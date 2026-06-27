# CLAUDE.md — Agent Guide for ci-report

This file is read automatically by Claude Code at the start of every session.
It tells AI agents what this project does, how it is structured, what the
invariants are, and how to implement and test changes without introducing
regressions.

---

## What this project is

**ci-report** is a self-contained CI dashboard report generator.

Given one or more database data sources, it:

1. **Collects** test failure results and performance metrics from live databases
   (MySQL / MariaDB, ClickHouse — one or both per run).
2. **Enriches** failures with related JIRA tickets (optional `--jira` phase).
3. **Analyses** failures with Claude AI (optional `--ai` phase).
4. **Renders** everything into a single-file, Dracula-themed HTML dashboard via
   a Jinja2 template.

The output is one self-contained `.html` file — no server, no JS bundle, no
external assets. It is designed to be attached to a CI notification or hosted
on a static page.

Typical invocation:

```bash
python generate_report.py --mysql --clickhouse --jira --ai 1247
```

Build `1247` is resolved into every `{build}` placeholder in `config.json`
at runtime.

---

## Repository layout

```
generate_report.py        Main orchestrator + CLI
generate_sample.py        Generates synthetic sample_report.html (no DB needed)
ai_analyser.py            Standalone AI analysis phase
jira_enricher.py          Standalone JIRA enrichment phase
template.html             Jinja2 HTML template (Dracula theme, self-contained)
config.json               User config (gitignored — copy from examples/)

datasources/
  base.py                 DataSource ABC + merge_results()
  tool_mysql.py           MySQL / MariaDB full data source
  tool_clickhouse.py      ClickHouse full data source
  tool_template.py        Scaffold for new data source tools

tests/
  test_compute.py         Pure-computation unit tests (normalize_heights, deltas, …)
  test_tools.py           Data source contract tests + enrichment pipeline tests

examples/
  config.json             Example config (copy to root and edit)
  example_run.json        Example --config mode run spec
  context.txt             Example AI prompt context file
  example_data.json       Pre-collected sample data JSON
  sample_report.html      Pre-rendered dashboard (regenerate with generate_sample.py)
  fixtures/
    mysql_schema.sql      MySQL schema + 8-build fixture data
    clickhouse_schema.sql ClickHouse schema + 8-build fixture data
```

---

## Architecture

### Four-phase pipeline

```
┌─────────────┐   ┌──────────┐   ┌──────────┐   ┌────────┐
│  Collection │──▶│   JIRA   │──▶│    AI    │──▶│ Render │
│  --mysql    │   │  --jira  │   │   --ai   │   │        │
│  --clickhouse│  │          │   │          │   │        │
└─────────────┘   └──────────┘   └──────────┘   └────────┘
```

Each phase writes `<build>.data.json` so any phase can be re-run
independently without repeating earlier ones.

### Data source tools

Every tool in `datasources/` subclasses `DataSource` and implements one method:

```python
def collect(self, config: dict) -> dict
```

`collect()` always returns the **same JSON shape** regardless of which
database or schema the tool reads. This is the central contract the template
depends on. See the next section.

Tools run in parallel via `ThreadPoolExecutor`. Their outputs are concatenated
by `merge_results()` in `datasources/base.py`.

### Config resolution

`{build}` in any config string value is replaced with the build name at
runtime by `resolve_build()` before `collect()` is called. Tools must not
resolve it themselves.

---

## The JSON output contract (critical invariant)

`collect()` must return a dict with these keys:

```python
{
  "failures": [
    {
      "scenario":  str,          # suite / scenario name
      "config":    str,          # environment label
      "jira":      str,          # JIRA ticket key or ""
      "jira_url":  str,          # full URL or "#"
      "test_cases": [
        {
          "name":            str,
          "status":          str,   # "fail" | "error" | "timeout"
          "duration":        str,   # "18.1s" | "3m 12s" | "—"
          "failure_message": str,
          "failure_text":    str,
          "jira":            str,
          "jira_url":        str,
          "task_url":        str,
          "log_url":         str,
          "history": [
            {"build": str, "status": str, "duration": str},
            # ... last entry always has "current": True
          ],
          "ai_analysis": {}        # always present; AI phase fills it in
        }
      ]
    }
  ],

  "performance": [
    {
      "model":         str,        # display name
      "summary_note":  str,        # footer text or ""
      "summary_chips": None,       # None = auto-derive; or list of chip dicts
      "metrics": [
        {
          "name":           str,
          "unit":           str,
          "direction":      str,   # "higher_better" | "lower_better"
          "current":        float,
          "reference":      float,
          "history_values": [float, ...],
          "history_builds": [str, ...],  # same length as history_values
        }
      ]
    }
  ]
}
```

