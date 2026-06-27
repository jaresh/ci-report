"""
datasources/tool_mysql.py
──────────────────────────
Full data source: reads all test failures and all performance metrics from
a MySQL / MariaDB database and returns the complete standard output expected
by the HTML template.

When this tool is enabled (--mysql flag), it always provides both:
  • failures    — all failing test cases for this build, with history strips
  • performance — all models and metrics found in performance_metrics,
                  grouped by model, for the last perf_history_limit builds

The DB schema drives what gets reported — no per-table toggles in config.

Schema and fixture data: examples/fixtures/mysql_schema.sql

Dependencies (install one):
    pip install PyMySQL
    pip install mysql-connector-python

Config keys
───────────
host              str    DB host                                [default: localhost]
port              int    DB port                                [default: 3306]
database          str    database / schema name                 [required]
user_env          str    env var holding the DB user            [default: MYSQL_USER]
password_env      str    env var holding the DB password        [default: MYSQL_PASSWORD]
build             str    current build identifier — use "{build}"   [required]
ref_build         str    reference build for metric delta       [optional]
history_limit     int    prior builds in failure history strip  [default: 7]
perf_history_limit int   builds shown in performance chart      [default: 8]
jira_base_url     str    base URL for JIRA links                [optional]
task_base_url     str    base URL for task links                [optional]
log_base_url      str    base URL for log links                 [optional]
"""

from __future__ import annotations

import logging
import os
import re
import time
from collections import defaultdict

from .base import DataSource, Profiler

log = logging.getLogger(__name__)

_SAFE_RE = re.compile(r'[^\w\-]')

# ── SQL ───────────────────────────────────────────────────────────────────────

_FAILURES_SQL = """
    SELECT scenario, config, name, status, duration_s,
           failure_msg, LEFT(failure_txt, 8192) AS failure_txt,
           jira, jira_url, task_url, log_url
    FROM   test_runs
    WHERE  build = %s
      AND  status IN ('fail', 'error', 'timeout')
    ORDER  BY scenario, name
"""

# One query for all failing test names; Python caps at history_limit per name
_BATCH_HISTORY_SQL = """
    SELECT build, name, status, duration_s
    FROM   test_runs
    WHERE  name IN ({ph})
      AND  build != %s
    ORDER  BY name, ran_at DESC
"""

# Fetch only the last perf_history_limit distinct builds so the query never
# does a full table scan as the performance_metrics table grows over time.
# The subquery uses idx_recorded_at; the outer query can then use idx_model_build.
_PERF_SQL = """
    SELECT model, metric_name, unit, direction, build, value
    FROM   performance_metrics
    WHERE  build IN (
        SELECT build FROM (
            SELECT DISTINCT build
            FROM   performance_metrics
            ORDER  BY recorded_at DESC
            LIMIT  %s
        ) AS _recent_builds
    )
    ORDER  BY model, metric_name, recorded_at ASC
"""

# ── Helpers ───────────────────────────────────────────────────────────────────

def _safe(name: str) -> str:
    return _SAFE_RE.sub('_', name)[:80]


def _fmt_dur(secs) -> str:
    try:
        s = float(secs or 0)
    except (TypeError, ValueError):
        return "—"
    if s <= 0:
        return "—"
    if s >= 60:
        m, r = divmod(s, 60)
        return f"{int(m)}m {r:.0f}s"
    return f"{s:.1f}s"


def _sort_builds(builds) -> list:
    try:
        return sorted(builds, key=int)
    except (ValueError, TypeError):
        return sorted(builds)


def _group_history(rows: list, limit: int) -> dict:
    """Return {name: [rows DESC, capped at limit]} from a batch history query."""
    result: dict = {}
    for row in rows:   # already ordered (name, ran_at DESC) by the query
        name    = str(row["name"])
        entries = result.setdefault(name, [])
        if len(entries) < limit:
            entries.append(row)
    return result


def _history_list(hist_entries: list, cur_status: str, cur_dur: str) -> list:
    """Convert DESC-ordered history rows to a chronological list + current entry."""
    entries = []
    for row in reversed(hist_entries):
        entries.append({
            "build":    str(row["build"]),
            "status":   str(row["status"]),
            "duration": _fmt_dur(row.get("duration_s")),
        })
    entries.append({"build": "current", "status": cur_status, "duration": cur_dur, "current": True})
    return entries


def _build_performance(rows: list, current_build: str, ref_build: str, limit: int) -> list:
    """
    Group flat performance rows into the per-model structure the template expects.
    Models and their order are discovered from the data — no config list needed.
    The global build window (last `limit` builds across all models) is computed
    once so all models share the same x-axis.
    """
    groups: dict      = defaultdict(list)
    all_builds_seen: set = set()

    for row in rows:
        key = (str(row["model"]), str(row["metric_name"]))
        groups[key].append(row)
        all_builds_seen.add(str(row["build"]))

    # Shared x-axis: last `limit` builds across the whole table
    window     = set(_sort_builds(all_builds_seen)[-limit:])
    # Models in the order they first appear in the query result
    models_ord = list(dict.fromkeys(str(r["model"]) for r in rows))

    performance = []
    for model_name in models_ord:
        model_keys = [k for k in groups if k[0] == model_name]
        if not model_keys:
            continue

        # Builds that exist for this model within the window
        model_window_builds = _sort_builds(
            {str(r["build"]) for key in model_keys for r in groups[key]} & window
        )
        build_window_set = set(model_window_builds)

        metrics = []
        for key in model_keys:
            metric_name  = key[1]
            filtered     = [r for r in groups[key] if str(r["build"]) in build_window_set]
            build_to_val = {str(r["build"]): float(r["value"]) for r in filtered}
            history_values = [build_to_val.get(b, 0.0) for b in model_window_builds]

            current   = build_to_val.get(str(current_build))
            if current is None:
                current = history_values[-1] if history_values else 0.0
            reference = build_to_val.get(str(ref_build), current)

            if not filtered:
                continue

            sample = filtered[0]
            metrics.append({
                "name":           metric_name,
                "unit":           str(sample["unit"]),
                "direction":      str(sample["direction"]),
                "current":        current,
                "reference":      reference,
                "history_values": history_values,
                "history_builds": model_window_builds,
            })

        if not metrics:
            continue

        performance.append({
            "model":         model_name,
            "summary_note":  "",
            "summary_chips": None,
            "metrics":       metrics,
        })

    return performance


