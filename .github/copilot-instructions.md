# GitHub Copilot Instructions — ci-report

This file is loaded automatically by GitHub Copilot to provide project
context and coding guidance. Follow these instructions for all suggestions
and completions in this repository.

---

## Project overview

**ci-report** generates a single-file HTML CI dashboard from database data
sources. It reads test failure results and performance metrics from MySQL
and/or ClickHouse, optionally enriches them with JIRA tickets and Claude AI
analysis, then renders a Dracula-themed HTML report via Jinja2.

```bash
python generate_report.py --mysql --clickhouse --jira --ai 1247
```

The `1247` argument is the build name. It is substituted into every `{build}`
placeholder in `config.json` before any collection begins.

---

## Key files

| File | Purpose |
|---|---|
| `generate_report.py` | Main orchestrator, CLI, `prepare_data()`, `merge_results()` |
| `datasources/base.py` | `DataSource` ABC, `Profiler`, schema TypedDicts, `merge_results()` |
| `datasources/transports.py` | DB connection + query mixins (`MySQLTransport`, `ClickHouseTransport`) |
| `datasources/contract.py` | Shared row→contract builders (`_fmt_dur`, `_build_performance`, …) |
| `datasources/logs.py` | `LogFetcher` — parallel HTTP log download + profiling |
| `datasources/tool_mysql.py` | MySQL data source — schema-specific SQL + mapping |
| `datasources/tool_clickhouse.py` | ClickHouse data source — schema-specific SQL + mapping |
| `datasources/tool_template.py` | Scaffold — copy this to add a new tool |
| `template.html` | Jinja2 template, Dracula theme, fully self-contained |
| `tests/test_compute.py` | Unit tests for computation helpers |
| `tests/test_tools.py` | Contract tests for tools and enrichment pipeline |
| `examples/fixtures/` | SQL schemas + 8-build fixture data for both DBs |

---

## The DataSource pattern

Every tool subclasses `DataSource` (from `datasources/base.py`) **plus a DB
transport mixin** (from `datasources/transports.py`), and assembles the contract
with the shared builders in `datasources/contract.py`. A tool holds only
schema-specific SQL + row mapping:

```python
from .base import DataSource
from .transports import MySQLTransport

class MySQLSource(DataSource, MySQLTransport):

    @property
    def name(self) -> str:
        return "mysql"           # matches TOOLS key and config.json section

    def collect(self, config: dict) -> dict:
        ...
        return {
            "failures":    [...],   # always return both
            "performance": [...],   # even if empty
        }
```

The transport provides `_connect()` + `_query()`/`_execute()`; `contract.py`
provides `_fmt_dur`, `_history_list`, `_build_performance`, `_safe`. This lets two
frameworks with different schemas share a driver and the contract-construction
code while keeping their own SQL. Never re-implement the contract builders in a
tool — import them.

Tools are registered in `generate_report.py`:

```python
TOOLS: dict = {
    "mysql":      MySQLSource(),
    "clickhouse": ClickHouseSource(),
}
```

`argparse` auto-generates `--mysql` and `--clickhouse` CLI flags from these keys.
Tools run in parallel via `ThreadPoolExecutor`.

---

## collect() output — required JSON shape

`collect()` must return this exact structure. The Jinja2 template and
`prepare_data()` depend on every key listed here.

### failures

```python
[
  {
    "scenario":  str,         # test suite name
    "config":    str,         # environment / agent label
    "jira":      str,         # "PROJ-123" or ""
    "jira_url":  str,         # full URL or "#"
    "test_cases": [
      {
        "name":            str,
        "status":          str,   # MUST be "fail" | "error" | "timeout"
        "duration":        str,   # "18.1s" | "3m 12s" | "—"
        "failure_message": str,
        "failure_text":    str,
        "jira":            str,
        "jira_url":        str,
        "task_url":        str,
        "log_url":         str,
        "log_file":        str,   # optional — local path to a fetched log (AI reads it)
        "history": [
          {"build": str, "status": str, "duration": str},
          # last entry MUST have "current": True
        ],
        "ai_analysis": {},        # always a dict — AI phase fills it in
      }
    ]
  }
]
```

