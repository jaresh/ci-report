"""
tests/test_compute.py
─────────────────────
Unit tests for the pure-computation layer.

Run with:  pytest tests/
           pytest tests/ -v
           pytest tests/ -v --tb=short
"""

import sys
from pathlib import Path

# Make sure the project root is on sys.path so imports work
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from generate_report import (
    normalize_heights,
    compute_delta,
    history_summary,
    validate_merged,
    resolve_build,
    SPARK_MIN_H,
    SPARK_MAX_H,
)
from jira_enricher import JiraEnricher


# ── normalize_heights ─────────────────────────────────────────────────────────

class TestNormalizeHeights:

    def test_empty(self):
        assert normalize_heights([], "higher_better") == []

    def test_all_same_higher_better(self):
        # Flat series → all bars at max height
        result = normalize_heights([5, 5, 5, 5], "higher_better")
        assert result == [SPARK_MAX_H] * 4

    def test_all_same_lower_better(self):
        result = normalize_heights([5, 5, 5, 5], "lower_better")
        assert result == [SPARK_MAX_H] * 4

    def test_higher_better_ascending(self):
        # Improving trend: last bar should be tallest
        result = normalize_heights([10, 20, 30, 40], "higher_better")
        assert result[-1] == SPARK_MAX_H
        assert result[0]  == SPARK_MIN_H
        assert result == sorted(result)            # monotonically increasing

    def test_lower_better_inverted(self):
        # Improving trend (decreasing values) should still produce ascending bars
        result = normalize_heights([40, 30, 20, 10], "lower_better")
        assert result[-1] == SPARK_MAX_H           # lowest value → tallest bar
        assert result[0]  == SPARK_MIN_H
        assert result == sorted(result)

    def test_output_within_bounds(self):
        import random
        values = [random.uniform(0, 100) for _ in range(20)]
        for direction in ("higher_better", "lower_better"):
            result = normalize_heights(values, direction)
            assert all(SPARK_MIN_H <= h <= SPARK_MAX_H for h in result)

    def test_single_value(self):
        result = normalize_heights([42.0], "higher_better")
        assert len(result) == 1
        assert result[0] == SPARK_MAX_H

    def test_output_length_matches_input(self):
        values = [1, 2, 3, 4, 5, 6, 7, 8]
        assert len(normalize_heights(values, "higher_better")) == 8
        assert len(normalize_heights(values, "lower_better"))  == 8


# ── compute_delta ─────────────────────────────────────────────────────────────

class TestComputeDelta:

    def test_zero_reference(self):
        pct, display, css = compute_delta(100, 0, "higher_better")
        assert pct == 0.0
        assert css == "neu"

    def test_higher_better_improvement(self):
        pct, display, css = compute_delta(110, 100, "higher_better")
        assert pct == pytest.approx(10.0)
        assert css == "pos"
        assert "▲" in display

    def test_higher_better_regression(self):
        pct, display, css = compute_delta(90, 100, "higher_better")
        assert pct == pytest.approx(-10.0)
        assert css == "neg"
        assert "▼" in display

    def test_higher_better_neutral(self):
        # Within DELTA_THRESH (2%) → neutral
        pct, display, css = compute_delta(101, 100, "higher_better")
        assert css == "neu"

    def test_lower_better_improvement(self):
        # Current is lower than ref → improvement
        pct, display, css = compute_delta(90, 100, "lower_better")
        assert css == "pos"

    def test_lower_better_regression(self):
        # Current is higher than ref → regression
        pct, display, css = compute_delta(110, 100, "lower_better")
        assert css == "neg"

    def test_lower_better_neutral(self):
        pct, display, css = compute_delta(101, 100, "lower_better")
        assert css == "neu"

    def test_display_zero(self):
        _, display, _ = compute_delta(100, 100, "higher_better")
        assert display == "→ 0%"

    def test_display_positive(self):
        _, display, _ = compute_delta(110, 100, "higher_better")
        assert "+10.0%" in display

    def test_display_negative(self):
        _, display, _ = compute_delta(90, 100, "higher_better")
        assert "-10.0%" in display


# ── history_summary ───────────────────────────────────────────────────────────

class TestHistorySummary:

    def _tc(self, statuses, current_status="fail"):
        history = [{"build": str(1240 + i), "status": s, "duration": "30s"}
                   for i, s in enumerate(statuses)]
        history.append({"build": "1247", "status": current_status,
                        "duration": "18s", "current": True})
        return history

    def test_empty_history(self):
        rate, note = history_summary([])
        assert rate == ""
        assert note == ""

    def test_single_entry_current_only(self):
        history = [{"build": "1247", "status": "fail", "duration": "5s", "current": True}]
        rate, note = history_summary(history)
        assert "0 / 1" in rate
        assert note == ""           # no prior history to compare

    def test_first_failure(self):
        history = self._tc(["pass", "pass", "pass", "pass", "pass", "pass", "pass"])
        rate, note = history_summary(history)
        assert "7 / 8" in rate
        assert "first failure" in note

    def test_recurring_failure(self):
        # More than half of prior builds also failed
        history = self._tc(["fail", "fail", "pass", "fail", "fail"])
        rate, note = history_summary(history)
        assert "recurring" in note

    def test_intermittent_failure(self):
        # Only a minority of prior builds failed
        history = self._tc(["pass", "pass", "fail", "pass", "pass", "pass"])
        rate, note = history_summary(history)
        assert "intermittent" in note

    def test_pass_current_no_note(self):
        history = self._tc(["pass", "pass"], current_status="pass")
        _, note = history_summary(history)
        assert note == ""


