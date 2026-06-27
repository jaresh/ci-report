"""
datasources/tool_clickhouse.py
───────────────────────────────
Full data source: reads all test failures and all performance metrics from
ClickHouse and returns the complete standard output expected by the HTML template.

When this tool is enabled (--clickhouse flag), it always provides both:
  • failures    — all failing test cases for this build, with history strips
  • performance — all models and metrics found in performance_metrics,
                  grouped by model, for the last perf_history_limit builds

This tool owns only what is schema-specific: the SQL constants and the
row→contract mapping (`_collect_failures` / `_collect_performance`). The DB
connection and the %s param adapter live in `ClickHouseTransport`
(datasources/transports.py) and the JSON-contract builders in
datasources/contract.py — both shared with any other ClickHouse-backed tool.

Schema and fixture data: examples/fixtures/clickhouse_schema.sql

Dependencies (install one):
    pip install clickhouse-driver     (native TCP, port 9000)
    pip install clickhouse-connect    (HTTP interface, port 8123)

Config keys
───────────
host              str    DB host                                [default: localhost]
port              int    native TCP port                        [default: 9000]
database          str    database name                          [default: default]
user_env          str    env var holding the DB user            [default: CLICKHOUSE_USER]
password_env      str    env var holding the DB password        [default: CLICKHOUSE_PASSWORD]
build             str    current build identifier — use "{build}"   [required]
ref_build         str    reference build for metric delta       [optional]
history_limit     int    prior builds in failure history strip  [default: 7]
perf_history_limit int   builds shown in performance chart      [default: 8]
jira_base_url     str    base URL for JIRA links                [optional]
task_base_url     str    base URL for task links                [optional]
log_base_url      str    base URL for log links                 [optional]

Log fetching keys (optional) are documented in datasources/logs.py.
"""

from __future__ import annotations

import logging
import os
import time

from .base import (DataSource, ModelPerformance, Profiler, Scenario,
                   TestCase, ToolOutput, ToolProfiling)
from .contract import (_build_performance, _fmt_dur, _group_history,
                       _history_list, _safe)
from .transports import ClickHouseTransport

log = logging.getLogger(__name__)

# ── SQL (schema-specific) ───────────────────────────────────────────────────────

_FAILURES_SQL = """
    SELECT scenario, config, name, status, duration_s,
           failure_msg, substring(failure_txt, 1, 8192) AS failure_txt,
           jira, jira_url, task_url, log_url
    FROM   test_runs
    WHERE  build = %s
      AND  status IN ('fail', 'error', 'timeout')
    ORDER  BY scenario, name
"""

_BATCH_HISTORY_SQL = """
    SELECT build, name, status, duration_s
    FROM   test_runs
    WHERE  name IN ({ph})
      AND  build != %s
    ORDER  BY name, ran_at DESC
"""

# Fetch only the last perf_history_limit distinct builds to avoid a full
# table scan as performance_metrics grows over time.
_PERF_SQL = """
    SELECT model, metric_name, unit, direction, build, value
    FROM   performance_metrics
    WHERE  build IN (
        SELECT DISTINCT build
        FROM   performance_metrics
        ORDER  BY recorded_at DESC
        LIMIT  %s
    )
    ORDER  BY model, metric_name, recorded_at ASC
"""


# ── Source class ──────────────────────────────────────────────────────────────

