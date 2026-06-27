"""
datasources/tool_junit_xml.py
──────────────────────────────
Data source: JUnit XML test report parser.

Reads standard JUnit XML files produced by pytest, JUnit 4/5, TestNG,
Go test, and most other frameworks that support the JUnit output format.
Extracts all failing and erroring test cases and optionally builds a
per-test history strip by reading previous XML files from a history
directory.

Run config keys
───────────────
report_glob      str   glob for current XML files, e.g. "./results/*.xml"  [required]
history_dir      str   directory of past XML files named <build>.xml        [optional]
history_limit    int   max number of historic builds to include  [default 7]
scenario_name    str   override the <testsuite name> shown in the report
scenario_jira    str   JIRA key for the whole scenario, e.g. "PIPE-123"
config_label     str   agent / config shown in collapsed row
jira_base_url    str   base for test-level JIRA links; ticket auto-extracted from
                       test names containing [PROJ-NNN]
task_base_url    str   prefix for "task" links (safe test name is appended)
log_base_url     str   prefix for "failure log" links (safe test name is appended)

Example run config entry
────────────────────────
{
  "name": "junit_xml",
  "enabled": true,
  "config": {
    "report_glob":    "./test-results/*.xml",
    "history_dir":    "./test-results/history/",
    "history_limit":  7,
    "scenario_jira":  "PIPE-123",
    "config_label":   "docker-arm64 · Linux · agent-01",
    "jira_base_url":  "https://jira.example.com/browse/",
    "task_base_url":  "https://ci.example.com/jobs/1247/tasks/",
    "log_base_url":   "https://ci.example.com/jobs/1247/logs/"
  }
}
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from .base import DataSource

log = logging.getLogger(__name__)

_JIRA_RE   = re.compile(r'\[([A-Z]+-\d+)\]')
_SAFE_NAME = re.compile(r'[^\w\-]')


def _safe(name: str) -> str:
    return _SAFE_NAME.sub('_', name)[:80]


def _dur(secs_str: str) -> str:
    try:
        s = float(secs_str)
        if s >= 60:
            return f"{int(s // 60)}m {s % 60:.0f}s"
        return f"{s:.1f}s"
    except (TypeError, ValueError):
        return "—"


class JUnitXMLSource(DataSource):

    @property
    def name(self) -> str:
        return "junit_xml"

    @property
    def description(self) -> str:
        return "Parses JUnit XML test reports; extracts failures with optional history"

    # ── Entry point ───────────────────────────────────────────────────────────

    def collect(self, config: dict) -> dict:
        report_glob  = config.get("report_glob", "*.xml")
        history_dir  = config.get("history_dir")
        limit        = int(config.get("history_limit", 7))
        jira_base    = config.get("jira_base_url", "")
        task_base    = config.get("task_base_url", "")
        log_base     = config.get("log_base_url", "")

        xml_files = sorted(Path(".").glob(report_glob))
        if not xml_files:
            log.warning("junit_xml: no files matched %r", report_glob)
            return {}

        scenarios = []
        for xml_path in xml_files:
            for suite in self._parse_file(xml_path):
                failures = self._extract_failures(
                    suite, config, history_dir, limit, jira_base, task_base, log_base
                )
                if not failures:
                    continue

                jira = config.get("scenario_jira", "")
                scenarios.append({
                    "scenario":   config.get("scenario_name") or suite["name"] or xml_path.stem,
                    "config":     config.get("config_label", ""),
                    "jira":       jira,
                    "jira_url":   f"{jira_base}{jira}" if jira and jira_base else "#",
                    "test_cases": failures,
                })

        if not scenarios:
            log.info("junit_xml: no failures found in matched files")

        return {"failures": scenarios}

    # ── XML parsing ───────────────────────────────────────────────────────────

    def _parse_file(self, path: Path) -> list[dict]:
        try:
            root = ET.parse(str(path)).getroot()
        except ET.ParseError as exc:
            log.error("junit_xml: cannot parse %s: %s", path, exc)
            return []

        if root.tag == "testsuites":
            suite_els = list(root)
        elif root.tag == "testsuite":
            suite_els = [root]
        else:
            suite_els = root.findall(".//testsuite")

        suites = []
        for el in suite_els:
            cases = []
            for tc in el.findall("testcase"):
                cases.append({
                    "name":      tc.get("name", ""),
                    "classname": tc.get("classname", ""),
                    "time":      tc.get("time", ""),
                    "failure":   tc.find("failure"),
                    "error":     tc.find("error"),
                    "skipped":   tc.find("skipped") is not None,
                })

            # Capture suite-level error / failure (not inside any <testcase>)
            suite_error = None
            suite_err_el = el.find("error")
            suite_fail_el = el.find("failure")
            err_el = suite_err_el if suite_err_el is not None else suite_fail_el
            if err_el is not None:
                suite_error = {
                    "type":    err_el.get("type", ""),
                    "message": err_el.get("message", "").strip(),
                    "text":    (err_el.text or "").strip(),
                }
            elif not cases:
                # No <testcase> elements and no explicit error element.
                # Check suite-level attributes for clues.
                n_errors   = int(el.get("errors",   0) or 0)
                n_failures = int(el.get("failures", 0) or 0)
                if n_errors or n_failures:
                    suite_error = {
                        "type":    el.get("error_type", ""),
                        "message": el.get("error_message", "No test cases produced — suite-level error"),
                        "text":    "",
                    }

            suites.append({
                "name":        el.get("name", ""),
                "cases":       cases,
                "suite_error": suite_error,
            })

        return suites

    # ── Failure extraction ────────────────────────────────────────────────────

    _ERROR_TYPE_LABELS = {
        "infra":       "INFRA ERROR",
        "infrastructure": "INFRA ERROR",
        "provision":   "INFRA ERROR",
        "build":       "BUILD ERROR",
        "compile":     "BUILD ERROR",
        "install":     "INSTALL ERROR",
        "setup":       "SETUP ERROR",
        "timeout":     "TIMEOUT",
        "connection":  "CONN ERROR",
    }

    def _error_label(self, error_type: str, message: str) -> str:
        """Derive a short display label from the error type string or message."""
        combined = f"{error_type} {message}".lower()
        for keyword, label in self._ERROR_TYPE_LABELS.items():
            if keyword in combined:
                return label
        return "SCENE ERROR"

    def _extract_failures(self, suite, config, history_dir, limit,
                          jira_base, task_base, log_base) -> list[dict]:
        out = []

        # ── Suite-level error: synthesize a single TC representing it ─────────
        if suite.get("suite_error") and not any(
            tc["failure"] is not None or tc["error"] is not None
            for tc in suite.get("cases", [])
        ):
            se = suite["suite_error"]
            label = self._error_label(se.get("type", ""), se.get("message", ""))
            suite_slug = _safe(suite.get("name", "scenario"))
            out.append({
                "name":              f"Scenario setup failed — {label.title()}",
                "duration":          "—",
                "status":            "error",
                "is_scenario_error": True,
                "error_label":       label,
                "failure_message":   se.get("message", ""),
                "failure_text":      se.get("text", ""),
                "jira":              "",
                "jira_url":          "#",
                "task_url":          f"{task_base}{suite_slug}-setup" if task_base else "#",
                "log_url":           f"{log_base}{suite_slug}-setup"  if log_base  else "#",
                "history":           [],
                "ai_analysis":       {},
            })
            return out

        # ── Normal per-test-case failure extraction ───────────────────────────
        for tc in suite.get("cases", []):
            # ElementTree elements are falsy when they have no child elements,
            # even if they carry text.  Always use `is not None`.
            failure_el = tc["failure"]
            error_el   = tc["error"]
            el = failure_el if failure_el is not None else error_el
            if el is None:
                continue                                 # passed or skipped

            status   = "fail" if failure_el is not None else "error"
            name     = tc["name"]
            duration = _dur(tc["time"])
            jira     = self._jira(name)

            out.append({
                "name":            name,
                "duration":        duration,
                "status":          status,
                "failure_message": el.get("message", "").strip(),
                "failure_text":    (el.text or "").strip(),
                "jira":     jira,
                "jira_url": f"{jira_base}{jira}" if jira and jira_base else "#",
                "task_url": f"{task_base}{_safe(name)}" if task_base else "#",
                "log_url":  f"{log_base}{_safe(name)}"  if log_base  else "#",
                "history":  self._history(name, history_dir, limit, duration, status),
                "ai_analysis": {},          # JUnit XML carries no AI analysis
            })
        return out

    def _jira(self, name: str) -> str:
        m = _JIRA_RE.search(name)
        return m.group(1) if m else ""

    # ── History ───────────────────────────────────────────────────────────────

    def _history(self, test_name: str, history_dir, limit: int,
                 cur_dur: str, cur_status: str) -> list[dict]:
        entries = []

        if history_dir:
            hist_path = Path(history_dir)
            if hist_path.exists():
                # Files must be named <build_number>.xml; sorted = chronological
                past = sorted(hist_path.glob("*.xml"))[-limit:]
                for xml_path in past:
                    status, dur = self._lookup(xml_path, test_name)
                    if status is not None:
                        entries.append({
                            "build":    xml_path.stem,
                            "status":   status,
                            "duration": dur,
                        })
            else:
                log.warning("junit_xml: history_dir not found: %s", hist_path)

        # Current build always last, always marked current
        entries.append({
            "build":    "current",
            "status":   cur_status,
            "duration": cur_dur,
            "current":  True,
        })

        return entries

    def _lookup(self, path: Path, test_name: str):
        """Return (status, duration) for test_name in an XML file."""
        try:
            root = ET.parse(str(path)).getroot()
        except Exception:
            return None, None

        for tc in root.iter("testcase"):
            if tc.get("name") != test_name:
                continue
            dur = _dur(tc.get("time", ""))
            if tc.find("failure") is not None:   return "fail",  dur
            if tc.find("error")   is not None:   return "error", dur
            if tc.find("skipped") is not None:   return "skip",  "—"
            return "pass", dur

        return None, None          # test not present in that historical run
