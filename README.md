# CI Dashboard Report Generator

Self-contained HTML report generator for CI build results. Collects test failures and performance metrics from configurable data sources, optionally enriches them with JIRA context and AI analysis, then renders a single-file Dracula-themed HTML report.

---

## Architecture

The pipeline has four sequential phases, each independently switchable:

```
┌─────────────┐   ┌──────────────┐   ┌──────────────┐   ┌────────┐
│  Collection │──▶│     JIRA     │──▶│      AI      │──▶│ Render │
│  --tool-*   │   │    --jira    │   │     --ai     │   │        │
└─────────────┘   └──────────────┘   └──────────────┘   └────────┘
  tools run in       fetches related    Claude analyses    Jinja2 +
  parallel, with     tickets for each   each failure,      Dracula
  retry on error     failing test case  writes ai_analysis template
```

All phases write an intermediate `<build>.data.json` file so any stage can be re-run independently without repeating earlier ones.

---

## Quick start

### Prerequisites

- Docker (recommended), or Python 3.11+ with `pip install jinja2 anthropic`

### 1 — Clone and build

```bash
git clone <repo>
cd ci-report
docker build -t ci-report .
```

### 2 — Set up credentials

```bash
cp .env.example .env
# edit .env — fill in JIRA_API_TOKEN and/or ANTHROPIC_API_KEY
```

### 3 — Configure paths

Copy the example config and edit it to point at your database:

```bash
cp examples/config.json config.json
```

### 4 — Validate credentials

```bash
docker run --rm --env-file .env -v $(pwd):/workspace ci-report --check-credentials
```

### 5 — Generate a report

```bash
# MySQL data source + JIRA enrichment + AI analysis
docker run --rm --env-file .env -v $(pwd):/workspace ci-report \
  --mysql --jira --ai 1247

# ClickHouse data source only
docker run --rm --env-file .env -v $(pwd):/workspace ci-report \
  --clickhouse 1247

# Both data sources (e.g. different test frameworks in different DBs)
docker run --rm --env-file .env -v $(pwd):/workspace ci-report \
  --mysql --clickhouse 1247
```

The report is written to `./1247.html` in your current directory.

### Makefile shortcuts

```bash
make build            # build the image
make check            # validate credentials
make report BUILD=1247
make report BUILD=1247 TOOLS="--mysql" FLAGS="--ai"
```

---

## Exit codes

| Code | Meaning |
|------|---------|
| `0` | Report generated, no test failures found |
| `1` | Script or configuration error |
| `2` | Report generated, test failures present |

Use in CI:
```bash
docker run ... ci-report --mysql --ai 1247
# The pipeline step fails if failures were found (exit 2)
```

---

## Configuration reference (`config.json`)

All settings live in `config.json`. Use `{build}` in any string value as a placeholder for the build name passed on the CLI.

```json
{
  "credentials": {
    "JIRA_API_TOKEN":    "Description shown by --check-credentials",
    "ANTHROPIC_API_KEY": "Description shown by --check-credentials"
  },

  "base": {
    "build_url":     "https://ci.example.com/jobs/{build}",
    "branch":        "feature/my-branch",
    "branch_url":    "https://github.com/org/repo/tree/feature/my-branch",
    "commit":        "",
    "commit_url":    "https://github.com/org/repo/commit/{commit}",
    "ref_build":     "1244",
    "ref_build_url": "https://ci.example.com/jobs/1244",
    "badges": { "passed": 0, "skipped": 0, "metrics": 0 }
  },

  "mysql": {
    "host":          "localhost",
    "port":          3306,
    "database":      "ci_reports",
    "user_env":      "MYSQL_USER",
    "password_env":  "MYSQL_PASSWORD",
    "build":         "{build}",
    "history_limit": 7,
    "jira_base_url": "https://org.atlassian.net/browse/",
    "task_base_url": "https://ci.example.com/jobs/{build}/tasks/",
    "log_base_url":  "https://ci.example.com/jobs/{build}/logs/"
  },

  "clickhouse": {
    "host":               "localhost",
    "port":               9000,
    "database":           "ci_metrics",
    "user_env":           "CLICKHOUSE_USER",
    "password_env":       "CLICKHOUSE_PASSWORD",
    "build":              "{build}",
    "ref_build":          "1244",
    "history_limit":      7,
    "perf_history_limit": 8,
    "jira_base_url":      "https://org.atlassian.net/browse/",
    "task_base_url":      "https://ci.example.com/jobs/{build}/tasks/",
    "log_base_url":       "https://ci.example.com/jobs/{build}/logs/"
  },

  "jira": {
    "base_url":        "https://org.atlassian.net",
    "email":           "ci@org.com",
    "api_token_env":   "JIRA_API_TOKEN",
    "project_keys":    ["PROJ", "INFRA"],
    "max_related":     4,
    "search_terms":    3,
    "skip_if_present": true,
    "timeout":         10
  },

  "ai": {
    "model":           "claude-sonnet-4-6",
    "context_file":    "context.txt",
    "log_dir":         "logs/{build}/",
    "max_tokens":      600,
    "delay_between":   0.5,
    "skip_if_present": true
  }
}
```

### context.txt

Plain text file injected into every AI prompt. Keep it current with your infrastructure:

```
## Infrastructure
- Artifactory 7.71.3 on eu-west-1 (3-node cluster)
- Agents: agent-01 (docker-arm64), agent-02 (docker-amd64)
- Token lifetime: 18 minutes (JWT_EXPIRY=1080)

## Required env vars
- JFROG_CLI_TOKEN_REFRESH=true   (enables mid-upload token refresh)

## Known issues
- agent-01 re-provisioned 2024-01-14 — env vars may be missing
```

