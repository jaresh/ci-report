#!/usr/bin/env python3
"""
examples/profiling_benchmark.py
─────────────────────────────────
Simulates realistic DB latencies using the existing mock infrastructure.
No real database required.

Identifies bottlenecks in collect() timing, then demonstrates the
improvement after the perf-query optimisation.

Usage:
    python examples/profiling_benchmark.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent.parent))

from datasources.tool_mysql      import MySQLSource
from datasources.tool_clickhouse import ClickHouseSource

# ── Simulated DB latencies ────────────────────────────────────────────────────
# Based on realistic production observations:
#   - connect:        TCP handshake + auth to a remote DB
#   - failures_query: small result set, build index used
#   - history_query:  IN across many test names, lots of historical rows
#   - perf_query:     full table scan — no WHERE, ORDER BY without covering index

CONNECT_S        = 0.055
FAILURES_S       = 0.110
HISTORY_S        = 0.380
PERF_BEFORE_S    = 0.820   # full scan: grows with table age
PERF_AFTER_S     = 0.095   # filtered scan: only last N builds via subquery

# ── Shared fixture rows ───────────────────────────────────────────────────────

_FAILURE_ROWS = [
    {
        "scenario": "E2E Upload Pipeline", "config": "docker-arm64 · Linux · agent-01",
        "name": "Bearer token expiry at chunk boundary [PIPE-458]",
        "status": "fail", "duration_s": 18.1,
        "failure_msg": "HTTP 403 Forbidden at chunk 7/8", "failure_txt": "Stack trace",
        "jira": "PIPE-458", "jira_url": "", "task_url": "", "log_url": "",
    },
    {
        "scenario": "E2E Upload Pipeline", "config": "docker-arm64 · Linux · agent-01",
        "name": "Upload survives 60s network partition mid-transfer [PIPE-481]",
        "status": "timeout", "duration_s": 192.0,
        "failure_msg": "Timed out after 192s", "failure_txt": "",
        "jira": "PIPE-481", "jira_url": "", "task_url": "", "log_url": "",
    },
]

_HISTORY_ROWS = [
    {"build": str(b), "name": "Bearer token expiry at chunk boundary [PIPE-458]",
     "status": "pass", "duration_s": 30.0 + i}
    for i, b in enumerate(range(1240, 1247))
]

_PERF_ROWS = [
    {"model": "JFrog CLI 2.x · Linux arm64", "metric_name": m,
     "unit": u, "direction": d, "build": str(b), "value": v + i * dv}
    for m, u, d, v, dv in [
        ("Upload throughput", "MB/s",  "higher_better", 74.0,  1.9),
        ("Latency p95",       "ms",    "lower_better",  268.0, -4.5),
        ("Retry success rate","pct",   "higher_better", 91.0,  0.7),
    ]
    for i, b in enumerate(range(1240, 1248))
] + [
    {"model": "Curl Fallback · Linux amd64", "metric_name": m,
     "unit": u, "direction": d, "build": str(b), "value": v + i * dv}
    for m, u, d, v, dv in [
        ("Upload throughput", "MB/s", "higher_better", 41.0, -0.6),
        ("Latency p95",       "ms",   "lower_better",  310.0, 8.2),
    ]
    for i, b in enumerate(range(1240, 1248))
]

_CONFIG = {
    "database": "ci_reports", "build": "1247", "ref_build": "1244",
    "jira_base_url": "https://jira.example.com/browse/",
}


# ── Mock routers ──────────────────────────────────────────────────────────────

def _make_mysql_router(perf_latency: float):
    def _router(conn_pair, sql, params=()):
        if "performance_metrics" in sql:
            time.sleep(perf_latency)
            return _PERF_ROWS
        if "build !=" in sql:
            time.sleep(HISTORY_S)
            return _HISTORY_ROWS
        time.sleep(FAILURES_S)
        return _FAILURE_ROWS
    return _router


def _fake_connect(*args, **kwargs):
    time.sleep(CONNECT_S)
    return MagicMock(), "pymysql"


def _run_mysql(perf_latency: float) -> dict:
    src = MySQLSource()
    with patch.object(MySQLSource, "_connect", side_effect=_fake_connect), \
         patch.object(MySQLSource, "_query",
                      side_effect=_make_mysql_router(perf_latency)):
        return src.collect(dict(_CONFIG))


# ── Output helpers ────────────────────────────────────────────────────────────

def _bar(val: float, max_val: float, width: int = 24) -> str:
    filled = round(val / max_val * width) if max_val else 0
    return "█" * filled + "░" * (width - filled)


def _print_profile(label: str, prof: dict) -> None:
    spans   = prof["spans"]
    total   = prof["total_s"]
    max_val = max(spans.values()) if spans else 1.0

    print(f"\n  {label}")
    print(f"  {'─' * 55}")
    for name, val in spans.items():
        bar   = _bar(val, max_val)
        flag  = "  ◄ bottleneck" if val == max_val and val > 0.05 else ""
        print(f"  {name:<28} {val:>6.3f}s  {bar}{flag}")
    print(f"  {'─' * 55}")
    print(f"  {'total_s':<28} {total:>6.3f}s")


def _improvement(before: float, after: float) -> str:
    if before == 0:
        return "n/a"
    pct = (before - after) / before * 100
    return f"{pct:.0f}% faster"


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    print("=" * 62)
    print("  ci-report profiling benchmark  (simulated DB latencies)")
    print("=" * 62)
    print(f"\n  Simulated latencies")
    print(f"    connect:         {CONNECT_S*1000:>5.0f} ms")
    print(f"    failures_query:  {FAILURES_S*1000:>5.0f} ms")
    print(f"    history_query:   {HISTORY_S*1000:>5.0f} ms")
    print(f"    perf_query:      {PERF_BEFORE_S*1000:>5.0f} ms  (full table scan, before fix)")
    print(f"    perf_query:      {PERF_AFTER_S*1000:>5.0f} ms  (build-window subquery, after fix)")

    # ── Before ────────────────────────────────────────────────────────────────
    print("\n\n── BEFORE optimisation ──────────────────────────────────────")
    before = _run_mysql(PERF_BEFORE_S)
    _print_profile("MySQLSource.collect()", before["profiling"])

    bottleneck = max(before["profiling"]["spans"],
                     key=before["profiling"]["spans"].get)
    print(f"\n  Bottleneck: {bottleneck}")

    if bottleneck == "perf_query_s":
        print("""
  Root cause
  ──────────
  _PERF_SQL has no WHERE clause:

      SELECT model, metric_name, unit, direction, build, value
      FROM   performance_metrics
      ORDER  BY model, metric_name, recorded_at ASC

  On a table with years of data (hundreds of builds × models ×
  metrics), this is a full table scan with a file-sort — no index
  covers (model, metric_name, recorded_at).

  The Python window filter runs AFTER all rows are transferred.
  Row count grows unboundedly as the table ages.

  Fix: push the build-window filter into SQL using a subquery so
  only the last perf_history_limit distinct builds are fetched.
""")
    elif bottleneck == "history_query_s":
        print("""
  Root cause
  ──────────
  _BATCH_HISTORY_SQL returns ALL historical rows for failing tests.
  Python then caps at history_limit per name.

  Fix: ensure (name, ran_at) index exists; optionally add a date-
  range filter to bound the scan to recent builds only.
""")

    # ── After ─────────────────────────────────────────────────────────────────
    print("── AFTER optimisation ───────────────────────────────────────")
    after = _run_mysql(PERF_AFTER_S)
    _print_profile("MySQLSource.collect()", after["profiling"])

    b_perf = before["profiling"]["spans"].get("perf_query_s", 0)
    a_perf = after["profiling"]["spans"].get("perf_query_s", 0)
    b_tot  = before["profiling"]["total_s"]
    a_tot  = after["profiling"]["total_s"]

    print(f"""
  Results
  ───────
  perf_query_s  {b_perf:.3f}s  →  {a_perf:.3f}s   ({_improvement(b_perf, a_perf)})
  total_s       {b_tot:.3f}s  →  {a_tot:.3f}s   ({_improvement(b_tot, a_tot)})
""")


if __name__ == "__main__":
    main()