# ── validate_merged ───────────────────────────────────────────────────────────

class TestValidateMerged:

    def _minimal_valid(self):
        return {
            "build": {"number": "1247", "badges": {}},
            "failures": [],
            "performance": [],
        }

    def test_valid_minimal(self):
        assert validate_merged(self._minimal_valid()) == []

    def test_missing_build_number(self):
        data = self._minimal_valid()
        data["build"]["number"] = ""
        errors = validate_merged(data)
        assert any("number" in e for e in errors)

    def test_missing_scenario_name(self):
        data = self._minimal_valid()
        data["failures"] = [{"scenario": "", "test_cases": []}]
        errors = validate_merged(data)
        assert any("scenario" in e for e in errors)

    def test_invalid_test_status(self):
        data = self._minimal_valid()
        data["failures"] = [{"scenario": "S", "test_cases": [
            {"name": "tc", "status": "broken"}   # invalid
        ]}]
        errors = validate_merged(data)
        assert any("status" in e for e in errors)

    def test_valid_test_statuses(self):
        data = self._minimal_valid()
        data["failures"] = [{"scenario": "S", "test_cases": [
            {"name": "tc1", "status": "fail"},
            {"name": "tc2", "status": "error"},
            {"name": "tc3", "status": "timeout"},
        ]}]
        assert validate_merged(data) == []

    def test_missing_metric_current(self):
        data = self._minimal_valid()
        data["performance"] = [{"model": "M", "metrics": [{"name": "X"}]}]
        errors = validate_merged(data)
        assert any("current" in e for e in errors)

    def test_invalid_direction(self):
        data = self._minimal_valid()
        data["performance"] = [{"model": "M", "metrics": [
            {"name": "X", "current": 1.0, "direction": "sideways"}
        ]}]
        errors = validate_merged(data)
        assert any("direction" in e for e in errors)


# ── resolve_build ─────────────────────────────────────────────────────────────

class TestResolveBuild:

    def test_string_replacement(self):
        assert resolve_build("results/{build}/junit.xml", "1247") == "results/1247/junit.xml"

    def test_dict_recursive(self):
        cfg = {"report_glob": "results/{build}/*.xml", "timeout": 30}
        out = resolve_build(cfg, "1247")
        assert out["report_glob"] == "results/1247/*.xml"
        assert out["timeout"] == 30

    def test_list_recursive(self):
        cfg = ["metrics/{build}/arm64.json", "metrics/{build}/curl.json"]
        out = resolve_build(cfg, "1247")
        assert out == ["metrics/1247/arm64.json", "metrics/1247/curl.json"]

    def test_nested(self):
        cfg = {"files": ["metrics/{build}/arm64.json"], "model": "arm64"}
        out = resolve_build(cfg, "1247")
        assert out["files"][0] == "metrics/1247/arm64.json"
        assert out["model"]    == "arm64"

    def test_no_placeholder(self):
        assert resolve_build("static/path.xml", "1247") == "static/path.xml"

    def test_non_string_passthrough(self):
        assert resolve_build(42, "1247")   == 42
        assert resolve_build(True, "1247") == True
        assert resolve_build(None, "1247") is None


# ── JiraEnricher._extract_terms ───────────────────────────────────────────────

class TestExtractTerms:

    def _enricher(self):
        return JiraEnricher({
            "base_url": "http://x",
            "project_keys": ["PIPE"],
            "search_terms": 3,
        })

    def test_search_hint_takes_priority(self):
        e  = self._enricher()
        tc = {
            "name": "Bearer token expiry [PIPE-458]",
            "failure_message": "HTTP 403",
            "search_hint": "token refresh env var missing",
        }
        terms = e._extract_terms(tc)
        assert "token" in terms
        assert "refresh" in terms
        # HTTP code should NOT appear (hint overrides)
        assert "403" not in terms

    def test_http_code_extracted(self):
        e  = self._enricher()
        tc = {"name": "Upload fails with HTTP error",
              "failure_message": "403 Forbidden at chunk 7",
              "jira": "PIPE-1"}
        terms = e._extract_terms(tc)
        assert "403" in terms

    def test_jira_key_not_in_terms(self):
        e  = self._enricher()
        tc = {"name": "Token expiry [PIPE-458]",
              "failure_message": "expired",
              "jira": "PIPE-458"}
        terms = e._extract_terms(tc)
        # PIPE-458 is direct lookup — should not appear as a search term
        assert "PIPE-458" not in terms
        assert "PIPE" not in terms

    def test_stop_words_excluded(self):
        e  = self._enricher()
        tc = {"name": "test the upload and retry when fails",
              "failure_message": ""}
        terms = e._extract_terms(tc)
        for stop in ("test", "the", "and", "when"):
            assert stop not in terms

    def test_max_terms_respected(self):
        e  = self._enricher()
        tc = {"name": "bearer token expiry chunk boundary upload retry",
              "failure_message": "very long message with many words here"}
        terms = e._extract_terms(tc)
        # n_terms * 2 is the internal cap before JQL slices to n_terms
        assert len(terms) <= e.n_terms * 2

    def test_build_jql_uses_top_terms(self):
        e  = self._enricher()
        tc = {"name": "Token expiry [PIPE-458]",
              "failure_message": "HTTP 403 Forbidden",
              "jira": "PIPE-458"}
        jql = e._build_jql(tc)
        assert "project in (PIPE)" in jql
        assert "text ~" in jql
        assert "ORDER BY updated DESC" in jql