### performance

```python
[
  {
    "model":         str,     # display name — discovered from DB, not config
    "summary_note":  str,     # footer text or ""
    "summary_chips": None,    # None = auto-derive; or list of chip dicts
    "metrics": [
      {
        "name":           str,
        "unit":           str,
        "direction":      str,   # MUST be "higher_better" | "lower_better"
        "current":        float,
        "reference":      float,
        "history_values": [float, ...],
        "history_builds": [str, ...],   # MUST be same length as history_values
      }
    ]
  }
]
```

### Hard rules

- `history_values` and `history_builds` must always be the same length.
- `direction` must be exactly `"higher_better"` or `"lower_better"`.
- `status` must be exactly `"fail"`, `"error"`, or `"timeout"`.
- `ai_analysis` must always be a `dict` — never `None`.
- The last `history` entry must always have `"current": True`.
- `collect()` must **never raise** — catch everything, log it, return `{}`.

---

## SQL conventions

Use `%s` placeholders in all SQL. The ClickHouse `_execute()` adapter
converts `%s` → `{_p0}`, `{_p1}`, … for clickhouse-connect automatically —
tools never need to do this themselves.

```python
# Correct — one %s per parameter, positional
rows = self._query(conn, "SELECT * FROM t WHERE build = %s", (build,))

# Wrong — string formatting opens SQL injection
rows = self._query(conn, f"SELECT * FROM t WHERE build = '{build}'")
```

**Always batch history queries.** One `IN (…)` query for all failing test
names; never one query per test:

```python
# Correct — one query for N tests
ph   = ', '.join(['%s'] * len(names))
sql  = f"SELECT ... FROM test_runs WHERE name IN ({ph}) AND build != %s"
rows = self._query(conn, sql, list(names) + [build])

# Wrong — N+1 queries
for name in names:
    rows = self._query(conn, "SELECT ... WHERE name = %s", (name,))
```

---

## Database helpers

### MySQL

```python
# _connect() tries PyMySQL first, then mysql-connector-python
conn, driver = self._connect(host, port, database, user, password)
# _query() handles both drivers transparently
rows = self._query((conn, driver), sql, params)
```

### ClickHouse

```python
# _connect() tries clickhouse-driver (TCP 9000) first, then clickhouse-connect (HTTP 8123)
client, driver = self._connect(host, port, database, user, password)
# _execute() handles both drivers and converts %s params for clickhouse-connect
rows = self._execute(client, driver, sql, [build])
```

---

## Coding style

- Python 3.11+. Use `from __future__ import annotations` in all source files.
- Type hints on all function signatures. Use built-in generics: `list[str]`,
  `dict[str, int]` — not `typing.List`, `typing.Dict`.
- No bare `except:`. Catch the narrowest exception or `Exception` with a log.
- No `print()` in `datasources/` or enricher modules — use
  `logging.getLogger(__name__)`.
- `generate_report.py` and `generate_sample.py` may use `print()` for
  user-facing progress. Always call
  `sys.stdout.reconfigure(encoding="utf-8", errors="replace")` near the top
  for Windows terminal compatibility.
- SQL constants: `_UPPER_SNAKE_SQL` at module level.
- Private helpers: `_lower_snake()` at module level.
- Do not shadow the `config` dict parameter — use `config_label` for loop
  variables unpacking DB `config` column values.

### Comments

Write comments only when the *why* is non-obvious. Do not describe what the
code does; well-named identifiers already do that. Do not reference issue
numbers or task names in comments.

---

## Adding a new data source tool