### Database tools

Each data source tool reads from a live database and always returns both test failures and performance metrics. They require an optional driver package:

```bash
pip install PyMySQL                # for --mysql
pip install clickhouse-driver      # for --clickhouse
```

Load the bundled fixture data to try the tools without a real CI database:

```bash
# MySQL
mysql -u root < examples/fixtures/mysql_schema.sql

# ClickHouse
clickhouse-client --multiquery < examples/fixtures/clickhouse_schema.sql
```

Then run against build `1247` (the fixture's "current" build):

```bash
python generate_report.py --mysql --clickhouse 1247
```

Both tools expect `test_runs` and `performance_metrics` tables. Schemas and fixture data are in `examples/fixtures/`. Columns queried: see the tool docstrings in `datasources/tool_mysql.py` and `datasources/tool_clickhouse.py`.

---

## CLI reference

```
python generate_report.py [--tool-flags] [--jira] [--ai] <build>
python generate_report.py data.json          # render-only, no collection

Options:
  --mysql              Read test failures and performance from MySQL / MariaDB
  --clickhouse         Read test failures and performance from ClickHouse
  --jira               Enrich failures with related JIRA tickets
  --ai                 Run AI analysis on collected failures
  --check-credentials  Validate credentials in config.json and exit
  --config PATH        Config file  [default: config.json]
  --template PATH      Jinja2 template  [default: template.html]
  --out PATH           Output HTML  [default: <build>.html]
```

Standalone enrichment (re-run without re-collecting):
```bash
python jira_enricher.py 1247.data.json config.json
python jira_enricher.py 1247.data.json config.json --dry-run
python ai_analyser.py   1247.data.json config.json
python ai_analyser.py   1247.data.json config.json --dry-run
```

---

## Adding a data source tool

1. Copy `datasources/tool_template.py` to `datasources/tool_<name>.py`
2. Fill in `name`, `description`, and `collect()`
3. Add two lines to `generate_report.py`:
   ```python
   from datasources.tool_myname import MyNameSource
   TOOLS["my_name"] = MyNameSource()
   ```
4. Add a `"my_name": { ...settings... }` block to `config.json`
5. Run with `--my-name` (underscores become hyphens in the CLI flag)

argparse picks up the new flag automatically from the `TOOLS` dict. No other changes needed.

### Providing JIRA search context from your tool

Any test case dict can include a `search_hint` field. The JIRA enricher uses it instead of auto-extracting terms, giving you precise control:

```python
test_cases.append({
    "name":        "My failing test",
    "search_hint": "token refresh env var agent provision",
    ...
})
```

---

## Credentials

Credentials are defined as **env var names** in `config.json`  under the `credentials` block. The actual values live in environment variables only — never in config files.

```
config.json  →  credentials: { "VAR_NAME": "description" }   ← names only
.env         →  VAR_NAME=actual_secret_value                  ← values
```

This makes `config.json` safe to commit to version control.

For local development: copy `.env.example` to `.env` and fill in values.  
For CI/CD: use your platform's secrets management (Jenkins credentials store, GitHub Actions secrets, GitLab CI variables, etc.).

---

## Development

### Run tests

```bash
pip install pytest
pytest tests/ -v
```

#### Windows (no system Python on PATH)

On Windows the Microsoft Store Python stub may be present but not functional. The project ships with `uv` support — use it to create an isolated environment and run tests:

```powershell
# One-time setup — create the virtual environment and install all dependencies
& "$env:USERPROFILE\.local\bin\uv.exe" venv .venv
& "$env:USERPROFILE\.local\bin\uv.exe" pip install -r requirements.txt pytest

# Run tests
.\.venv\Scripts\python.exe -m pytest tests/ -v --tb=short
```

`uv` is available at `~/.local/bin/uv.exe` after installing via the [uv installer](https://docs.astral.sh/uv/getting-started/installation/). The `.venv` directory is gitignored.

### Preview the report without real data

```bash
pip install jinja2
python generate_sample.py --out examples/sample_report.html
```

Generates synthetic but realistic CI data and renders it through the template. Useful for testing template changes without a live CI environment.

### Project structure

```
generate_report.py        Main script + CLI
generate_sample.py        Generates a sample report for preview/testing
ai_analyser.py            AI analysis phase
jira_enricher.py          JIRA enrichment phase
template.html             Jinja2 HTML template (Dracula theme)

datasources/
  base.py                 DataSource ABC + merge_results()
  tool_mysql.py           MySQL / MariaDB full data source (failures + performance)
  tool_clickhouse.py      ClickHouse full data source (failures + performance)
  tool_template.py        Scaffold for new tools

tests/
  test_compute.py         Unit tests for computation layer

examples/
  config.json             Example config (copy to root and customise)
  example_run.json        Example run config for --config mode
  context.txt             Example AI context file
  example_data.json       Sample pre-collected report data
  sample_report.html      Pre-rendered sample dashboard
  fixtures/
    mysql_schema.sql      MySQL schema + fixture data (8 builds, 2 scenarios, 2 perf models)
    clickhouse_schema.sql ClickHouse schema + fixture data (8 builds, 2 scenarios, 2 perf models)

Dockerfile                Container definition
requirements.txt          Python dependencies
.env.example              Credential template (copy to .env and fill in values)
.dockerignore             Build context exclusions
Makefile                  Common task shortcuts
```

### Extending the template

The template is pure Jinja2 + CSS. The Dracula colour tokens are in `:root` in the `<style>` block — swapping to a different palette is a single variable block change.

The template includes `@media (max-width: 768px)` responsive rules and `@media print` rules for PDF output with a light background.