# ── Source class ──────────────────────────────────────────────────────────────

class MySQLSource(DataSource):

    @property
    def name(self) -> str:
        return "mysql"

    @property
    def description(self) -> str:
        return ("Full data source: reads all test failures and performance metrics "
                "from MySQL / MariaDB (requires PyMySQL or mysql-connector-python)")

    # ── Entry point ───────────────────────────────────────────────────────────

    def collect(self, config: dict) -> dict:
        host     = config.get("host", "localhost")
        port     = int(config.get("port", 3306))
        database = config.get("database", "")
        user     = os.environ.get(config.get("user_env",     "MYSQL_USER"), "")
        password = os.environ.get(config.get("password_env", "MYSQL_PASSWORD"), "")
        build    = str(config.get("build", ""))

        if not database:
            log.error('mysql: "database" key missing from config')
            return {}
        if not build:
            log.error('mysql: "build" key not set — add "build": "{build}" to config')
            return {}

        prof   = Profiler()
        t_start = time.perf_counter()

        try:
            with prof.span("connect_s"):
                conn_pair = self._connect(host, port, database, user, password)
        except Exception as exc:
            log.error("mysql: cannot connect — %s", exc)
            return {}

        try:
            failures    = self._collect_failures(conn_pair, config, prof)
            performance = self._collect_performance(conn_pair, config, prof)
            return {
                "failures":    failures,
                "performance": performance,
                "profiling": {
                    "tool":    self.name,
                    "total_s": round(time.perf_counter() - t_start, 3),
                    "spans":   prof.to_dict(),
                },
            }
        except Exception as exc:
            log.error("mysql: collection failed — %s", exc)
            return {}
        finally:
            conn_pair[0].close()

    # ── Failures ──────────────────────────────────────────────────────────────

    def _collect_failures(self, conn_pair, config: dict, prof: Profiler) -> list:
        build     = str(config["build"])
        limit     = int(config.get("history_limit", 7))
        jira_base = config.get("jira_base_url", "")
        task_base = config.get("task_base_url", "")
        log_base  = config.get("log_base_url",  "")

        with prof.span("failures_query_s"):
            rows = self._query(conn_pair, _FAILURES_SQL, (build,))
        if not rows:
            log.info("mysql: no failures for build %s", build)
            return []

        names    = list({r["name"] for r in rows})
        hist_sql = _BATCH_HISTORY_SQL.format(ph=', '.join(['%s'] * len(names)))
        with prof.span("history_query_s"):
            hist_map = _group_history(
                self._query(conn_pair, hist_sql, list(names) + [build]),
                limit,
            )

        groups: dict = {}
        for row in rows:
            key = (str(row["scenario"]), str(row["config"]))
            groups.setdefault(key, []).append(row)

        scenarios = []
        for (scenario, config_label), tcs in groups.items():
            test_cases = []
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

    def _collect_performance(self, conn_pair, config: dict, prof: Profiler) -> list:
        current_build = str(config.get("build", ""))
        ref_build     = str(config.get("ref_build", ""))
        limit         = int(config.get("perf_history_limit", 8))

        with prof.span("perf_query_s"):
            rows = self._query(conn_pair, _PERF_SQL, (limit,))
        if not rows:
            log.info("mysql: no rows in performance_metrics")
            return []

        return _build_performance(rows, current_build, ref_build, limit)

    # ── DB helpers ────────────────────────────────────────────────────────────

    def _connect(self, host: str, port: int, database: str, user: str, password: str):
        try:
            import pymysql
            conn = pymysql.connect(
                host=host, port=port, db=database,
                user=user, password=password,
                charset='utf8mb4',
                cursorclass=pymysql.cursors.DictCursor,
                connect_timeout=10,
            )
            return conn, 'pymysql'
        except ImportError:
            pass

        try:
            import mysql.connector
            conn = mysql.connector.connect(
                host=host, port=port, database=database,
                user=user, password=password,
                charset='utf8mb4',
                connection_timeout=10,
            )
            return conn, 'connector'
        except ImportError:
            raise RuntimeError(
                "No MySQL driver found. Install one:\n"
                "  pip install PyMySQL\n"
                "  pip install mysql-connector-python"
            )

    def _query(self, conn_pair, sql: str, params=()):
        conn, driver = conn_pair
        if driver == 'pymysql':
            with conn.cursor() as cur:
                cur.execute(sql, params)
                return cur.fetchall()
        else:
            cur = conn.cursor(dictionary=True)
            try:
                cur.execute(sql, params)
                return cur.fetchall()
            finally:
                cur.close()