1. Copy `datasources/tool_template.py` → `datasources/tool_<name>.py`
2. Implement `name`, `description`, `collect()`
3. Register in `generate_report.py`:
   ```python
   from datasources.tool_<name> import <Name>Source
   TOOLS["<name>"] = <Name>Source()
   ```
4. Add `"<name>": { ... }` block to `examples/config.json`
5. Add fixture SQL to `examples/fixtures/<name>_schema.sql`
6. Add contract tests to `tests/test_tools.py` (see below)
7. Update `README.md` — project structure, CLI reference, DB tools section

---

## Testing

### Run tests

```bash
pytest tests/ -v --tb=short
```

All tests must pass before committing.

### Test structure

| File | What it covers |
|---|---|
| `tests/test_compute.py` | `normalize_heights`, `compute_delta`, `history_summary`, `validate_merged`, `resolve_build`, JIRA term extraction |
| `tests/test_tools.py` | Helper functions, `_build_performance`, `merge_results`, full `collect()` contract for both tools, `_execute` adapter, `prepare_data` pipeline |

### Contract tests pattern

Mock at the `_query` / `_execute` level — never at the connection level.
This keeps the data-transformation logic under test:

```python
def _mysql_query_router(conn_pair, sql, params=()):
    if "performance_metrics" in sql:
        return PERF_ROWS
    if "build !=" in sql:
        return HISTORY_ROWS
    return FAILURE_ROWS

class TestMyNewSourceContract:

    def _collect(self):
        src = MyNewSource()
        with patch.object(MyNewSource, "_connect", return_value=(MagicMock(), "driver")), \
             patch.object(MyNewSource, "_query", side_effect=_mysql_query_router):
            return src.collect({"database": "ci", "build": "1247"})

    def test_always_returns_failures_and_performance_keys(self):
        result = self._collect()
        assert "failures" in result
        assert "performance" in result

    def test_metric_required_keys(self):
        metric = self._collect()["performance"][0]["metrics"][0]
        for key in ("name", "unit", "direction", "current", "reference",
                    "history_values", "history_builds"):
            assert key in metric

    def test_history_values_and_builds_same_length(self):
        for model in self._collect()["performance"]:
            for m in model["metrics"]:
                assert len(m["history_values"]) == len(m["history_builds"])

    def test_missing_build_returns_empty(self):
        src = MyNewSource()
        with patch.object(MyNewSource, "_connect", return_value=(MagicMock(), "driver")):
            assert src.collect({"database": "ci", "build": ""}) == {}

    def test_connect_failure_returns_empty(self):
        src = MyNewSource()
        with patch.object(MyNewSource, "_connect", side_effect=RuntimeError("no driver")):
            assert src.collect({"database": "ci", "build": "1247"}) == {}
```

### Non-regression checklist

Before committing any change:

- [ ] `pytest tests/ -v` — all tests pass
- [ ] If `collect()` output shape changed → update contract tests
- [ ] If `template.html` or `prepare_data()` changed →
      `python generate_sample.py --out examples/sample_report.html` renders
      without errors
- [ ] If a tool, module, or config key was removed →
      `grep -r "old_name" --include="*.py" --include="*.json" --include="*.md" .`
      returns no matches

---

## Profiling

Every tool's `collect()` must record timing spans and include a `"profiling"` key in its return dict. The data flows into the `.data.json` file so agents can read it and identify bottlenecks.

### Return shape

```python
return {
    "failures":    [...],
    "performance": [...],
    "profiling": {
        "tool":    self.name,          # str — tool key, e.g. "mysql"
        "total_s": 4.20,              # float — full collect() wall time
        "spans": {                    # dict — per-operation times in seconds
            "connect_s":        0.12,
            "failures_query_s": 0.84,
            "history_query_s":  2.90,
            "perf_query_s":     0.34,
            "log_fetch_s":      1.90,  # only when fetch_logs is on
        },
        # "logs": {...}               # detailed download profiling — see below
    },
}
```

### Pattern to follow