class ClickHouseSource(DataSource, ClickHouseTransport):

    @property
    def name(self) -> str:
        return "clickhouse"

    @property
    def description(self) -> str:
        return ("Full data source: reads all test failures and performance metrics "
                "from ClickHouse (requires clickhouse-driver or clickhouse-connect)")

    # ── Entry point ───────────────────────────────────────────────────────────

    def collect(self, config: dict) -> ToolOutput:
        host     = config.get("host", "localhost")
        port     = int(config.get("port", 9000))
        database = config.get("database", "default")
        user     = os.environ.get(config.get("user_env",     "CLICKHOUSE_USER"), "default")
        password = os.environ.get(config.get("password_env", "CLICKHOUSE_PASSWORD"), "")
        build    = str(config.get("build", ""))

        if not build:
            log.error('clickhouse: "build" key not set — add "build": "{build}" to config')
            return {}

        prof    = Profiler()
        t_start = time.perf_counter()

        try:
            with prof.span("connect_s"):
                client, driver = self._connect(host, port, database, user, password)
        except Exception as exc:
            log.error("clickhouse: cannot connect — %s", exc)
            return {}

        try:
            failures  = self._collect_failures(client, driver, config, prof)
            log_stats = None
            if config.get("fetch_logs"):
                from .logs import attach_logs
                with prof.span("log_fetch_s"):
                    log_stats = attach_logs(failures, config)
            performance = self._collect_performance(client, driver, config, prof)

            profiling: ToolProfiling = {
                "tool":    self.name,
                "total_s": round(time.perf_counter() - t_start, 3),
                "spans":   prof.to_dict(),
            }
            if log_stats is not None:
                profiling["logs"] = log_stats
            return {
                "failures":    failures,
                "performance": performance,
                "profiling":   profiling,
            }
        except Exception as exc:
            log.error("clickhouse: collection failed — %s", exc)
            return {}

    # ── Failures ──────────────────────────────────────────────────────────────

    def _collect_failures(self, client, driver: str, config: dict, prof: Profiler) -> list[Scenario]:
        build     = str(config["build"])
        limit     = int(config.get("history_limit", 7))
        jira_base = config.get("jira_base_url", "")
        task_base = config.get("task_base_url", "")
        log_base  = config.get("log_base_url",  "")

        with prof.span("failures_query_s"):
            rows = self._execute(client, driver, _FAILURES_SQL, [build])
        if not rows:
            log.info("clickhouse: no failures for build %s", build)
            return []

        names    = list({r["name"] for r in rows})
        hist_sql = _BATCH_HISTORY_SQL.format(ph=', '.join(['%s'] * len(names)))
        with prof.span("history_query_s"):
            hist_map = _group_history(
                self._execute(client, driver, hist_sql, list(names) + [build]),
                limit,
            )

        groups: dict = {}
        for row in rows:
            key = (str(row["scenario"]), str(row["config"]))
            groups.setdefault(key, []).append(row)

        scenarios: list[Scenario] = []
        for (scenario, config_label), tcs in groups.items():
            test_cases: list[TestCase] = []
            for row in tcs:
                name   = str(row["name"])
                status = str(row["status"])
                dur    = _fmt_dur(row.get("duration_s"))
                jira   = str(row.get("jira") or "")
                test_cases.append({
                    "name":            name,
                    "status":          status,
                    "duration":        dur,
                    "failure_message": (str(row.get("failure_msg") or "")).strip(),
                    "failure_text":    (str(row.get("failure_txt") or "")).strip(),
                    "jira":            jira,
                    "jira_url":        str(row.get("jira_url") or (f"{jira_base}{jira}" if jira and jira_base else "#")),
                    "task_url":        str(row.get("task_url") or (f"{task_base}{_safe(name)}" if task_base else "#")),
                    "log_url":         str(row.get("log_url")  or (f"{log_base}{_safe(name)}"  if log_base  else "#")),
                    "history":         _history_list(hist_map.get(name, []), status, dur),
                    "ai_analysis":     {},
                })
            scenarios.append({
                "scenario":   scenario,
                "config":     config_label,
                "jira":       "",
                "jira_url":   "#",
                "test_cases": test_cases,
            })

        return scenarios

    # ── Performance ───────────────────────────────────────────────────────────

    def _collect_performance(self, client, driver: str, config: dict, prof: Profiler) -> list[ModelPerformance]:
        current_build = str(config.get("build", ""))
        ref_build     = str(config.get("ref_build", ""))
        limit         = int(config.get("perf_history_limit", 8))

        with prof.span("perf_query_s"):
            rows = self._execute(client, driver, _PERF_SQL, [limit])
        if not rows:
            log.info("clickhouse: no rows in performance_metrics")
            return []

        return _build_performance(rows, current_build, ref_build, limit)