**Rules that must never be broken:**

- `"history_values"` and `"history_builds"` must always have the same length.
- `"direction"` must be exactly `"higher_better"` or `"lower_better"`.
- `"status"` must be exactly `"fail"`, `"error"`, or `"timeout"`.
- `"ai_analysis"` must always be a `dict` (never `None`; leave it `{}`).
- The last entry in every `"history"` list must have `"current": True`.
- `collect()` must never raise — catch all exceptions, log them, return `{}`.
- Return `{}` on config errors or DB connection failure.

`validate_merged()` in `generate_report.py` checks these rules after every
collection run and prints warnings without aborting.

---

## How to add a new data source

1. **Copy the scaffold:**

   ```bash
   cp datasources/tool_template.py datasources/tool_mydb.py
   ```

2. **Implement the class** — fill in `name`, `description`, and `collect()`.
   `collect()` must return the JSON contract above.

3. **Register it** in `generate_report.py`:

   ```python
   from datasources.tool_mydb import MyDbSource
   TOOLS["mydb"] = MyDbSource()
   ```

   `argparse` automatically adds `--mydb` to the CLI from the `TOOLS` dict.

4. **Add a config block** to `examples/config.json`:

   ```json
   "mydb": {
     "host": "...",
     "build": "{build}"
   }
   ```

5. **Add fixture data** in `examples/fixtures/mydb_schema.sql`.

6. **Write tests** in `tests/test_tools.py` following the existing
   `TestMySQLSourceContract` pattern — mock at the query level, verify the
   output shape.

7. **Update docs** — `README.md` project structure, CLI reference, and DB
   tools sections.

---

## Implementation workflow

Follow this workflow for every change, whether adding a feature, fixing a bug,
or adding a data source.

### 1 — Understand before touching

- Read the relevant source files before editing. Never edit from memory.
- Identify which invariants (especially the JSON contract) your change affects.
- If the change touches `collect()` output, the template, or `prepare_data()`,
  write tests before changing the implementation.

### 2 — Make the minimum change

- Change only what the task requires. Do not refactor surrounding code,
  rename variables, or clean up unrelated areas in the same commit.
- Prefer editing existing files over creating new ones.
- Do not add parameters, config keys, or toggles unless the task requires them.

### 3 — Run tests immediately

```bash
pytest tests/ -v --tb=short
```

All tests must pass before any commit. If a test fails after your change:

- Do not disable or delete the test.
- Fix the implementation to satisfy the test.
- If the test itself was wrong (it tested the wrong thing), fix the test
  and document why in the commit message.

### 4 — Add tests for new behaviour

Every new feature or bug fix must have at least one test.

For **data source output contract** changes, add tests to
`tests/test_tools.py` following the existing class structure. Tests must:
- Mock at the `_query` / `_execute` level, not at the connection level
  (so the actual data transformation logic is tested).
- Assert every required key is present in the output.
- Assert valid enum values (`status`, `direction`).
- Assert array length invariants (`history_values` == `history_builds`).
- Cover the empty-result case (empty DB table → empty list output).
- Cover the guard-rail cases (missing config key → `{}`).

For **computation changes** (`normalize_heights`, `compute_delta`, etc.),
add tests to `tests/test_compute.py`.

For **helper changes** (`_fmt_dur`, `_sort_builds`, etc.), add tests to
`tests/test_tools.py`.

### 5 — Regenerate the sample report

After any change to `template.html`, `generate_report.py`, or
`generate_sample.py`, regenerate the sample report to confirm the render
pipeline is not broken:

```bash
python generate_sample.py --out examples/sample_report.html
```

This script requires only `jinja2` and no database connection.

### 6 — Verify no stale references

After removing or renaming anything (a tool, a config key, a module), check
for stale references:

```bash
# Search all relevant file types
grep -r "old_name" --include="*.py" --include="*.json" --include="*.md" .
```

---

## Coding conventions

### Python