```python
from .base import DataSource, Profiler
import time

def collect(self, config: dict) -> dict:
    prof    = Profiler()
    t_start = time.perf_counter()

    with prof.span("connect_s"):
        conn = self._connect(...)

    failures    = self._collect_failures(conn, config, prof)
    performance = self._collect_performance(conn, config, prof)

    return {
        "failures":    failures,
        "performance": performance,
        "profiling": {
            "tool":    self.name,
            "total_s": round(time.perf_counter() - t_start, 3),
            "spans":   prof.to_dict(),
        },
    }

def _collect_failures(self, conn, config: dict, prof: Profiler) -> list:
    with prof.span("failures_query_s"):
        rows = self._query(conn, _FAILURES_SQL, (build,))
    with prof.span("history_query_s"):
        hist = self._query(conn, hist_sql, params)
    ...

def _collect_performance(self, conn, config: dict, prof: Profiler) -> list:
    with prof.span("perf_query_s"):
        rows = self._query(conn, _PERF_SQL, ())
    ...
```

### Rules

- Always use `Profiler` from `datasources/base.py` — no ad-hoc timing dicts.
- Span names: `lower_snake_case` ending in `_s`.
- Pass `prof` as the last argument to every private helper that runs queries.
- `collect()` must still return `{}` on error — never a partial dict.
- Do not add profiling inside `merge_results()`.

### Read-only DB access — optimisation constraints

Production databases are **read-only**. Agents and Copilot suggestions must
never propose schema changes. The only levers available are:

1. **Query rewriting** — push filters into SQL to reduce rows transferred.
   Assume the schema and indexes are fixed. Write queries that benefit from
   whatever indexes already exist (inspect `examples/fixtures/` for the schema).
2. **Config tuning** — lower `history_limit` or `perf_history_limit` in
   `config.json` to request less data.
3. **Python processing** — avoid redundant loops; never re-query data already
   fetched in the same `collect()` call.

When profiling shows a slow span, map it to one of the three levers. If the
root cause is a missing index that can't be compensated by query rewriting,
document the finding as a human-DBA recommendation — do not attempt to create it.

| Span | Lever |
|---|---|
| `perf_query_s` high | Rewrite: add build-window subquery against `recorded_at` |
| `history_query_s` high | Config: lower `history_limit`; or rewrite with date range if builds are numeric |
| `failures_query_s` high | Rewrite: ensure `WHERE build = %s` can use an existing index |
| `connect_s` high | Not fixable from code; document for ops team |

---

## Pipeline efficiency

### Implemented optimisations

| Area | Technique |
|---|---|
| Tool collection | Parallel `ThreadPoolExecutor` — wall time = slowest tool |
| `_PERF_SQL` | Build-window subquery filters rows server-side |
| History query | Batched `IN` clause — one query for all failing test names |
| `failure_txt` transfer | `LEFT(failure_txt, 8192)` (MySQL) / `substring(..., 1, 8192)` (ClickHouse) |
| JIRA enrichment | Parallel HTTP via `ThreadPoolExecutor` (`parallel_requests`, default 8) |
| AI analysis | Parallel LLM calls via `ThreadPoolExecutor` (`parallel_requests`, default 4) |
| Phase replay | `.data.json` written after each phase; re-runs skip earlier phases |

### Parallelism rules

- **JIRA `enrich()`:** uses `ThreadPoolExecutor`. Dry-run mode stays sequential
  so print lines are not interleaved. `parallel_requests=8` is safe under JIRA
  Cloud's 10 req/s rate limit.
- **AI `enrich()`:** uses `ThreadPoolExecutor`. All `save_fn` calls are
  serialised under `threading.Lock` — never call `save_fn` outside the lock.
  Tune `parallel_requests` to your API tier's RPM limit.
- **New enrichers** that use `ThreadPoolExecutor` must hold a `threading.Lock`
  around every cross-thread state write. Never mutate `report_data` outside
  the lock.
