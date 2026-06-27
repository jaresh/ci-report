"""
tests/test_tools.py
───────────────────
Tests for datasource helpers, the JSON output contract produced by
collect(), the merge layer, and the prepare_data enrichment pipeline.

The JSON output contract is the critical invariant tested here: every
dict emitted by collect() must carry exactly the keys the HTML template
depends on, in the expected types, so schema regressions are caught
before they reach the renderer.

Run with:
    pytest tests/
    pytest tests/ -v --tb=short
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from datasources.tool_mysql import MySQLSource
from datasources.contract import (
    _fmt_dur, _sort_builds, _group_history, _history_list, _build_performance,
)
from datasources.tool_clickhouse import ClickHouseSource
from datasources.base import merge_results
from generate_report import prepare_data


# ═══════════════════════════════════════════════════════════════════════════════
# Shared helpers  (identical implementations in tool_mysql and tool_clickhouse)
# ═══════════════════════════════════════════════════════════════════════════════

class TestFmtDur:

    def test_zero(self):
        assert _fmt_dur(0) == "—"

    def test_none(self):
        assert _fmt_dur(None) == "—"

    def test_negative(self):
        assert _fmt_dur(-1) == "—"

    def test_sub_minute(self):
        assert _fmt_dur(18.1) == "18.1s"

    def test_exact_one_minute(self):
        assert _fmt_dur(60) == "1m 0s"

    def test_over_one_minute(self):
        assert _fmt_dur(192) == "3m 12s"

    def test_string_numeric_input(self):
        assert _fmt_dur("18.5") == "18.5s"

    def test_invalid_string(self):
        assert _fmt_dur("n/a") == "—"

    def test_integer_input(self):
        result = _fmt_dur(30)
        assert result == "30.0s"


class TestSortBuilds:

    def test_numeric_order(self):
        assert _sort_builds(["1242", "1240", "1247"]) == ["1240", "1242", "1247"]

    def test_already_sorted(self):
        assert _sort_builds(["1240", "1241", "1242"]) == ["1240", "1241", "1242"]

    def test_single_element(self):
        assert _sort_builds(["1247"]) == ["1247"]

    def test_empty(self):
        assert _sort_builds([]) == []

    def test_falls_back_to_string_sort_for_non_numeric(self):
        result = _sort_builds(["c", "a", "b"])
        assert result == ["a", "b", "c"]


class TestGroupHistory:

    def _row(self, name, build, status="pass"):
        return {"name": name, "build": build, "status": status, "duration_s": 10.0}

    def test_groups_by_name(self):
        rows = [
            self._row("test_a", "1245"),
            self._row("test_b", "1245"),
            self._row("test_a", "1244"),
        ]
        result = _group_history(rows, limit=10)
        assert len(result["test_a"]) == 2
        assert len(result["test_b"]) == 1

    def test_limit_caps_entries(self):
        rows = [self._row("test_a", str(1240 + i)) for i in range(10)]
        result = _group_history(rows, limit=3)
        assert len(result["test_a"]) == 3

    def test_limit_keeps_first_rows(self):
        # rows arrive DESC (most recent first); limit keeps the top N
        rows = [self._row("test_a", "1246"), self._row("test_a", "1245")]
        result = _group_history(rows, limit=1)
        assert len(result["test_a"]) == 1
        assert result["test_a"][0]["build"] == "1246"

    def test_empty_rows(self):
        assert _group_history([], limit=7) == {}

    def test_multiple_names_independent_limits(self):
        rows = [self._row("a", str(i)) for i in range(5)] + \
               [self._row("b", str(i)) for i in range(5)]
        result = _group_history(rows, limit=3)
        assert len(result["a"]) == 3
        assert len(result["b"]) == 3


class TestHistoryList:

    def _hist_rows(self, builds_statuses):
        return [
            {"build": b, "status": s, "duration_s": 10.0}
            for b, s in builds_statuses
        ]

    def test_current_entry_always_last(self):
        hist = self._hist_rows([("1245", "pass"), ("1246", "pass")])
        result = _history_list(hist, "fail", "18.1s")
        assert result[-1]["build"] == "current"
        assert result[-1].get("current") is True

    def test_current_status_and_duration_correct(self):
        hist = self._hist_rows([("1246", "pass")])
        result = _history_list(hist, "fail", "30.4s")
        cur = result[-1]
        assert cur["status"] == "fail"
        assert cur["duration"] == "30.4s"

    def test_chronological_order(self):
        # hist_entries arrive DESC; _history_list reverses to ASC before appending current
        hist = self._hist_rows([("1246", "pass"), ("1245", "pass"), ("1244", "fail")])
        result = _history_list(hist, "fail", "18s")
        assert [r["build"] for r in result] == ["1244", "1245", "1246", "current"]

    def test_empty_prior_history(self):
        result = _history_list([], "fail", "18s")
        assert len(result) == 1
        assert result[0]["build"] == "current"

    def test_each_entry_has_required_keys(self):
        hist = self._hist_rows([("1246", "pass")])
        for entry in _history_list(hist, "fail", "18s"):
            assert "build" in entry
            assert "status" in entry
            assert "duration" in entry


# ═══════════════════════════════════════════════════════════════════════════════
# _build_performance — core performance JSON builder
# ═══════════════════════════════════════════════════════════════════════════════

BUILDS_8 = ["1240", "1241", "1242", "1243", "1244", "1245", "1246", "1247"]


def _perf_rows(builds=None, model="arm64", metric="Throughput",
               unit="MB/s", direction="higher_better", base=80.0):
    builds = builds or BUILDS_8
    return [
        {
            "model": model, "metric_name": metric, "unit": unit,
            "direction": direction, "build": b, "value": base + i,
        }
        for i, b in enumerate(builds)
    ]


class TestBuildPerformance:

    # ── Output shape ─────────────────────────────────────────────────────────

    def test_returns_list(self):
        assert isinstance(_build_performance(_perf_rows(), "1247", "1244", 8), list)

    def test_model_top_level_keys(self):
        model = _build_performance(_perf_rows(), "1247", "1244", 8)[0]
        for key in ("model", "summary_note", "summary_chips", "metrics"):
            assert key in model, f"missing model key: {key}"

    def test_metric_required_keys(self):
        metric = _build_performance(_perf_rows(), "1247", "1244", 8)[0]["metrics"][0]
        for key in ("name", "unit", "direction", "current", "reference",
                    "history_values", "history_builds"):
            assert key in metric, f"missing metric key: {key}"

    def test_history_values_and_builds_same_length(self):
        metric = _build_performance(_perf_rows(), "1247", "1244", 8)[0]["metrics"][0]
        assert len(metric["history_values"]) == len(metric["history_builds"])

    def test_direction_is_valid(self):
        metric = _build_performance(_perf_rows(), "1247", "1244", 8)[0]["metrics"][0]
        assert metric["direction"] in {"higher_better", "lower_better"}

    def test_current_and_reference_are_numeric(self):
        metric = _build_performance(_perf_rows(), "1247", "1244", 8)[0]["metrics"][0]
        assert isinstance(metric["current"],   (int, float))
        assert isinstance(metric["reference"], (int, float))

    # ── Build window ─────────────────────────────────────────────────────────

    def test_window_limits_history_length(self):
        metric = _build_performance(_perf_rows(), "1247", "1244", limit=4)[0]["metrics"][0]
        assert len(metric["history_builds"]) == 4
        assert metric["history_builds"] == ["1244", "1245", "1246", "1247"]

    def test_history_builds_are_sorted_ascending(self):
        metric = _build_performance(_perf_rows(), "1247", "1244", 8)[0]["metrics"][0]
        builds = metric["history_builds"]
        assert builds == sorted(builds, key=int)

    def test_history_values_correspond_to_builds(self):
        rows = _perf_rows(["1240", "1247"], base=80.0)
        # 1240 → 80.0, 1247 → 81.0
        metric = _build_performance(rows, "1247", "1240", 2)[0]["metrics"][0]
        build_to_val = dict(zip(metric["history_builds"], metric["history_values"]))
        assert build_to_val["1240"] == pytest.approx(80.0)
        assert build_to_val["1247"] == pytest.approx(81.0)

    # ── Current and reference values ─────────────────────────────────────────

    def test_current_build_value_selected(self):
        rows = _perf_rows(["1246", "1247"], base=80.0)
        metric = _build_performance(rows, "1247", "1246", 2)[0]["metrics"][0]
        assert metric["current"] == pytest.approx(81.0)

    def test_ref_build_value_selected(self):
        rows = _perf_rows(["1244", "1247"], base=80.0)
        metric = _build_performance(rows, "1247", "1244", 2)[0]["metrics"][0]
        assert metric["reference"] == pytest.approx(80.0)

    def test_missing_ref_build_falls_back_to_current(self):
        rows = _perf_rows(["1247"], base=88.0)
        metric = _build_performance(rows, "1247", "9999", 8)[0]["metrics"][0]
        assert metric["reference"] == pytest.approx(metric["current"])

    def test_missing_current_build_falls_back_to_last_history_value(self):
        rows = _perf_rows(["1245", "1246"], base=80.0)
        metric = _build_performance(rows, "9999", "1246", 8)[0]["metrics"][0]
        assert metric["current"] == pytest.approx(81.0)

    # ── Model discovery ───────────────────────────────────────────────────────

    def test_multiple_models_discovered(self):
        rows = _perf_rows(model="arm64") + _perf_rows(model="amd64", base=70.0)
        model_names = [m["model"] for m in _build_performance(rows, "1247", "1244", 8)]
        assert "arm64" in model_names
        assert "amd64" in model_names

    def test_model_order_preserved(self):
        rows = _perf_rows(model="arm64") + _perf_rows(model="amd64", base=70.0)
        result = _build_performance(rows, "1247", "1244", 8)
        assert result[0]["model"] == "arm64"
        assert result[1]["model"] == "amd64"

    def test_multiple_metrics_per_model(self):
        rows = _perf_rows(metric="Throughput") + \
               _perf_rows(metric="Latency", direction="lower_better", base=200.0)
        result = _build_performance(rows, "1247", "1244", 8)
        assert len(result[0]["metrics"]) == 2

    # ── Edge cases ────────────────────────────────────────────────────────────

    def test_empty_rows_returns_empty_list(self):
        assert _build_performance([], "1247", "1244", 8) == []

    def test_single_build(self):
        rows = _perf_rows(["1247"])
        result = _build_performance(rows, "1247", "1244", 8)
        assert len(result) == 1
        assert len(result[0]["metrics"][0]["history_builds"]) == 1


# ═══════════════════════════════════════════════════════════════════════════════
# merge_results
# ═══════════════════════════════════════════════════════════════════════════════

class TestMergeResults:

    BASE = {
        "build": {
            "number": "1247",
            "branch": "main",
            "badges": {"passed": 34, "skipped": 2},
        },
    }

    def _scenario(self, name="S1", n_cases=1):
        return {
            "scenario": name,
            "config": "linux",
            "jira": "",
            "jira_url": "#",
            "test_cases": [
                {"name": f"tc_{i}", "status": "fail"} for i in range(n_cases)
            ],
        }

    def _model(self, name="arm64"):
        return {
            "model": name, "summary_note": "", "summary_chips": None,
            "metrics": [{
                "name": "Throughput", "unit": "MB/s",
                "direction": "higher_better", "current": 80.0,
                "reference": 78.0, "history_values": [80.0], "history_builds": ["1247"],
            }],
        }

    def test_result_has_all_top_level_keys(self):
        result = merge_results(self.BASE, [])
        assert "build" in result
        assert "failures" in result
        assert "performance" in result

    def test_failures_from_multiple_tools_concatenated(self):
        t1 = {"failures": [self._scenario("S1")]}
        t2 = {"failures": [self._scenario("S2")]}
        result = merge_results(self.BASE, [t1, t2])
        names = [s["scenario"] for s in result["failures"]]
        assert "S1" in names and "S2" in names

    def test_performance_from_multiple_tools_concatenated(self):
        t1 = {"performance": [self._model("arm64")]}
        t2 = {"performance": [self._model("amd64")]}
        result = merge_results(self.BASE, [t1, t2])
        assert len(result["performance"]) == 2

    def test_failed_badge_recomputed_from_test_cases(self):
        tool = {"failures": [self._scenario("S1", n_cases=3)]}
        result = merge_results(self.BASE, [tool])
        assert result["build"]["badges"]["failed"] == 3

    def test_failed_badge_zero_with_no_failures(self):
        result = merge_results(self.BASE, [])
        assert result["build"]["badges"]["failed"] == 0

    def test_failed_badge_sums_across_tools(self):
        t1 = {"failures": [self._scenario("S1", n_cases=2)]}
        t2 = {"failures": [self._scenario("S2", n_cases=3)]}
        result = merge_results(self.BASE, [t1, t2])
        assert result["build"]["badges"]["failed"] == 5

    def test_empty_and_none_tool_outputs_skipped(self):
        valid = {"failures": [self._scenario()]}
        result = merge_results(self.BASE, [{}, None, valid])
        assert len(result["failures"]) == 1

    def test_tool_build_fields_merged(self):
        tool = {"build": {"commit": "abc1234"}}
        result = merge_results(self.BASE, [tool])
        assert result["build"]["commit"] == "abc1234"

    def test_base_fields_preserved(self):
        result = merge_results(self.BASE, [])
        assert result["build"]["number"] == "1247"
        assert result["build"]["branch"] == "main"

    def test_base_badges_preserved_when_no_tool_badges(self):
        result = merge_results(self.BASE, [])
        assert result["build"]["badges"]["passed"] == 34

    def test_tool_badges_merged_into_base(self):
        tool = {"build": {"badges": {"metrics": 12}}}
        result = merge_results(self.BASE, [tool])
        assert result["build"]["badges"]["metrics"] == 12
        assert result["build"]["badges"]["passed"] == 34   # base preserved

    def test_no_tools_returns_empty_lists(self):
        result = merge_results(self.BASE, [])
        assert result["failures"] == []
        assert result["performance"] == []


# ═══════════════════════════════════════════════════════════════════════════════
# Shared fixture rows — used by both MySQL and ClickHouse contract tests
# ═══════════════════════════════════════════════════════════════════════════════

_FAILURE_ROWS = [
    {
        "scenario": "E2E Upload", "config": "docker-arm64",
        "name": "test_token_expiry", "status": "fail", "duration_s": 18.1,
        "failure_msg": "HTTP 403", "failure_txt": "Stack trace line 1\nline 2",
        "jira": "PIPE-458", "jira_url": "", "task_url": "", "log_url": "",
    },
    {
        "scenario": "E2E Upload", "config": "docker-arm64",
        "name": "test_network_retry", "status": "timeout", "duration_s": 192.0,
        "failure_msg": "Timeout after 3m", "failure_txt": "",
        "jira": "", "jira_url": "", "task_url": "", "log_url": "",
    },
]

_HISTORY_ROWS = [
    {"build": "1246", "name": "test_token_expiry", "status": "pass", "duration_s": 17.5},
    {"build": "1245", "name": "test_token_expiry", "status": "pass", "duration_s": 16.8},
]

_PERF_ROWS = [
    {"model": "arm64", "metric_name": "Throughput", "unit": "MB/s",
     "direction": "higher_better", "build": "1240", "value": 80.0},
    {"model": "arm64", "metric_name": "Throughput", "unit": "MB/s",
     "direction": "higher_better", "build": "1247", "value": 87.3},
    {"model": "arm64", "metric_name": "Latency p95", "unit": "ms",
     "direction": "lower_better", "build": "1240", "value": 268.0},
    {"model": "arm64", "metric_name": "Latency p95", "unit": "ms",
     "direction": "lower_better", "build": "1247", "value": 234.0},
]


def _mysql_query_router(conn_pair, sql, params=()):
    if "performance_metrics" in sql:
        return _PERF_ROWS
    if "build !=" in sql:
        return _HISTORY_ROWS
    return _FAILURE_ROWS


# ═══════════════════════════════════════════════════════════════════════════════
# MySQLSource — collect() output contract (mocked DB)
# ═══════════════════════════════════════════════════════════════════════════════

class TestMySQLSourceContract:
    """
    Verify that MySQLSource.collect() always produces the JSON shape
    the HTML template depends on.  The DB is mocked at the _query level
    so no real MySQL server is required.
    """

    _CONFIG = {
        "database": "ci_reports", "build": "1247", "ref_build": "1244",
        "jira_base_url": "https://jira.example.com/browse/",
        "task_base_url": "https://ci.example.com/jobs/1247/tasks/",
        "log_base_url":  "https://ci.example.com/jobs/1247/logs/",
    }

    def _collect(self, extra_config=None):
        cfg = dict(self._CONFIG)
        if extra_config:
            cfg.update(extra_config)
        src = MySQLSource()
        with patch.object(MySQLSource, "_connect", return_value=(MagicMock(), "pymysql")), \
             patch.object(MySQLSource, "_query", side_effect=_mysql_query_router):
            return src.collect(cfg)

    # ── Top-level contract ────────────────────────────────────────────────────

    def test_always_returns_failures_and_performance_keys(self):
        result = self._collect()
        assert "failures" in result
        assert "performance" in result

    def test_failures_is_list(self):
        assert isinstance(self._collect()["failures"], list)

    def test_performance_is_list(self):
        assert isinstance(self._collect()["performance"], list)

    # ── Failure scenario shape ────────────────────────────────────────────────

    def test_scenario_required_keys(self):
        scenario = self._collect()["failures"][0]
        for key in ("scenario", "config", "jira", "jira_url", "test_cases"):
            assert key in scenario, f"scenario missing key: {key}"

    def test_scenario_name_is_string(self):
        assert isinstance(self._collect()["failures"][0]["scenario"], str)

    def test_test_cases_is_list(self):
        assert isinstance(self._collect()["failures"][0]["test_cases"], list)

    def test_both_failure_rows_present_in_one_scenario(self):
        # Both fixture rows share the same (scenario, config) → 1 scenario, 2 test cases
        result = self._collect()
        assert len(result["failures"]) == 1
        assert len(result["failures"][0]["test_cases"]) == 2

    # ── Test case shape ───────────────────────────────────────────────────────

    def test_test_case_required_keys(self):
        tc = self._collect()["failures"][0]["test_cases"][0]
        for key in ("name", "status", "duration", "failure_message", "failure_text",
                    "jira", "jira_url", "task_url", "log_url", "history", "ai_analysis"):
            assert key in tc, f"test case missing key: {key}"

    def test_test_case_status_is_valid(self):
        valid = {"fail", "error", "timeout"}
        for scenario in self._collect()["failures"]:
            for tc in scenario["test_cases"]:
                assert tc["status"] in valid

    def test_duration_is_string(self):
        tc = self._collect()["failures"][0]["test_cases"][0]
        assert isinstance(tc["duration"], str)

    def test_ai_analysis_is_dict(self):
        for scenario in self._collect()["failures"]:
            for tc in scenario["test_cases"]:
                assert isinstance(tc["ai_analysis"], dict)

    # ── History shape ─────────────────────────────────────────────────────────

    def test_history_is_list(self):
        tc = self._collect()["failures"][0]["test_cases"][0]
        assert isinstance(tc["history"], list)

    def test_history_last_entry_is_current_build(self):
        tc = self._collect()["failures"][0]["test_cases"][0]
        last = tc["history"][-1]
        assert last.get("current") is True
        assert last["build"] == "current"

    def test_history_entries_have_required_keys(self):
        tc = self._collect()["failures"][0]["test_cases"][0]
        for entry in tc["history"]:
            assert "build"    in entry
            assert "status"   in entry
            assert "duration" in entry

    def test_history_respects_limit(self):
        tc = self._collect({"history_limit": 1})["failures"][0]["test_cases"][0]
        # 1 prior + 1 current
        assert len(tc["history"]) <= 2

    # ── URL fallback ──────────────────────────────────────────────────────────

    def test_jira_url_built_from_base_when_db_url_empty(self):
        tc = self._collect()["failures"][0]["test_cases"][0]
        # jira = "PIPE-458", jira_url stored as "" → should construct from base
        assert "PIPE-458" in tc["jira_url"] or tc["jira_url"] == "#"

    def test_test_case_with_no_jira_gets_hash_url(self):
        # second fixture row has jira="" and jira_url=""
        tc = self._collect()["failures"][0]["test_cases"][1]
        assert tc["jira"] == ""
        assert tc["jira_url"] == "#"

    # ── Performance model shape ───────────────────────────────────────────────

    def test_performance_model_required_keys(self):
        model = self._collect()["performance"][0]
        for key in ("model", "summary_note", "summary_chips", "metrics"):
            assert key in model, f"model missing key: {key}"

    def test_model_name_is_string(self):
        assert isinstance(self._collect()["performance"][0]["model"], str)

    def test_metrics_is_list(self):
        assert isinstance(self._collect()["performance"][0]["metrics"], list)

    # ── Metric shape ──────────────────────────────────────────────────────────

    def test_metric_required_keys(self):
        metric = self._collect()["performance"][0]["metrics"][0]
        for key in ("name", "unit", "direction", "current", "reference",
                    "history_values", "history_builds"):
            assert key in metric, f"metric missing key: {key}"

    def test_metric_direction_is_valid(self):
        valid = {"higher_better", "lower_better"}
        for model in self._collect()["performance"]:
            for m in model["metrics"]:
                assert m["direction"] in valid

    def test_history_values_and_builds_same_length(self):
        for model in self._collect()["performance"]:
            for m in model["metrics"]:
                assert len(m["history_values"]) == len(m["history_builds"])

    def test_current_and_reference_are_numeric(self):
        for model in self._collect()["performance"]:
            for m in model["metrics"]:
                assert isinstance(m["current"],   (int, float))
                assert isinstance(m["reference"], (int, float))

    def test_two_metrics_from_fixture_rows(self):
        # _PERF_ROWS has Throughput + Latency p95
        assert len(self._collect()["performance"][0]["metrics"]) == 2

    # ── Guard rails ───────────────────────────────────────────────────────────

    def test_missing_build_returns_empty(self):
        src = MySQLSource()
        with patch.object(MySQLSource, "_connect", return_value=(MagicMock(), "pymysql")):
            result = src.collect({"database": "ci", "build": ""})
        assert result == {}

    def test_missing_database_returns_empty(self):
        src = MySQLSource()
        with patch.object(MySQLSource, "_connect", return_value=(MagicMock(), "pymysql")):
            result = src.collect({"build": "1247"})
        assert result == {}

    def test_connect_failure_returns_empty(self):
        src = MySQLSource()
        with patch.object(MySQLSource, "_connect", side_effect=RuntimeError("no driver")):
            result = src.collect({"database": "ci", "build": "1247"})
        assert result == {}

    def test_empty_db_returns_empty_failures_list(self):
        src = MySQLSource()
        with patch.object(MySQLSource, "_connect", return_value=(MagicMock(), "pymysql")), \
             patch.object(MySQLSource, "_query", return_value=[]):
            result = src.collect({"database": "ci", "build": "1247"})
        assert result["failures"] == []

    def test_empty_perf_table_returns_empty_performance_list(self):
        def router_no_perf(conn_pair, sql, params=()):
            if "performance_metrics" in sql:
                return []
            if "build !=" in sql:
                return _HISTORY_ROWS
            return _FAILURE_ROWS

        src = MySQLSource()
        with patch.object(MySQLSource, "_connect", return_value=(MagicMock(), "pymysql")), \
             patch.object(MySQLSource, "_query", side_effect=router_no_perf):
            result = src.collect(dict(self._CONFIG))
        assert result["performance"] == []


# ═══════════════════════════════════════════════════════════════════════════════
# ClickHouseSource — collect() output contract (mocked client)
# ═══════════════════════════════════════════════════════════════════════════════

def _ch_execute_router(client, driver, sql, params=None):
    if "performance_metrics" in sql:
        return _PERF_ROWS
    if "build !=" in sql:
        return _HISTORY_ROWS
    return _FAILURE_ROWS


class TestClickHouseSourceContract:
    """
    Same JSON shape contract as MySQL.  Both tools must produce identical
    output structures so the template renders either without modification.
    """

    _CONFIG = {
        "database": "ci_metrics", "build": "1247", "ref_build": "1244",
        "jira_base_url": "https://jira.example.com/browse/",
        "task_base_url": "https://ci.example.com/jobs/1247/tasks/",
        "log_base_url":  "https://ci.example.com/jobs/1247/logs/",
    }

    def _collect(self, extra_config=None):
        cfg = dict(self._CONFIG)
        if extra_config:
            cfg.update(extra_config)
        src = ClickHouseSource()
        with patch.object(ClickHouseSource, "_connect", return_value=(MagicMock(), "driver")), \
             patch.object(ClickHouseSource, "_execute", side_effect=_ch_execute_router):
            return src.collect(cfg)

    # ── Top-level contract ────────────────────────────────────────────────────

    def test_always_returns_failures_and_performance_keys(self):
        result = self._collect()
        assert "failures" in result
        assert "performance" in result

    # ── Failure / test case shape matches MySQL output ────────────────────────

    def test_scenario_required_keys(self):
        scenario = self._collect()["failures"][0]
        for key in ("scenario", "config", "jira", "jira_url", "test_cases"):
            assert key in scenario

    def test_test_case_required_keys(self):
        tc = self._collect()["failures"][0]["test_cases"][0]
        for key in ("name", "status", "duration", "failure_message", "failure_text",
                    "jira", "jira_url", "task_url", "log_url", "history", "ai_analysis"):
            assert key in tc

    def test_test_case_status_is_valid(self):
        valid = {"fail", "error", "timeout"}
        for scenario in self._collect()["failures"]:
            for tc in scenario["test_cases"]:
                assert tc["status"] in valid

    def test_history_last_entry_is_current_build(self):
        tc = self._collect()["failures"][0]["test_cases"][0]
        assert tc["history"][-1].get("current") is True

    def test_ai_analysis_is_dict(self):
        for scenario in self._collect()["failures"]:
            for tc in scenario["test_cases"]:
                assert isinstance(tc["ai_analysis"], dict)

    # ── Performance model shape matches MySQL output ──────────────────────────

    def test_performance_model_required_keys(self):
        model = self._collect()["performance"][0]
        for key in ("model", "summary_note", "summary_chips", "metrics"):
            assert key in model

    def test_metric_required_keys(self):
        metric = self._collect()["performance"][0]["metrics"][0]
        for key in ("name", "unit", "direction", "current", "reference",
                    "history_values", "history_builds"):
            assert key in metric

    def test_metric_direction_is_valid(self):
        valid = {"higher_better", "lower_better"}
        for model in self._collect()["performance"]:
            for m in model["metrics"]:
                assert m["direction"] in valid

    def test_history_values_and_builds_same_length(self):
        for model in self._collect()["performance"]:
            for m in model["metrics"]:
                assert len(m["history_values"]) == len(m["history_builds"])

    # ── Guard rails ───────────────────────────────────────────────────────────

    def test_missing_build_returns_empty(self):
        src = ClickHouseSource()
        with patch.object(ClickHouseSource, "_connect", return_value=(MagicMock(), "driver")):
            result = src.collect({"database": "ci_metrics", "build": ""})
        assert result == {}

    def test_connect_failure_returns_empty(self):
        src = ClickHouseSource()
        with patch.object(ClickHouseSource, "_connect", side_effect=RuntimeError("no driver")):
            result = src.collect({"database": "ci_metrics", "build": "1247"})
        assert result == {}

    def test_empty_db_returns_empty_failures_list(self):
        src = ClickHouseSource()
        with patch.object(ClickHouseSource, "_connect", return_value=(MagicMock(), "driver")), \
             patch.object(ClickHouseSource, "_execute", return_value=[]):
            result = src.collect(dict(self._CONFIG))
        assert result["failures"] == []

    def test_empty_perf_table_returns_empty_performance_list(self):
        def router_no_perf(client, driver, sql, params=None):
            if "performance_metrics" in sql:
                return []
            if "build !=" in sql:
                return _HISTORY_ROWS
            return _FAILURE_ROWS

        src = ClickHouseSource()
        with patch.object(ClickHouseSource, "_connect", return_value=(MagicMock(), "driver")), \
             patch.object(ClickHouseSource, "_execute", side_effect=router_no_perf):
            result = src.collect(dict(self._CONFIG))
        assert result["performance"] == []


# ═══════════════════════════════════════════════════════════════════════════════
# ClickHouseSource._execute — clickhouse-connect param conversion
# ═══════════════════════════════════════════════════════════════════════════════

class TestClickHouseExecuteAdapter:
    """
    clickhouse-connect uses {_pN} named params; clickhouse-driver uses %s.
    _execute() must convert %s → {_p0}, {_p1}, ... for the connect driver
    and pass a matching parameter dict, while keeping native behaviour for
    the clickhouse-driver ('driver') path.
    """

    def _connect_client(self):
        client = MagicMock()
        result = MagicMock()
        result.column_names = ["build", "name"]
        result.result_rows  = [("1247", "test_a")]
        client.query.return_value = result
        return client

    def test_single_placeholder_converted_to_named(self):
        client = self._connect_client()
        ClickHouseSource()._execute(
            client, "connect", "SELECT * FROM t WHERE build = %s", ["1247"])
        sql  = client.query.call_args[0][0]
        pars = client.query.call_args[1]["parameters"]
        assert "{_p0}" in sql
        assert pars["_p0"] == "1247"

    def test_multiple_placeholders_numbered_sequentially(self):
        client = self._connect_client()
        ClickHouseSource()._execute(
            client, "connect",
            "SELECT * FROM t WHERE a = %s AND b = %s", ["x", "y"])
        sql  = client.query.call_args[0][0]
        pars = client.query.call_args[1]["parameters"]
        assert "{_p0}" in sql and "{_p1}" in sql
        assert "%s" not in sql
        assert pars == {"_p0": "x", "_p1": "y"}

    def test_no_placeholders_passes_empty_params(self):
        client = self._connect_client()
        ClickHouseSource()._execute(client, "connect", "SELECT 1", [])
        pars = client.query.call_args[1]["parameters"]
        assert pars == {}

    def test_driver_mode_calls_native_execute(self):
        client = MagicMock()
        client.execute.return_value = (
            [("1247",)], [("build", "String")]
        )
        result = ClickHouseSource()._execute(
            client, "driver", "SELECT build FROM t", [])
        client.execute.assert_called_once()
        assert result == [{"build": "1247"}]

    def test_driver_mode_returns_list_of_dicts(self):
        client = MagicMock()
        client.execute.return_value = (
            [("1247", "test_a"), ("1246", "test_b")],
            [("build", "String"), ("name", "String")],
        )
        result = ClickHouseSource()._execute(client, "driver", "SELECT build, name FROM t", [])
        assert result == [
            {"build": "1247", "name": "test_a"},
            {"build": "1246", "name": "test_b"},
        ]

    def test_connect_mode_returns_list_of_dicts(self):
        client = self._connect_client()
        result = ClickHouseSource()._execute(
            client, "connect", "SELECT build, name FROM t WHERE x = %s", ["v"])
        assert result == [{"build": "1247", "name": "test_a"}]


# ═══════════════════════════════════════════════════════════════════════════════
# prepare_data — full enrichment pipeline
# ═══════════════════════════════════════════════════════════════════════════════

def _raw_report(n_failures=1, n_perf=1, has_ai=True):
    """Minimal raw dict that prepare_data() expects, matching collect() output."""
    ai = {
        "text": "<strong>Root cause</strong>: missing env var",
        "tag": "REGRESSION · HTTP 403",
        "tag_type": "error",
    } if has_ai else {}

    test_cases = [{
        "name": "test_token_expiry",
        "status": "fail",
        "duration": "18.1s",
        "failure_message": "HTTP 403",
        "failure_text": "Stack trace",
        "jira": "PIPE-458",
        "jira_url": "https://jira.example.com/browse/PIPE-458",
        "task_url": "https://ci.example.com/jobs/1247/tasks/38",
        "log_url":  "https://ci.example.com/jobs/1247/logs/pipe-458",
        "history": [
            {"build": "1245", "status": "pass", "duration": "17s"},
            {"build": "1246", "status": "pass", "duration": "17s"},
            {"build": "current", "status": "fail", "duration": "18.1s", "current": True},
        ],
        "ai_analysis": ai,
    }] * n_failures

    failures = [{
        "scenario": "E2E Upload",
        "config":   "docker-arm64",
        "jira":     "PIPE-123",
        "jira_url": "https://jira.example.com/browse/PIPE-123",
        "test_cases": test_cases,
    }]

    metrics = [
        {
            "name": "Throughput", "unit": "MB/s", "direction": "higher_better",
            "current": 87.3, "reference": 82.9,
            "history_values": [74.0, 77.1, 78.4, 79.2, 81.0, 83.3, 85.1, 87.3],
            "history_builds": ["1240","1241","1242","1243","1244","1245","1246","1247"],
        },
        {
            "name": "Latency p95", "unit": "ms", "direction": "lower_better",
            "current": 234.0, "reference": 255.0,
            "history_values": [268.0, 260.0, 255.0, 250.0, 245.0, 240.0, 237.0, 234.0],
            "history_builds": ["1240","1241","1242","1243","1244","1245","1246","1247"],
        },
    ]

    performance = [{
        "model": "arm64", "summary_note": "", "summary_chips": None,
        "metrics": metrics,
    }] * n_perf

    return {
        "build": {
            "number": "1247", "branch": "feature/test", "ref_build": "1244",
            "badges": {"passed": 34, "failed": 1, "skipped": 0, "metrics": 2},
        },
        "failures":    failures,
        "performance": performance,
    }


class TestPrepareData:

    def test_returns_dict(self):
        assert isinstance(prepare_data(_raw_report()), dict)

    def test_generated_at_added(self):
        assert "generated_at" in prepare_data(_raw_report())

    # ── Performance enrichment ────────────────────────────────────────────────

    def test_sparkline_heights_added(self):
        metric = prepare_data(_raw_report())["performance"][0]["metrics"][0]
        assert "sparkline_heights" in metric

    def test_sparkline_heights_same_length_as_values(self):
        metric = prepare_data(_raw_report())["performance"][0]["metrics"][0]
        assert len(metric["sparkline_heights"]) == len(metric["history_values"])

    def test_delta_fields_added(self):
        metric = prepare_data(_raw_report())["performance"][0]["metrics"][0]
        assert "delta_pct"     in metric
        assert "delta_display" in metric
        assert "delta_css"     in metric

    def test_status_field_added(self):
        metric = prepare_data(_raw_report())["performance"][0]["metrics"][0]
        assert "status" in metric
        assert metric["status"] in {"improved", "regressed", "neutral"}

    def test_unit_display_field_added(self):
        metric = prepare_data(_raw_report())["performance"][0]["metrics"][0]
        assert "unit_display" in metric

    def test_trend_fields_added_to_model(self):
        model = prepare_data(_raw_report())["performance"][0]
        assert "trend_class" in model
        assert "trend_label" in model
        assert model["trend_class"] in {"good", "bad", "neu"}

    def test_count_fields_added_to_model(self):
        model = prepare_data(_raw_report())["performance"][0]
        assert "improved_count"  in model
        assert "regressed_count" in model
        assert "neutral_count"   in model
        assert "total_metrics"   in model

    def test_total_metrics_correct(self):
        model = prepare_data(_raw_report())["performance"][0]
        assert model["total_metrics"] == 2   # Throughput + Latency

    def test_summary_chips_auto_generated_when_none(self):
        chips = prepare_data(_raw_report())["performance"][0]["summary_chips_display"]
        assert isinstance(chips, list)
        assert len(chips) > 0

    def test_summary_chips_display_entries_have_required_keys(self):
        chips = prepare_data(_raw_report())["performance"][0]["summary_chips_display"]
        for chip in chips:
            assert "label"     in chip
            assert "value"     in chip
            assert "css_class" in chip

    def test_history_length_field_added(self):
        model = prepare_data(_raw_report())["performance"][0]
        assert "history_length" in model
        assert model["history_length"] == 8

    def test_summary_note_auto_generated_when_empty(self):
        model = prepare_data(_raw_report())["performance"][0]
        assert isinstance(model["summary_note"], str)
        assert len(model["summary_note"]) > 0

    # ── Lower-better delta direction ──────────────────────────────────────────

    def test_lower_better_improvement_is_pos(self):
        # Latency: current 234 < reference 255 → improvement
        metric = prepare_data(_raw_report())["performance"][0]["metrics"][1]
        assert metric["delta_css"] == "pos"

    def test_higher_better_improvement_is_pos(self):
        # Throughput: current 87.3 > reference 82.9 → improvement
        metric = prepare_data(_raw_report())["performance"][0]["metrics"][0]
        assert metric["delta_css"] == "pos"

    # ── Failure enrichment ────────────────────────────────────────────────────

    def test_history_rate_added(self):
        tc = prepare_data(_raw_report())["failures"][0]["test_cases"][0]
        assert "history_rate" in tc

    def test_history_note_added(self):
        tc = prepare_data(_raw_report())["failures"][0]["test_cases"][0]
        assert "history_note" in tc

    def test_has_ai_true_when_text_present(self):
        tc = prepare_data(_raw_report(has_ai=True))["failures"][0]["test_cases"][0]
        assert tc["has_ai"] is True

    def test_has_ai_false_when_no_ai_text(self):
        tc = prepare_data(_raw_report(has_ai=False))["failures"][0]["test_cases"][0]
        assert tc["has_ai"] is False

    def test_missing_ai_analysis_key_handled_gracefully(self):
        raw = _raw_report()
        del raw["failures"][0]["test_cases"][0]["ai_analysis"]
        result = prepare_data(raw)   # must not raise
        tc = result["failures"][0]["test_cases"][0]
        assert tc["has_ai"] is False
        assert isinstance(tc["ai_analysis"], dict)

    def test_is_scenario_error_defaults_false(self):
        tc = prepare_data(_raw_report())["failures"][0]["test_cases"][0]
        assert tc["is_scenario_error"] is False

    def test_scenario_config_inherited_by_test_case(self):
        tc = prepare_data(_raw_report())["failures"][0]["test_cases"][0]
        # tc has no own "config", inherits from scenario
        assert tc["config"] == "docker-arm64"

    # ── Empty collections ─────────────────────────────────────────────────────

    def test_empty_failures_handled(self):
        raw = _raw_report()
        raw["failures"] = []
        assert prepare_data(raw)["failures"] == []

    def test_empty_performance_handled(self):
        raw = _raw_report()
        raw["performance"] = []
        assert prepare_data(raw)["performance"] == []

    def test_empty_history_on_test_case_handled(self):
        raw = _raw_report()
        raw["failures"][0]["test_cases"][0]["history"] = []
        result = prepare_data(raw)   # must not raise
        tc = result["failures"][0]["test_cases"][0]
        assert isinstance(tc["history_rate"], str)
        assert isinstance(tc["history_note"], str)


# ═══════════════════════════════════════════════════════════════════════════════
# Profiler — unit tests for the timing recorder
# ═══════════════════════════════════════════════════════════════════════════════

from datasources.base import Profiler


class TestProfiler:

    def test_span_records_elapsed_time(self):
        prof = Profiler()
        with prof.span("connect_s"):
            pass
        assert "connect_s" in prof.spans
        assert isinstance(prof.spans["connect_s"], float)

    def test_span_value_is_non_negative(self):
        prof = Profiler()
        with prof.span("query_s"):
            pass
        assert prof.spans["query_s"] >= 0.0

    def test_multiple_spans_all_recorded(self):
        prof = Profiler()
        with prof.span("connect_s"):
            pass
        with prof.span("failures_query_s"):
            pass
        with prof.span("perf_query_s"):
            pass
        assert set(prof.spans) == {"connect_s", "failures_query_s", "perf_query_s"}

    def test_to_dict_returns_copy(self):
        prof = Profiler()
        with prof.span("connect_s"):
            pass
        d = prof.to_dict()
        assert isinstance(d, dict)
        d["connect_s"] = 999.0          # mutating the copy must not affect the profiler
        assert prof.spans["connect_s"] != 999.0

    def test_span_value_is_rounded_to_three_decimals(self):
        prof = Profiler()
        with prof.span("x_s"):
            pass
        val = prof.spans["x_s"]
        assert val == round(val, 3)

    def test_empty_profiler_to_dict_is_empty(self):
        assert Profiler().to_dict() == {}


# ═══════════════════════════════════════════════════════════════════════════════
# Profiling — collect() output (MySQL and ClickHouse)
# ═══════════════════════════════════════════════════════════════════════════════

_MYSQL_CFG = {
    "database": "ci_reports", "build": "1247", "ref_build": "1244",
    "jira_base_url": "https://jira.example.com/browse/",
}

_CH_CFG = {
    "database": "ci_metrics", "build": "1247", "ref_build": "1244",
}


def _mysql_collect(extra=None):
    cfg = dict(_MYSQL_CFG)
    if extra:
        cfg.update(extra)
    src = MySQLSource()
    with patch.object(MySQLSource, "_connect", return_value=(MagicMock(), "pymysql")), \
         patch.object(MySQLSource, "_query", side_effect=_mysql_query_router):
        return src.collect(cfg)


def _ch_collect(extra=None):
    cfg = dict(_CH_CFG)
    if extra:
        cfg.update(extra)
    src = ClickHouseSource()
    with patch.object(ClickHouseSource, "_connect", return_value=(MagicMock(), "driver")), \
         patch.object(ClickHouseSource, "_execute", side_effect=_ch_execute_router):
        return src.collect(cfg)


class TestMySQLProfiling:

    def test_collect_returns_profiling_key(self):
        assert "profiling" in _mysql_collect()

    def test_profiling_has_required_keys(self):
        prof = _mysql_collect()["profiling"]
        for key in ("tool", "total_s", "spans"):
            assert key in prof, f"profiling missing key: {key}"

    def test_profiling_tool_name_is_mysql(self):
        assert _mysql_collect()["profiling"]["tool"] == "mysql"

    def test_total_s_is_non_negative_float(self):
        total = _mysql_collect()["profiling"]["total_s"]
        assert isinstance(total, float)
        assert total >= 0.0

    def test_standard_spans_are_present(self):
        spans = _mysql_collect()["profiling"]["spans"]
        for name in ("connect_s", "failures_query_s", "history_query_s", "perf_query_s"):
            assert name in spans, f"span missing: {name}"

    def test_all_span_values_are_non_negative_floats(self):
        for name, val in _mysql_collect()["profiling"]["spans"].items():
            assert isinstance(val, float), f"{name} is not float"
            assert val >= 0.0, f"{name} is negative"

    def test_connect_failure_returns_empty_no_profiling(self):
        src = MySQLSource()
        with patch.object(MySQLSource, "_connect", side_effect=RuntimeError("timeout")):
            result = src.collect({"database": "ci", "build": "1247"})
        assert result == {}

    def test_missing_build_returns_empty_no_profiling(self):
        src = MySQLSource()
        with patch.object(MySQLSource, "_connect", return_value=(MagicMock(), "pymysql")):
            result = src.collect({"database": "ci", "build": ""})
        assert result == {}

    def test_empty_db_still_has_profiling(self):
        src = MySQLSource()
        with patch.object(MySQLSource, "_connect", return_value=(MagicMock(), "pymysql")), \
             patch.object(MySQLSource, "_query", return_value=[]):
            result = src.collect(dict(_MYSQL_CFG))
        assert "profiling" in result
        assert "connect_s" in result["profiling"]["spans"]


class TestClickHouseProfiling:

    def test_collect_returns_profiling_key(self):
        assert "profiling" in _ch_collect()

    def test_profiling_has_required_keys(self):
        prof = _ch_collect()["profiling"]
        for key in ("tool", "total_s", "spans"):
            assert key in prof

    def test_profiling_tool_name_is_clickhouse(self):
        assert _ch_collect()["profiling"]["tool"] == "clickhouse"

    def test_total_s_is_non_negative_float(self):
        total = _ch_collect()["profiling"]["total_s"]
        assert isinstance(total, float)
        assert total >= 0.0

    def test_standard_spans_are_present(self):
        spans = _ch_collect()["profiling"]["spans"]
        for name in ("connect_s", "failures_query_s", "history_query_s", "perf_query_s"):
            assert name in spans, f"span missing: {name}"

    def test_all_span_values_are_non_negative_floats(self):
        for name, val in _ch_collect()["profiling"]["spans"].items():
            assert isinstance(val, float), f"{name} is not float"
            assert val >= 0.0

    def test_connect_failure_returns_empty_no_profiling(self):
        src = ClickHouseSource()
        with patch.object(ClickHouseSource, "_connect", side_effect=RuntimeError("timeout")):
            result = src.collect({"database": "ci_metrics", "build": "1247"})
        assert result == {}

    def test_empty_db_still_has_profiling(self):
        src = ClickHouseSource()
        with patch.object(ClickHouseSource, "_connect", return_value=(MagicMock(), "driver")), \
             patch.object(ClickHouseSource, "_execute", return_value=[]):
            result = src.collect(dict(_CH_CFG))
        assert "profiling" in result
        assert "connect_s" in result["profiling"]["spans"]


# ═══════════════════════════════════════════════════════════════════════════════
# merge_results — profiling aggregation
# ═══════════════════════════════════════════════════════════════════════════════

class TestMergeResultsProfiling:

    _BASE = {"build": {"number": "1247"}}

    def _tool_output(self, tool_name, total=1.23, spans=None):
        return {
            "failures":    [],
            "performance": [],
            "profiling": {
                "tool":    tool_name,
                "total_s": total,
                "spans":   spans or {"connect_s": 0.1, "failures_query_s": 0.8},
            },
        }

    def test_merge_always_has_profiling_key(self):
        result = merge_results(self._BASE, [])
        assert "profiling" in result

    def test_merge_profiling_has_tools_key(self):
        result = merge_results(self._BASE, [])
        assert "tools" in result["profiling"]

    def test_no_tools_gives_empty_tools_dict(self):
        result = merge_results(self._BASE, [])
        assert result["profiling"]["tools"] == {}

    def test_single_tool_profiling_merged_by_name(self):
        out = self._tool_output("mysql", total=4.2)
        result = merge_results(self._BASE, [out])
        assert "mysql" in result["profiling"]["tools"]
        assert result["profiling"]["tools"]["mysql"]["total_s"] == 4.2

    def test_multiple_tools_both_in_profiling(self):
        m_out  = self._tool_output("mysql",      total=4.2)
        ch_out = self._tool_output("clickhouse", total=7.8)
        result = merge_results(self._BASE, [m_out, ch_out])
        assert "mysql"      in result["profiling"]["tools"]
        assert "clickhouse" in result["profiling"]["tools"]

    def test_tool_spans_preserved(self):
        spans = {"connect_s": 0.05, "failures_query_s": 1.2, "history_query_s": 3.0}
        out = self._tool_output("mysql", spans=spans)
        merged_spans = merge_results(self._BASE, [out])["profiling"]["tools"]["mysql"]["spans"]
        assert merged_spans == spans

    def test_tool_output_without_profiling_is_skipped_gracefully(self):
        no_prof = {"failures": [], "performance": []}
        result = merge_results(self._BASE, [no_prof])
        assert result["profiling"]["tools"] == {}

    def test_empty_tool_output_skipped(self):
        result = merge_results(self._BASE, [{}, None])
        assert result["profiling"]["tools"] == {}