- Python 3.11+. Use `from __future__ import annotations` in all source files.
- Type hints on all function signatures; use built-in generics (`list[str]`,
  `dict[str, int]`) not `typing.List` / `typing.Dict`.
- No bare `except:` — catch the narrowest exception possible, or `Exception`
  with an explicit log message.
- No `print()` in library code (`datasources/`, `jira_enricher.py`,
  `ai_analyser.py`) — use `logging.getLogger(__name__)`.
- `generate_report.py` and `generate_sample.py` may use `print()` for
  user-facing progress messages; include `sys.stdout.reconfigure(encoding="utf-8")`
  at the top for Windows compatibility.
- No module-level side effects beyond logging setup.
- Credentials come from environment variables, never from config files or
  source code. Config files store only the env var *name*.

### Naming

- Tool files: `datasources/tool_<name>.py` — the `<name>` must match the key
  in the `TOOLS` dict and the section name in `config.json`.
- Source class: `<Name>Source(DataSource)`.
- SQL constants: `_UPPER_SNAKE_SQL` module-level.
- Helper functions: `_lower_snake()` module-level (private).
- Do not shadow the `config` parameter name inside methods (use `config_label`
  for loop variables that unpack DB config column values).

### SQL

- Use `%s` placeholders for all drivers; the ClickHouse `_execute()` adapter
  converts them to `{_pN}` for clickhouse-connect automatically.
- Batch history queries — one query for all failing test names (using `IN`),
  not N queries per test. Cap results in Python.
- Performance data: one query for the entire `performance_metrics` table; the
  build window and model grouping are done in Python.
- `ORDER BY` every query whose row order matters downstream.

### Config

- All time values in seconds (`int`).
- All count values as `int`.
- URL base values end without a trailing slash (the code appends the key).
- Use `{build}` as the placeholder for the current build in string values.
- Never hard-code build numbers, file paths, or hostnames in source files.

### Comments

- Write no comments unless the *why* is non-obvious.
- Never document what the code does; well-named identifiers do that.
- Do not reference issue numbers, PR numbers, or task names in comments.

---

## Performance guidelines

- **Parallel collection**: tools run concurrently via `ThreadPoolExecutor`.
  `collect()` must be thread-safe (no shared mutable module state).
- **Batch DB queries**: never issue one query per test case. Use `IN (…)` and
  group results in Python.
- **Single perf query**: fetch all `performance_metrics` rows in one query;
  all grouping, windowing, and model discovery happen in Python.
- **No redundant connections**: open one connection per `collect()` call,
  use it for all queries, close it in `finally`.
- **Early return on missing config**: validate required config keys at the
  start of `collect()` and return `{}` immediately to avoid a useless
  connection attempt.
- **Template rendering**: `template.html` is a single self-contained file.
  Keep all CSS and JS inline. Do not add external CDN dependencies.

---

## Test coverage targets

| Module | Covered by |
|---|---|
| `datasources/base.py` — `merge_results()` | `tests/test_tools.py::TestMergeResults` |
| `datasources/tool_mysql.py` — helpers | `tests/test_tools.py::TestFmtDur` … `TestBuildPerformance` |
| `datasources/tool_mysql.py` — `collect()` contract | `tests/test_tools.py::TestMySQLSourceContract` |
| `datasources/tool_clickhouse.py` — `collect()` contract | `tests/test_tools.py::TestClickHouseSourceContract` |
| `datasources/tool_clickhouse.py` — `_execute()` adapter | `tests/test_tools.py::TestClickHouseExecuteAdapter` |
| `generate_report.py` — computation | `tests/test_compute.py` |
| `generate_report.py` — `prepare_data()` | `tests/test_tools.py::TestPrepareData` |
| `jira_enricher.py` — term extraction | `tests/test_compute.py::TestExtractTerms` |

When adding a new data source tool, add a `Test<Name>SourceContract` class
that mirrors `TestMySQLSourceContract` in structure and coverage.

---

## Profiling

Every `collect()` call records timing data and returns it under a `"profiling"` key alongside `"failures"` and `"performance"`. The profiling data is merged into the saved `.data.json` file and is available to agents on subsequent runs.

### JSON shape