- **Log fetching:** `attach_logs()` (`datasources/logs.py`) downloads per-test-case
  logs in parallel when `fetch_logs` is set, sets `tc["log_file"]`, and returns a
  `profiling.logs` block. Workers return per-file records, so aggregation is
  lock-free. Use HTTP `Range` (`log_tail_bytes`) and on-disk caching
  (`log_skip_if_present`) to cut transfer.

### Truncation constants (do not make these config keys)

| File | Constant | Value | Purpose |
|---|---|---|---|
| `datasources/tool_mysql.py` | `LEFT(failure_txt, 8192)` in `_FAILURES_SQL` | 8 KB | Reduce wire transfer |
| `datasources/tool_clickhouse.py` | `substring(failure_txt, 1, 8192)` in `_FAILURES_SQL` | 8 KB | Same |
| `ai_analyser.py` | `text[:2000]` in `build_prompt()` | 2 000 chars | Prompt token reduction |
| `ai_analyser.py` | `content[:4000]` in `_load_file()` | 4 000 chars | Context file cap |

### Config knobs (no code change needed)

| Section | Key | Default | Lowering reduces |
|---|---|---|---|
| `mysql` / `clickhouse` | `history_limit` | 7 | `history_query_s` |
| `mysql` / `clickhouse` | `perf_history_limit` | 8 | `perf_query_s` |
| `jira` | `parallel_requests` | 8 | Concurrent HTTP calls |
| `ai` | `parallel_requests` | 4 | Concurrent LLM calls |
| `jira` / `ai` | `skip_if_present` | true | Re-enrichment of already-done cases |
| `mysql` / `clickhouse` | `log_parallel_requests` | 8 | Concurrent log downloads (with `fetch_logs`) |
| `mysql` / `clickhouse` | `log_tail_bytes` | 0 | Bytes per log (0 = full); set to fetch only the tail |
| `mysql` / `clickhouse` | `log_skip_if_present` | true | Re-download of cached logs |

### Log download profiling

When `fetch_logs` is set, the tool's `profiling.logs` block reports parallel
download stats. Log fetching is client-side, so every lever is available:

| Block shows | Lever |
|---|---|
| `speedup` ≈ `workers` | raise `log_parallel_requests` |
| `speedup` ≪ `workers` | server throttling — lower workers / respect rate limit |
| `connect_sum_s` ≫ `transfer_sum_s` | reuse a keep-alive session (`requests.Session`) |
| high `p95`/`max` vs `p50` | a few large logs — set `log_tail_bytes` (HTTP Range) |
| `throughput_mb_s` near link cap | bandwidth-bound — gzip / fetch less |
| `cached` low on replay | keep `log_skip_if_present: true` |

---

## What Copilot should not suggest

- Do not add `models` as a config key — models are always discovered from
  database data, never configured.
- Do not add per-feature toggles to `collect()` — tools are full data source
  providers; when enabled they always return both `failures` and `performance`.
- Do not issue one DB query per test case for history — always batch with `IN`.
- Do not use f-strings to build SQL — always use `%s` placeholders.
- Do not add `print()` to `datasources/` modules — use `logging`.
- Do not catch `Exception` silently without logging it.
- Do not suggest `CREATE INDEX`, `ALTER TABLE`, `ANALYZE TABLE`, or any DDL —
  the agent has read-only access; all such suggestions must go to a human DBA.
- Do not suggest writing query results to DB tables or creating views — read-only.
- Do not return `None` from `collect()` — return `{}` on error.
- Do not hard-code build numbers, hostnames, or file paths in source files.
- Do not add external CDN links to `template.html` — keep it self-contained.
- Do not add JavaScript to `template.html` — the report is HTML + CSS only.
  No `<script>` tags, no inline event handlers (`onclick`, `onload`, …), and no
  `javascript:` URLs. Use pure CSS (`:hover`, `:target`, `<details>`/`<summary>`)
  for any interactivity; the report must work with JavaScript disabled.
