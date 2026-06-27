"""
datasources/logs.py
───────────────────
Parallel HTTP log fetcher for data source tools.

A data source that downloads per-test-case log files calls `attach_logs()`
during collect(). Logs are fetched concurrently over HTTP, written to a local
directory, and each test case gets a `log_file` (local path) that the AI phase
reads and excerpts — while `log_url` stays as the human-facing link.

Detailed download profiling
───────────────────────────
`LogFetcher.fetch_many()` returns a `LogProfiling` block (see datasources/base.py)
so agents can assess the fastest way to pull logs and which optimisation to
apply. It records, per run:

    wall_s / sum_s / speedup   parallel efficiency  (speedup = sum_s / wall_s)
    connect_sum_s / transfer_sum_s   where the time goes (TLS vs body read)
    bytes_total / throughput_mb_s    bandwidth- vs latency-bound
    per_file_s (min/p50/p95/max/mean) and slowest[]   tail latency

Each worker returns its own per-file record, so aggregation is lock-free.

Config keys (read by attach_logs, under the tool's config section)
──────────────────────────────────────────────────────────────────
fetch_logs            bool  master switch — read by the TOOL, not here  [default: false]
log_dest_dir          str   local directory for downloaded logs         [default: "logs/"]
log_parallel_requests int   concurrent downloads                        [default: 8]
log_timeout           int   per-request HTTP timeout (s)                [default: 15]
log_tail_bytes        int   fetch only the last N bytes via Range       [default: 0 = full]
log_max_bytes         int   client-side cap if the server ignores Range [default: 0 = none]
log_skip_if_present   bool  skip download if the local file exists       [default: true]
log_auth_token_env    str   env var holding a bearer token              [optional]

Never raises: a failed download is logged and skipped; that test case simply
has no `log_file`. All network access is isolated in `_fetch_one` so tests can
mock it at that boundary.
"""

from __future__ import annotations

import logging
import os
import re
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .base import LogProfiling

log = logging.getLogger(__name__)

_SAFE_RE = re.compile(r"[^\w\-]")


def _safe(name: str) -> str:
    return _SAFE_RE.sub("_", name)[:80]


# ── Aggregation ─────────────────────────────────────────────────────────────────

def _stats(sorted_vals: list[float]) -> dict[str, float]:
    """min / p50 / p95 / max / mean of an already-sorted list (nearest-rank)."""
    if not sorted_vals:
        return {"min": 0.0, "p50": 0.0, "p95": 0.0, "max": 0.0, "mean": 0.0}
    n = len(sorted_vals)

    def pct(p: float) -> float:
        if n == 1:
            return sorted_vals[0]
        return sorted_vals[min(n - 1, int(round((p / 100) * (n - 1))))]

    return {
        "min":  round(sorted_vals[0], 3),
        "p50":  round(pct(50), 3),
        "p95":  round(pct(95), 3),
        "max":  round(sorted_vals[-1], 3),
        "mean": round(sum(sorted_vals) / n, 3),
    }


def _summarize(records: list[dict], wall: float, workers: int) -> LogProfiling:
    """Fold per-file records into the LogProfiling block.

    Rate/size/percentile figures are computed over actually-downloaded files
    (ok and not cached) so they reflect the real transfer, not skipped work.
    """
    wall = round(wall, 3)
    dl   = [r for r in records if r["ok"] and not r["cached"]]
    durs = sorted(r["s"] for r in dl)
    sum_s       = round(sum(r["s"] for r in dl), 3)
    bytes_total = sum(r["bytes"] for r in dl)

    return {
        "count":          len(records),
        "ok":             sum(1 for r in records if r["ok"]),
        "failed":         sum(1 for r in records if not r["ok"]),
        "cached":         sum(1 for r in records if r["cached"]),
        "workers":        workers,
        "wall_s":         wall,
        "sum_s":          sum_s,
        "speedup":        round(sum_s / wall, 2) if wall > 0 else 0.0,
        "bytes_total":    bytes_total,
        "throughput_mb_s": round(bytes_total / 1e6 / wall, 2) if wall > 0 else 0.0,
        "connect_sum_s":  round(sum(r["connect_s"] for r in dl), 3),
        "transfer_sum_s": round(sum(r["transfer_s"] for r in dl), 3),
        "per_file_s":     _stats(durs),
        "slowest": [
            {"key": r["key"], "s": r["s"], "bytes": r["bytes"]}
            for r in sorted(dl, key=lambda r: r["s"], reverse=True)[:5]
        ],
    }