```json
"profiling": {
  "pipeline_total_s": 12.34,
  "phases": {
    "collection_s": 8.10,
    "jira_s": 2.40,
    "ai_s": 1.80,
    "render_s": 0.04
  },
  "tools": {
    "mysql": {
      "total_s": 4.20,
      "spans": {
        "connect_s":        0.12,
        "failures_query_s": 0.84,
        "history_query_s":  2.90,
        "perf_query_s":     0.34
      }
    }
  }
}
```

### How tools record timings

Each tool creates a `Profiler` (from `datasources/base.py`) at the start of `collect()` and wraps each operation with `prof.span("name_s")`:

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
```

Pass `prof` to private helpers and wrap each query:

```python
def _collect_failures(self, conn, config: dict, prof: Profiler) -> list:
    with prof.span("failures_query_s"):
        rows = self._query(conn, _FAILURES_SQL, (build,))
    with prof.span("history_query_s"):
        hist = self._query(conn, hist_sql, params)
```

Standard span names (use these in every tool):

| Span | Measures |
|---|---|
| `connect_s` | DB connection time |
| `failures_query_s` | Main failures SELECT |
| `history_query_s` | Batch history SELECT (typical bottleneck) |
| `perf_query_s` | Performance metrics SELECT |

Add further spans for any additional DB or API calls a tool makes.

### Agent use of profiling data

#### Access model — read-only databases

Agents have **read-only** access to production databases. They cannot add indexes,
create views, alter tables, or change any schema. All optimisations must come from
one of these three levers:

1. **Query rewriting** — push filters into SQL so fewer rows cross the network
   (e.g. the `_PERF_SQL` build-window subquery). The DB schema and indexes are
   fixed; write queries that benefit from existing indexes.
2. **Config tuning** — lower `history_limit` or `perf_history_limit` in
   `config.json` to reduce how much data is requested.
3. **Python processing** — avoid redundant passes over data already in memory;
   never re-query what was already fetched in the same `collect()` call.

When the agent reads `profiling` from `<build>.data.json`, it should map each
slow span to one of the three levers above — never to a schema change.

#### Interpreting slow spans

| Span | Likely cause | Agent's lever |
|---|---|---|
| `connect_s` > 0.3 s | Network latency to DB | Cannot fix; document for ops |
| `failures_query_s` > 0.5 s | No index on `build` | Query rewriting only; no index creation |
| `history_query_s` > 1 s | Many failing tests × many builds of history | Lower `history_limit` in config; or add a build-range `WHERE` clause if build numbers are numeric |
| `perf_query_s` > 0.5 s | Full table scan (no `WHERE` on build) | Push build-window filter into SQL using a subquery against `recorded_at` |
| `jira_s` > 2 s | Many tickets searched per failure | Set `"skip_if_present": true` in JIRA config |
| `ai_s` > 5 s | Many unenriched failures | Set `"skip_if_present": true` in AI config |
| `collection_s` ≈ slowest tool | Parallel tools; wall time = max, not sum | Optimise the slowest tool first |

#### What the agent must NOT suggest

- Adding or dropping indexes
- Creating materialized views or summary tables
- Changing table engine, partitioning, or charset
- Granting write permissions to run `ANALYZE TABLE` or `OPTIMIZE TABLE`

All of the above require DBA access that the agent does not have. If an index
is missing and query rewriting cannot compensate, the agent should document the
finding in the run log and recommend the index to a human DBA, not attempt to
create it.

### Rules when adding profiling to new tools

- Always use `Profiler` from `datasources/base.py`.
- Pass `prof` as the last argument to private helpers so sub-query spans are recorded.
- Span names must end in `_s` and use `lower_snake_case`.
- `collect()` must still return `{}` on error — never a partial dict with only `"profiling"`.

---

## Pipeline efficiency

This section documents every performance optimisation that is implemented and
the concepts behind each one, so agents can extend them correctly.

### Implemented optimisations

| Area | Technique |
|---|---|
| Tool collection | Parallel via `ThreadPoolExecutor` in `generate_report.py` |
| `_PERF_SQL` | Build-window subquery — filters rows server-side instead of full table scan |
| History query | Batched `IN` clause — one query for all failing test names |
| `failure_txt` transfer | `LEFT(failure_txt, 8192)` (MySQL) / `substring(..., 1, 8192)` (ClickHouse) in `_FAILURES_SQL` |
| JIRA enrichment | Parallel HTTP requests via `ThreadPoolExecutor` (`parallel_requests` config key, default 8) |
| AI analysis | Parallel LLM calls via `ThreadPoolExecutor` (`parallel_requests` config key, default 4) |
| Phase replay | Each phase writes `<build>.data.json`; re-running a phase skips all earlier work |
| Early exit | `_collect_failures()` returns `[]` immediately when no failures are found for the build |

### Parallelism design

**Collection phase:** `ThreadPoolExecutor` in `generate_report.py` runs all
enabled tools concurrently. Wall time equals the slowest tool, not their sum.
When both MySQL and ClickHouse are enabled, optimise the slower tool first.

**JIRA phase:** `JiraEnricher.enrich()` uses `ThreadPoolExecutor` with
`parallel_requests` (default 8). Each `_find_tickets()` call is pure HTTP I/O
with no shared mutable state. JIRA Cloud allows ~10 req/s per IP; 8 workers is
safe. Dry-run mode stays sequential so print lines are not interleaved.

**AI phase:** `AIAnalyser.enrich()` uses `ThreadPoolExecutor` with
`parallel_requests` (default 4). All `save_fn` calls (partial saves to disk)
are serialised under a `threading.Lock` so concurrent threads never write a
corrupted file. Dry-run mode stays sequential.

**Thread-safety rule:** any new enricher that uses `ThreadPoolExecutor` must
hold a `threading.Lock` around every cross-thread state mutation (counters,
`save_fn` calls, shared dicts). Never mutate `report_data` outside the lock.

### Config knobs for speed

These `config.json` keys trade report detail for speed without any code change:

| Section | Key | Default | Effect of lowering |
|---|---|---|---|
| `mysql` / `clickhouse` | `history_limit` | 7 | Fewer rows in `history_query_s` (the typical bottleneck) |
| `mysql` / `clickhouse` | `perf_history_limit` | 8 | Narrower build window in `perf_query_s` |
| `jira` | `parallel_requests` | 8 | Fewer concurrent JIRA HTTP calls |
| `jira` | `skip_if_present` | true | Skip test cases already enriched in `.data.json` |
| `ai` | `parallel_requests` | 4 | Fewer concurrent LLM calls (tune to your API tier's RPM limit) |
| `ai` | `skip_if_present` | true | Skip cases that already have `ai_analysis.text` |

### Truncation constants

| File | Location | Limit | Rationale |
|---|---|---|---|
| `datasources/tool_mysql.py` | `_FAILURES_SQL` — `LEFT(failure_txt, 8192)` | 8 KB | Covers any real stack trace; reduces wire transfer |
| `datasources/tool_clickhouse.py` | `_FAILURES_SQL` — `substring(failure_txt, 1, 8192)` | 8 KB | Same |
| `ai_analyser.py` | `build_prompt()` — `text[:2000]` | 2 000 chars | LLMs cannot usefully read more of a stack trace |
| `ai_analyser.py` | `_load_file()` — `content[:4000]` | 4 000 chars | Context file size cap |

To change a limit, edit the constant in the relevant file. Do not add config
keys for truncation limits — they are implementation constants, not user tuning.

---

## What never to do

- **Do not break the JSON contract.** If a key is removed or renamed in
  `collect()` output, the template will silently produce broken or blank
  sections. The tests in `TestMySQLSourceContract` / `TestClickHouseSourceContract`
  are the regression guard — keep them passing.
- **Do not add per-tool toggles or partial modes.** A tool is always a full
  data source provider. When enabled, it always returns both `failures` and
  `performance`.
- **Do not hard-code models in config.** Models are discovered automatically
  from the data in `performance_metrics`. No `"models"` config key.
- **Do not mock the DB connection in tests.** Mock at the `_query` /
  `_execute` level so the actual SQL routing, data transformation, and output
  assembly are exercised.
- **Do not suppress or skip failing tests.** A failing test means the
  invariant was broken; fix the code, not the test.
- **Do not commit credentials, tokens, or real hostnames.** Config files in
  `examples/` must use placeholder values and `{build}` tokens only.
- **Do not add OS packages to the Dockerfile.** The `python:3.11-slim` base
  image is sufficient; all dependencies are pure Python.
- **Do not use `git add -A` or `git add .` without review.** Stage files
  explicitly by name to avoid committing `.env`, `*.data.json`, or generated
  HTML reports.
