"""
datasources/tool_clickhouse.py
───────────────────────────────
Full data source: reads all test failures and all performance metrics from
ClickHouse and returns the complete standard output expected by the HTML template.

When this tool is enabled (--clickhouse flag), it always provides both:
  • failures    — all failing test cases for this build, with history strips
  • performance — all models and metrics found in performance_metrics,
                  grouped by model, for the last perf_history_limit builds

The DB schema drives what gets reported — no per-table toggles in config.

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
"""

from __future__ import annotations

import logging
import os
import re
from collections import defaultdict

from .base import DataSource

log = logging.getLogger(__name__)

_SAFE_RE = re.compile(r'[^\w\-]')

# ── SQL ───────────────────────────────────────────────────────────────────────

_FAILURES_SQL = """
    SELECT scenario, config, name, status, duration_s,
           failure_msg, failure_txt, jira, jira_url, task_url, log_url
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

# All performance data — models and build window discovered in Python
_PERF_SQL = """
    SELECT model, metric_name, unit, direction, build, value
    FROM   performance_metrics
    ORDER  BY model, metric_name, recorded_at ASC
"""

# ── Helpers (identical contract to tool_mysql.py) ────────────────────────────

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
    result: dict = {}
    for row in rows:
        name    = str(row["name"])
        entries = result.setdefault(name, [])
        if len(entries) < limit:
            entries.append(row)
    return result


def _history_list(hist_entries: list, cur_status: str, cur_dur: str) -> list:
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
    """
    groups: dict     = defaultdict(list)
    all_builds_seen: set = set()

    for row in rows:
        key = (str(row["model"]), str(row["metric_name"]))
        groups[key].append(row)
        all_builds_seen.add(str(row["build"]))

    window     = set(_sort_builds(all_builds_seen)[-limit:])
    models_ord = list(dict.fromkeys(str(r["model"]) for r in rows))

    performance = []
    for model_name in models_ord:
        model_keys = [k for k in groups if k[0] == model_name]
        if not model_keys:
            continue

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

class ClickHouseSource(DataSource):

    @property
    def name(self) -> str:
        return "clickhouse"

    @property
    def description(self) -> str:
        return ("Full data source: reads all test failures and performance metrics "
                "from ClickHouse (requires clickhouse-driver or clickhouse-connect)")

    # ── Entry point ───────────────────────────────────────────────────────────

    def collect(self, config: dict) -> dict:
        host     = config.get("host", "localhost")
        port     = int(config.get("port", 9000))
        database = config.get("database", "default")
        user     = os.environ.get(config.get("user_env",     "CLICKHOUSE_USER"), "default")
        password = os.environ.get(config.get("password_env", "CLICKHOUSE_PASSWORD"), "")
        build    = str(config.get("build", ""))

        if not build:
            log.error('clickhouse: "build" key not set — add "build": "{build}" to config')
            return {}

        try:
            client, driver = self._connect(host, port, database, user, password)
        except Exception as exc:
            log.error("clickhouse: cannot connect — %s", exc)
            return {}

        try:
            return {
                "failures":    self._collect_failures(client, driver, config),
                "performance": self._collect_performance(client, driver, config),
            }
        except Exception as exc:
            log.error("clickhouse: collection failed — %s", exc)
            return {}

    # ── Failures ──────────────────────────────────────────────────────────────

    def _collect_failures(self, client, driver: str, config: dict) -> list:
        build     = str(config["build"])
        limit     = int(config.get("history_limit", 7))
        jira_base = config.get("jira_base_url", "")
        task_base = config.get("task_base_url", "")
        log_base  = config.get("log_base_url",  "")

        rows = self._execute(client, driver, _FAILURES_SQL, [build])
        if not rows:
            log.info("clickhouse: no failures for build %s", build)
            return []

        names    = list({r["name"] for r in rows})
        hist_sql = _BATCH_HISTORY_SQL.format(ph=', '.join(['%s'] * len(names)))
        hist_map = _group_history(
            self._execute(client, driver, hist_sql, list(names) + [build]),
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

    def _collect_performance(self, client, driver: str, config: dict) -> list:
        current_build = str(config.get("build", ""))
        ref_build     = str(config.get("ref_build", ""))
        limit         = int(config.get("perf_history_limit", 8))

        rows = self._execute(client, driver, _PERF_SQL, [])
        if not rows:
            log.info("clickhouse: no rows in performance_metrics")
            return []

        return _build_performance(rows, current_build, ref_build, limit)

    # ── DB helpers ────────────────────────────────────────────────────────────

    def _connect(self, host: str, port: int, database: str, user: str, password: str):
        try:
            from clickhouse_driver import Client
            client = Client(
                host=host, port=port, database=database,
                user=user, password=password,
                connect_timeout=10,
            )
            return client, 'driver'
        except ImportError:
            pass

        try:
            import clickhouse_connect
            client = clickhouse_connect.get_client(
                host=host, port=8123, database=database,
                username=user, password=password,
                connect_timeout=10,
            )
            return client, 'connect'
        except ImportError:
            raise RuntimeError(
                "No ClickHouse driver found. Install one:\n"
                "  pip install clickhouse-driver\n"
                "  pip install clickhouse-connect"
            )

    def _execute(self, client, driver: str, sql: str, params: list = None) -> list[dict]:
        """Execute a query and return rows as dicts, normalising both driver APIs."""
        params = list(params or [])
        if driver == 'driver':
            rows, col_types = client.execute(sql, params, with_column_types=True)
            col_names = [c[0] for c in col_types]
            return [dict(zip(col_names, row)) for row in rows]
        else:
            # clickhouse-connect uses {name} style params — convert %s positionally
            idx = [0]
            def _repl(m):
                key = f"_p{idx[0]}"
                idx[0] += 1
                return f"{{{key}}}"
            named_sql  = re.sub(r'%s', _repl, sql)
            param_dict = {f"_p{i}": v for i, v in enumerate(params)}
            result     = client.query(named_sql, parameters=param_dict)
            return [dict(zip(result.column_names, row)) for row in result.result_rows]