# ── Fetcher ─────────────────────────────────────────────────────────────────────

class LogFetcher:
    """Downloads many log URLs in parallel and records detailed timing."""

    def __init__(self, dest_dir: str, *, parallel: int = 8, timeout: int = 15,
                 auth_header: str = "", tail_bytes: int = 0, max_bytes: int = 0,
                 skip_if_present: bool = True) -> None:
        self.dest            = Path(dest_dir)
        self.parallel        = max(1, parallel)
        self.timeout         = timeout
        self.auth_header     = auth_header
        self.tail_bytes      = tail_bytes
        self.max_bytes       = max_bytes
        self.skip_if_present = skip_if_present

    def fetch_many(self, jobs: list[tuple[str, str]]) -> tuple[dict[str, str], LogProfiling]:
        """jobs = [(key, url), ...] → ({key: local_path}, profiling)."""
        jobs = list(jobs)
        if not jobs:
            return {}, _summarize([], 0.0, 0)

        self.dest.mkdir(parents=True, exist_ok=True)
        workers = max(1, min(self.parallel, len(jobs)))

        t0 = time.perf_counter()
        with ThreadPoolExecutor(max_workers=workers) as pool:
            records = list(pool.map(self._fetch_one, jobs))
        wall = time.perf_counter() - t0

        paths = {r["key"]: r["path"] for r in records if r["ok"]}
        return paths, _summarize(records, wall, workers)

    def _fetch_one(self, job: tuple[str, str]) -> dict:
        key, url = job
        rec = {"key": key, "url": url, "ok": False, "path": "", "bytes": 0,
               "s": 0.0, "connect_s": 0.0, "transfer_s": 0.0,
               "cached": False, "error": ""}

        dest = self.dest / f"{_safe(key)}.log"
        if self.skip_if_present and dest.exists():
            rec.update(ok=True, path=str(dest), cached=True)
            return rec

        headers: dict[str, str] = {}
        if self.auth_header:
            headers["Authorization"] = self.auth_header
        if self.tail_bytes > 0:
            headers["Range"] = f"bytes=-{self.tail_bytes}"

        t0 = time.perf_counter()
        try:
            req  = urllib.request.Request(url, headers=headers)
            resp = urllib.request.urlopen(req, timeout=self.timeout)
            try:
                t1   = time.perf_counter()
                data = resp.read()
            finally:
                resp.close()
            if self.max_bytes and len(data) > self.max_bytes:
                data = data[-self.max_bytes:]
            t2 = time.perf_counter()
            dest.write_bytes(data)
            rec.update(ok=True, path=str(dest), bytes=len(data),
                       connect_s=round(t1 - t0, 3),
                       transfer_s=round(t2 - t1, 3),
                       s=round(t2 - t0, 3))
        except Exception as exc:
            rec.update(error=str(exc), s=round(time.perf_counter() - t0, 3))
            log.warning("log fetch failed for %s: %s", key, exc)
        return rec


# ── Tool-facing convenience ──────────────────────────────────────────────────────

def _auth_header(config: dict) -> str:
    env = config.get("log_auth_token_env", "")
    if not env:
        return ""
    token = os.environ.get(env, "")
    return f"Bearer {token}" if token else ""


def attach_logs(failures: list, config: dict) -> LogProfiling:
    """Fetch the log for every failing test case and set tc["log_file"].

    Reads the standard log_* keys from `config`, downloads in parallel, mutates
    `failures` in place, and returns the LogProfiling block for the tool to put
    under profiling["logs"]. Test cases whose log_url is missing or "#" are
    skipped.
    """
    jobs = [
        (tc["name"], tc["log_url"])
        for scenario in failures
        for tc in scenario.get("test_cases", [])
        if tc.get("log_url") and tc["log_url"] != "#"
    ]

    fetcher = LogFetcher(
        config.get("log_dest_dir", "logs/"),
        parallel=int(config.get("log_parallel_requests", 8)),
        timeout=int(config.get("log_timeout", 15)),
        auth_header=_auth_header(config),
        tail_bytes=int(config.get("log_tail_bytes", 0)),
        max_bytes=int(config.get("log_max_bytes", 0)),
        skip_if_present=bool(config.get("log_skip_if_present", True)),
    )
    paths, stats = fetcher.fetch_many(jobs)

    for scenario in failures:
        for tc in scenario.get("test_cases", []):
            path = paths.get(tc["name"])
            if path:
                tc["log_file"] = path

    return stats
