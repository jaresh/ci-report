"""
tests/test_logs.py
──────────────────
Tests for the parallel log fetcher (datasources/logs.py):

  · LogFetcher._fetch_one  — HTTP success / failure / cache-skip / Range / auth
                             (mocked at urllib.request.urlopen)
  · LogFetcher.fetch_many  — parallel aggregation (mocked at _fetch_one)
  · _summarize / _stats    — the profiling.logs block maths
  · attach_logs            — sets tc["log_file"], skips placeholder URLs
  · tool integration       — fetch_logs opt-in records log_fetch_s + profiling.logs
  · merge_results          — carries the per-tool logs block

Network access is always mocked at the _fetch_one / urlopen boundary, mirroring
the "mock at the boundary, not the connection" rule used for the DB tools.

Run with:
    pytest tests/test_logs.py -v
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from datasources.logs import (
    LogFetcher, attach_logs, _summarize, _stats, _auth_header,
)
from datasources.tool_mysql import MySQLSource
from datasources.tool_clickhouse import ClickHouseSource
from datasources.base import merge_results


# ── Fixtures ─────────────────────────────────────────────────────────────────────

def _rec(key, ok=True, s=0.1, b=100, connect=0.02, transfer=0.08, cached=False, err=""):
    return {"key": key, "url": "u", "ok": ok, "path": f"/p/{key}.log", "bytes": b,
            "s": s, "connect_s": connect, "transfer_s": transfer,
            "cached": cached, "error": err}


_FAILURE_ROWS = [{
    "scenario": "Suite", "config": "env", "name": "t1",
    "status": "fail", "duration_s": 1.0,
    "failure_msg": "m", "failure_txt": "x",
    "jira": "", "jira_url": "", "task_url": "", "log_url": "",
}]

_FAKE_STATS = {
    "count": 1, "ok": 1, "failed": 0, "cached": 0, "workers": 1,
    "wall_s": 0.1, "sum_s": 0.1, "speedup": 1.0, "bytes_total": 10,
    "throughput_mb_s": 0.1, "connect_sum_s": 0.0, "transfer_sum_s": 0.0,
    "per_file_s": {}, "slowest": [],
}


def _mysql_router(conn_pair, sql, params=()):
    if "performance_metrics" in sql:
        return []
    if "build !=" in sql:
        return []
    return _FAILURE_ROWS


def _ch_router(client, driver, sql, params=None):
    if "performance_metrics" in sql:
        return []
    if "build !=" in sql:
        return []
    return _FAILURE_ROWS


# ── _stats ───────────────────────────────────────────────────────────────────────

class TestStats:

    def test_empty(self):
        s = _stats([])
        assert s == {"min": 0.0, "p50": 0.0, "p95": 0.0, "max": 0.0, "mean": 0.0}

    def test_single(self):
        s = _stats([0.3])
        assert s["min"] == s["max"] == s["p50"] == 0.3

    def test_basic(self):
        s = _stats([0.1, 0.2, 0.3, 0.4])
        assert s["min"] == 0.1
        assert s["max"] == 0.4
        assert s["mean"] == 0.25


# ── _summarize ───────────────────────────────────────────────────────────────────

class TestSummarize:

    def test_empty(self):
        st = _summarize([], 0.0, 0)
        assert st["count"] == 0
        assert st["speedup"] == 0.0
        assert st["slowest"] == []

    def test_counts(self):
        recs = [_rec("a"), _rec("b", ok=False), _rec("c", cached=True)]
        st = _summarize(recs, 1.0, 3)
        assert st["count"] == 3
        assert st["ok"] == 2
        assert st["failed"] == 1
        assert st["cached"] == 1

    def test_speedup_and_throughput(self):
        recs = [_rec("a", s=0.5, b=500), _rec("b", s=0.5, b=500)]
        st = _summarize(recs, 0.5, 2)
        assert st["sum_s"] == 1.0
        assert st["speedup"] == 2.0
        assert st["bytes_total"] == 1000
        assert st["throughput_mb_s"] == round(1000 / 1e6 / 0.5, 2)

    def test_cached_excluded_from_rates(self):
        recs = [_rec("a", s=0.4, b=400), _rec("b", cached=True, b=999, s=0.0)]
        st = _summarize(recs, 0.4, 2)
        assert st["bytes_total"] == 400        # cached file's bytes not counted
        assert st["sum_s"] == 0.4

    def test_connect_transfer_sums(self):
        recs = [_rec("a", connect=0.1, transfer=0.2), _rec("b", connect=0.3, transfer=0.4)]
        st = _summarize(recs, 1.0, 2)
        assert st["connect_sum_s"] == 0.4
        assert st["transfer_sum_s"] == 0.6

    def test_slowest_sorted_desc(self):
        recs = [_rec("a", s=0.1), _rec("b", s=0.9), _rec("c", s=0.5)]
        st = _summarize(recs, 1.0, 3)
        assert [x["key"] for x in st["slowest"]] == ["b", "c", "a"]


# ── LogFetcher._fetch_one (HTTP mocked) ──────────────────────────────────────────

class TestFetchOne:

    def test_success_writes_file(self, tmp_path):
        f = LogFetcher(str(tmp_path))
        resp = MagicMock()
        resp.read.return_value = b"hello"
        with patch("datasources.logs.urllib.request.urlopen", return_value=resp):
            rec = f._fetch_one(("t1", "http://x/1"))
        assert rec["ok"] is True
        assert rec["bytes"] == 5
        assert rec["cached"] is False
        assert (tmp_path / "t1.log").read_bytes() == b"hello"

    def test_failure_is_skipped_not_raised(self, tmp_path):
        f = LogFetcher(str(tmp_path))
        with patch("datasources.logs.urllib.request.urlopen", side_effect=OSError("boom")):
            rec = f._fetch_one(("t1", "http://x/1"))
        assert rec["ok"] is False
        assert "boom" in rec["error"]

    def test_cache_skip_when_present(self, tmp_path):
        (tmp_path / "t1.log").write_bytes(b"old")
        f = LogFetcher(str(tmp_path), skip_if_present=True)
        with patch("datasources.logs.urllib.request.urlopen") as uo:
            rec = f._fetch_one(("t1", "http://x/1"))
        assert rec["cached"] is True
        assert rec["ok"] is True
        uo.assert_not_called()

    def test_tail_bytes_sets_range_header(self, tmp_path):
        f = LogFetcher(str(tmp_path), tail_bytes=64)
        captured = {}

        def fake(req, timeout=None):
            captured["req"] = req
            resp = MagicMock()
            resp.read.return_value = b"x"
            return resp

        with patch("datasources.logs.urllib.request.urlopen", side_effect=fake):
            f._fetch_one(("t1", "http://x/1"))
        assert captured["req"].get_header("Range") == "bytes=-64"

    def test_auth_header_sent(self, tmp_path):
        f = LogFetcher(str(tmp_path), auth_header="Bearer X")
        captured = {}

        def fake(req, timeout=None):
            captured["req"] = req
            resp = MagicMock()
            resp.read.return_value = b"x"
            return resp

        with patch("datasources.logs.urllib.request.urlopen", side_effect=fake):
            f._fetch_one(("t1", "http://x/1"))
        assert captured["req"].get_header("Authorization") == "Bearer X"

    def test_max_bytes_caps_payload(self, tmp_path):
        f = LogFetcher(str(tmp_path), max_bytes=4)
        resp = MagicMock()
        resp.read.return_value = b"0123456789"
        with patch("datasources.logs.urllib.request.urlopen", return_value=resp):
            rec = f._fetch_one(("t1", "http://x/1"))
        assert rec["bytes"] == 4
        assert (tmp_path / "t1.log").read_bytes() == b"6789"   # last 4 bytes kept


# ── LogFetcher.fetch_many (aggregation, _fetch_one mocked) ────────────────────────

class TestFetchMany:

    def test_returns_paths_and_stats(self, tmp_path):
        f = LogFetcher(str(tmp_path), parallel=4)

        def fake_one(job):
            k, _ = job
            return _rec(k, b=100, s=0.1)

        with patch.object(LogFetcher, "_fetch_one", side_effect=fake_one):
            paths, stats = f.fetch_many([("a", "ua"), ("b", "ub")])
        assert set(paths) == {"a", "b"}
        assert stats["count"] == 2
        assert stats["ok"] == 2
        assert stats["bytes_total"] == 200

    def test_workers_capped_to_job_count(self, tmp_path):
        f = LogFetcher(str(tmp_path), parallel=8)
        with patch.object(LogFetcher, "_fetch_one", side_effect=lambda job: _rec(job[0])):
            _, stats = f.fetch_many([("a", "ua"), ("b", "ub")])
        assert stats["workers"] == 2

    def test_empty_jobs(self, tmp_path):
        paths, stats = LogFetcher(str(tmp_path)).fetch_many([])
        assert paths == {}
        assert stats["count"] == 0
        assert stats["workers"] == 0


# ── _auth_header ─────────────────────────────────────────────────────────────────

class TestAuthHeader:

    def test_from_env(self, monkeypatch):
        monkeypatch.setenv("LOG_TOKEN", "abc")
        assert _auth_header({"log_auth_token_env": "LOG_TOKEN"}) == "Bearer abc"

    def test_no_env_key(self):
        assert _auth_header({}) == ""

    def test_env_unset(self, monkeypatch):
        monkeypatch.delenv("MISSING_TOK", raising=False)
        assert _auth_header({"log_auth_token_env": "MISSING_TOK"}) == ""


# ── attach_logs ──────────────────────────────────────────────────────────────────

class TestAttachLogs:

    def test_sets_log_file(self, tmp_path):
        failures = [{"test_cases": [{"name": "t1", "log_url": "http://x/1"}]}]
        with patch.object(LogFetcher, "fetch_many",
                          return_value=({"t1": "/p/t1.log"}, _summarize([], 0.0, 0))):
            stats = attach_logs(failures, {"log_dest_dir": str(tmp_path)})
        assert failures[0]["test_cases"][0]["log_file"] == "/p/t1.log"
        assert "count" in stats

    def test_skips_placeholder_and_missing_urls(self, tmp_path):
        failures = [{"test_cases": [{"name": "t1", "log_url": "#"}, {"name": "t2"}]}]
        captured = {}

        def fake_many(jobs):
            captured["jobs"] = list(jobs)
            return {}, _summarize([], 0.0, 0)

        with patch.object(LogFetcher, "fetch_many", side_effect=fake_many):
            attach_logs(failures, {"log_dest_dir": str(tmp_path)})
        assert captured["jobs"] == []


# ── Tool integration (fetch_logs opt-in) ─────────────────────────────────────────

class TestToolLogIntegration:

    def test_mysql_fetch_logs_records_profiling(self):
        def fake_attach(failures, config):
            for s in failures:
                for tc in s["test_cases"]:
                    tc["log_file"] = "/local/x.log"
            return _FAKE_STATS

        src = MySQLSource()
        with patch.object(MySQLSource, "_connect", return_value=(MagicMock(), "pymysql")), \
             patch.object(MySQLSource, "_query", side_effect=_mysql_router), \
             patch("datasources.logs.attach_logs", side_effect=fake_attach):
            out = src.collect({"database": "ci", "build": "1", "fetch_logs": True})

        assert "log_fetch_s" in out["profiling"]["spans"]
        assert out["profiling"]["logs"] == _FAKE_STATS
        assert out["failures"][0]["test_cases"][0]["log_file"] == "/local/x.log"

    def test_mysql_without_fetch_logs_has_no_logs_block(self):
        src = MySQLSource()
        with patch.object(MySQLSource, "_connect", return_value=(MagicMock(), "pymysql")), \
             patch.object(MySQLSource, "_query", side_effect=_mysql_router):
            out = src.collect({"database": "ci", "build": "1"})

        assert "log_fetch_s" not in out["profiling"]["spans"]
        assert "logs" not in out["profiling"]

    def test_clickhouse_fetch_logs_records_profiling(self):
        src = ClickHouseSource()
        with patch.object(ClickHouseSource, "_connect", return_value=(MagicMock(), "driver")), \
             patch.object(ClickHouseSource, "_execute", side_effect=_ch_router), \
             patch("datasources.logs.attach_logs", return_value=_FAKE_STATS):
            out = src.collect({"database": "ci", "build": "1", "fetch_logs": True})

        assert "log_fetch_s" in out["profiling"]["spans"]
        assert out["profiling"]["logs"] == _FAKE_STATS


# ── merge_results carries the logs block ─────────────────────────────────────────

class TestMergeCarriesLogs:

    def test_logs_block_preserved_per_tool(self):
        tool_out = {
            "failures": [], "performance": [],
            "profiling": {"tool": "mysql", "total_s": 1.0, "spans": {}, "logs": _FAKE_STATS},
        }
        merged = merge_results({"build": {}}, [tool_out])
        assert merged["profiling"]["tools"]["mysql"]["logs"] == _FAKE_STATS

    def test_no_logs_block_when_absent(self):
        tool_out = {
            "failures": [], "performance": [],
            "profiling": {"tool": "mysql", "total_s": 1.0, "spans": {}},
        }
        merged = merge_results({"build": {}}, [tool_out])
        assert "logs" not in merged["profiling"]["tools"]["mysql"]
