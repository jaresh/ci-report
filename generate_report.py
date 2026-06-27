#!/usr/bin/env python3
"""
generate_report.py — CI dashboard report generator.

Usage:
    # Collect from data sources, then render  (build name is required)
    python generate_report.py --mysql 1247
    python generate_report.py --clickhouse --ai 1247
    python generate_report.py --mysql --clickhouse 1247 --out report_1247.html

    # Render only (pre-built data JSON, no collection, no AI)
    python generate_report.py data.json

Tool flags activate specific data sources.  --ai activates the AI analysis
phase.  All tool settings live in config.json (see example_config.json).

Requirements:
    pip install jinja2
"""

import argparse
import json
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from datetime import datetime

# Windows terminals default to cp1252/cp1251 — force UTF-8 so Unicode
# status symbols (✓ ✗ → ▲ ▼) print without errors.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

try:
    from jinja2 import Environment, FileSystemLoader
except ImportError:
    sys.exit("Missing dependency: pip install jinja2")

# ── Static tool registry ──────────────────────────────────────────────────────
# Add a new tool: create datasources/tool_xyz.py, import here, add to TOOLS.

sys.path.insert(0, str(Path(__file__).parent))

from datasources.tool_mysql      import MySQLSource
from datasources.tool_clickhouse import ClickHouseSource

TOOLS: dict = {
    "mysql":      MySQLSource(),
    "clickhouse": ClickHouseSource(),
}


# ── Constants ─────────────────────────────────────────────────────────────────

SPARK_MIN_H  = 8    # minimum sparkline bar height (px)
SPARK_MAX_H  = 20   # maximum sparkline bar height (px)
DELTA_THRESH = 2.0  # % change that counts as improvement or regression

STATUS_SYM = {"pass": "✓", "fail": "✗", "error": "✗", "timeout": "⏱", "skip": "⚠", "na": "—"}


# ── Build-name substitution ───────────────────────────────────────────────────

def resolve_build(obj, build: str):
    """
    Recursively replace {build} in every string value in obj.
    Lets config.json use paths like "results/{build}/*.xml" that
    are resolved at runtime from the CLI build argument.
    """
    if isinstance(obj, str):
        return obj.replace("{build}", build)
    if isinstance(obj, dict):
        return {k: resolve_build(v, build) for k, v in obj.items()}
    if isinstance(obj, list):
        return [resolve_build(v, build) for v in obj]
    return obj


# ── Schema validation ─────────────────────────────────────────────────────────

def validate_merged(data: dict) -> list[str]:
    """
    Lightweight structural check on the merged report dict.
    Returns a list of human-readable error strings; empty = valid.
    Called after merge_results() so bad tool output is caught early
    rather than producing a cryptic Jinja2 UndefinedError at render time.
    """
    errors: list[str] = []
    build = data.get("build", {})
    if not str(build.get("number", "")).strip():
        errors.append("build.number is missing — was a build name provided?")

    valid_statuses = {"fail", "error", "timeout", "skip"}
    for i, s in enumerate(data.get("failures", [])):
        loc = f"failures[{i}]"
        if not s.get("scenario"):
            errors.append(f"{loc}: missing 'scenario' field")
        for j, tc in enumerate(s.get("test_cases", [])):
            tloc = f"{loc}.test_cases[{j}]"
            if not tc.get("name"):
                errors.append(f"{tloc}: missing 'name' field")
            st = tc.get("status")
            if st and st not in valid_statuses:
                errors.append(f"{tloc}: unknown status '{st}' — expected one of {sorted(valid_statuses)}")

    valid_dirs = {"higher_better", "lower_better"}
    for i, model in enumerate(data.get("performance", [])):
        loc = f"performance[{i}]"
        if not model.get("model"):
            errors.append(f"{loc}: missing 'model' field")
        for j, m in enumerate(model.get("metrics", [])):
            mloc = f"{loc}.metrics[{j}]"
            if not m.get("name"):
                errors.append(f"{mloc}: missing 'name' field")
            if "current" not in m:
                errors.append(f"{mloc}: missing 'current' value")
            d = m.get("direction")
            if d and d not in valid_dirs:
                errors.append(f"{mloc}: invalid direction '{d}' — expected one of {sorted(valid_dirs)}")

    return errors


# ── Tool retry helper ─────────────────────────────────────────────────────────

def _collect_with_retry(tool, cfg: dict, label: str,
                        times: int = 3, backoff: float = 2.0) -> dict:
    """Run tool.collect() with exponential-backoff retry on transient errors."""
    log = logging.getLogger(__name__)
    for attempt in range(times):
        try:
            return tool.collect(cfg)
        except Exception as exc:
            if attempt == times - 1:
                raise
            wait = backoff ** attempt
            log.warning("%s attempt %d/%d failed: %s — retry in %.0fs",
                        label, attempt + 1, times, exc, wait)
            time.sleep(wait)
    return {}   # unreachable but satisfies type checkers



def normalize_heights(values: list, direction: str) -> list[int]:
    """
    Map raw metric values → bar heights in px so UP always = improvement.
    lower_better metrics are inverted before scaling.
    """
    if not values:
        return []
    mn, mx = min(values), max(values)
    if mn == mx:
        return [SPARK_MAX_H] * len(values)
    rng = mx - mn

    def scale(v):
        return round(SPARK_MIN_H + (v - mn) / rng * (SPARK_MAX_H - SPARK_MIN_H))

    if direction == "lower_better":
        return [round(SPARK_MIN_H + (mx - v) / rng * (SPARK_MAX_H - SPARK_MIN_H)) for v in values]
    return [scale(v) for v in values]


# ── Delta ─────────────────────────────────────────────────────────────────────

def compute_delta(current: float, reference: float, direction: str) -> tuple:
    """
    Returns (pct, display_str, css_class).
    For higher_better: positive delta is green ("pos").
    For lower_better:  negative delta is green ("pos").
    """
    if reference == 0:
        return 0.0, "→ N/A", "neu"

    pct = (current - reference) / abs(reference) * 100

    if direction == "lower_better":
        is_good = pct < -DELTA_THRESH
        is_bad  = pct > DELTA_THRESH
    else:
        is_good = pct > DELTA_THRESH
        is_bad  = pct < -DELTA_THRESH

    css = "pos" if is_good else ("neg" if is_bad else "neu")

    if abs(pct) < 0.05:
        display = "→ 0%"
    elif pct > 0:
        display = f"▲ +{pct:.1f}%"
    else:
        display = f"▼ {pct:.1f}%"

    return pct, display, css


def metric_status(delta_pct: float, direction: str) -> str:
    """Return 'improved' | 'regressed' | 'neutral'."""
    if direction == "lower_better":
        if delta_pct < -DELTA_THRESH:  return "improved"
        if delta_pct > DELTA_THRESH:   return "regressed"
    else:
        if delta_pct > DELTA_THRESH:   return "improved"
        if delta_pct < -DELTA_THRESH:  return "regressed"
    return "neutral"


# ── Model trend ───────────────────────────────────────────────────────────────

def model_trend(metrics: list) -> tuple:
    """
    Returns (trend_class, trend_label, improved, neutral, regressed, counts_display).
    trend_class: "good" | "bad" | "neu"
    """
    improved  = sum(1 for m in metrics if m["status"] == "improved")
    regressed = sum(1 for m in metrics if m["status"] == "regressed")
    neutral   = len(metrics) - improved - regressed
    total     = len(metrics)

    if improved > regressed:
        cls, label, counts = "good", "▲ Improved", f"{improved} / {total} improved"
    elif regressed > improved:
        cls, label, counts = "bad",  "▼ Regressed", f"{regressed} / {total} regressed"
    else:
        cls, label, counts = "neu",  "→ Neutral", f"{neutral} / {total} stable"

    return cls, label, improved, neutral, regressed, counts


# ── History ───────────────────────────────────────────────────────────────────

def history_summary(history: list) -> tuple:
    """
    Returns (rate_str, note_str).
    Handles empty history and any number of prior builds (0, 1, N).
    """
    if not history:
        return "", ""

    passed  = sum(1 for h in history if h["status"] == "pass")
    total   = len(history)
    rate    = f"{passed} / {total} passed"

    current  = next((h for h in history if h.get("current")), history[-1])
    prev     = [h["status"] for h in history if not h.get("current")]

    # No prior builds to compare against
    if not prev:
        return rate, ""

    fail_cnt = sum(1 for s in prev if s in ("fail", "error", "timeout"))

    if current["status"] in ("fail", "error", "timeout"):
        if fail_cnt == 0:
            note = "first failure — consistent pass until current build"
        elif fail_cnt >= max(1, len(prev) // 2):
            note = "recurring failure — flaky or persistent issue"
        else:
            note = f"intermittent — {fail_cnt} prior failure(s) in window"
    else:
        note = ""

    return rate, note


# ── Data enrichment ───────────────────────────────────────────────────────────

def _unit_display(unit: str, direction: str) -> str:
    arrow = "↓ lower better" if direction == "lower_better" else "↑ higher better"
    inv   = "· bars inv." if direction == "lower_better" else ""
    return f"{unit} · {arrow} {inv}".strip()


def _chip_css(delta_css: str) -> str:
    return {"pos": "green", "neg": "red"}.get(delta_css, "")


def prepare_data(raw: dict) -> dict:
    """Enrich raw JSON with all computed display values needed by the template."""
    out = dict(raw)
    out["generated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")

    # ── Performance models ────────────────────────────────────────────────────
    perf_out = []
    for model in raw.get("performance", []):
        metrics_out = []

        for m in model["metrics"]:
            values    = m["history_values"]
            direction = m["direction"]
            current   = float(m["current"])
            reference = float(m["reference"])

            delta_pct, delta_display, delta_css = compute_delta(current, reference, direction)
            status    = metric_status(delta_pct, direction)
            heights   = normalize_heights(values, direction)

            metrics_out.append({
                **m,
                "sparkline_heights": heights,
                "delta_pct":         delta_pct,
                "delta_display":     delta_display,
                "delta_css":         delta_css,
                "status":            status,
                "unit_display":      _unit_display(m["unit"], direction),
                "value_color":       _chip_css(delta_css),
            })

        cls, label, improved, neutral, regressed, counts = model_trend(metrics_out)

        # Summary chips: explicit in JSON, or auto-derive from first 2 metrics
        raw_chips = model.get("summary_chips")
        if raw_chips:
            chips_display = [
                {**c, "css_class": c.get("css_class", "")}
                for c in raw_chips
            ]
        else:
            chips_display = [
                {
                    "label":     m["name"].lower()[:14],
                    "value":     f"{m['current']} {m['unit']}",
                    "css_class": _chip_css(m["delta_css"]),
                }
                for m in metrics_out[:2]
            ]

        history_len = len(metrics_out[0]["history_builds"]) if metrics_out else 0

        summary_note = model.get("summary_note") or (
            "All metrics at best in recent window"  if cls == "good" else
            "Performance regression — investigation recommended" if cls == "bad" else
            f"All metrics stable vs ref build #{raw['build']['ref_build']}"
        )

        perf_out.append({
            **model,
            "metrics":            metrics_out,
            "trend_class":        cls,
            "trend_label":        label,
            "improved_count":     improved,
            "neutral_count":      neutral,
            "regressed_count":    regressed,
            "total_metrics":      len(metrics_out),
            "counts_display":     counts,
            "summary_chips_display": chips_display,
            "history_length":     history_len,
            "summary_note":       summary_note,
        })

    out["performance"] = perf_out

    # ── Failures ──────────────────────────────────────────────────────────────
    failures_out = []
    for scenario in raw.get("failures", []):
        tcs_out = []
        for tc in scenario["test_cases"]:
            history = tc.get("history") or []
            rate, note = history_summary(history)

            # Inherit environment fields from scenario if tc doesn't declare its own
            tc_config = tc.get("config") or scenario.get("config", "")
            tc_os     = tc.get("os")    or scenario.get("os", "")
            tc_agent  = tc.get("agent") or scenario.get("agent", "")

            # ai_analysis may be absent, None, or an empty dict
            ai = tc.get("ai_analysis") or {}
            has_ai = bool(ai.get("text"))

            tcs_out.append({
                **tc,
                "config":            tc_config,
                "os":                tc_os,
                "agent":             tc_agent,
                "is_scenario_error": tc.get("is_scenario_error", False),
                "error_label":       tc.get("error_label", ""),
                "history":           history,
                "history_rate":      rate,
                "history_note":      note,
                "has_ai":            has_ai,
                "ai_analysis":       ai,
            })
        failures_out.append({**scenario, "test_cases": tcs_out})

    out["failures"] = failures_out
    return out


# ── Jinja2 filters ────────────────────────────────────────────────────────────

def filter_status_sym(status: str) -> str:
    return STATUS_SYM.get(status, "?")

def filter_trend_chip_class(trend_class: str) -> str:
    return {"good": "up", "bad": "down", "neu": "neu"}.get(trend_class, "neu")

def filter_fmt_float(value) -> str:
    """Format a float nicely: drop .0 suffix, keep meaningful decimals."""
    try:
        f = float(value)
        return str(int(f)) if f == int(f) else f"{f:g}"
    except (TypeError, ValueError):
        return str(value)


# ── Render ────────────────────────────────────────────────────────────────────

def _build_env(template_dir: Path) -> "Environment":
    env = Environment(
        loader=FileSystemLoader(str(template_dir)),
        autoescape=False,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["status_sym"]        = filter_status_sym
    env.filters["trend_chip_class"]  = filter_trend_chip_class
    env.filters["fmt"]               = filter_fmt_float
    return env


def _render_prepared(data: dict, template_path: Path, output_path: Path):
    env  = _build_env(template_path.parent)
    html = env.get_template(template_path.name).render(data=data)
    output_path.write_text(html, encoding="utf-8")
    print(f"✓ Report written → {output_path}")


def render(data_path: Path, template_path: Path, output_path: Path):
    """Direct-data mode: load JSON file, enrich, render."""
    raw  = json.loads(data_path.read_text(encoding="utf-8"))
    data = prepare_data(raw)
    _render_prepared(data, template_path, output_path)


# ── Plugin / config mode ──────────────────────────────────────────────────────

# ── Credential validation ─────────────────────────────────────────────────────

def check_credentials(config: dict) -> bool:
    """
    Validate that every env var listed in config["credentials"] is set.
    Prints a status line per credential. Returns True if all are present.
    """
    creds = {k: v for k, v in config.get("credentials", {}).items()
             if not k.startswith("_")}
    if not creds:
        print("No credentials defined in config.")
        return True

    all_ok = True
    width  = max(len(k) for k in creds)
    print(f"{'Variable':<{width}}   Status     Description")
    print("─" * (width + 50))
    for var, description in creds.items():
        present = bool(os.environ.get(var))
        status  = "✓  set   " if present else "✗  MISSING"
        if not present:
            all_ok = False
        print(f"  {var:<{width}}   {status}  {description}")
    print()
    return all_ok


def run_pipeline(build: str, selected_tools: list[str], run_jira: bool,
                 run_ai: bool, config: dict, template_path: Path,
                 output_path: Path):
    """
    Execute the selected phases for a given build name.
    Returns an exit code: 0 = no failures found, 2 = failures present.
    """
    from datasources.base import merge_results

    logging.basicConfig(level=logging.INFO,
                        format="%(levelname)-8s %(name)s — %(message)s")

    # ── Collect (parallel) ────────────────────────────────────────────────────
    tool_outputs: list[dict] = []

    if selected_tools:
        print(f"Collection phase  (build: {build})  [{len(selected_tools)} tool(s) in parallel]")

        # Submit all tools concurrently; retry each independently
        fut_map: dict = {}
        with ThreadPoolExecutor(max_workers=len(selected_tools)) as ex:
            for key in selected_tools:
                fut_map[key] = ex.submit(
                    _collect_with_retry, TOOLS[key], config.get(key, {}), key
                )

        # Print results and collect in CLI-specified order (preserves merge order)
        for key in selected_tools:
            try:
                out    = fut_map[key].result()
                n_fail = sum(len(s.get("test_cases", [])) for s in out.get("failures", []))
                n_perf = len(out.get("performance", []))
                print(f"  ✓  {key}: {n_fail} failure(s), {n_perf} perf model(s)")
                tool_outputs.append(out)
            except Exception as exc:
                print(f"  ✗  {key}: {exc}")
    else:
        print("No collection tools selected — using base config data only")

    base = dict(config.get("base", {}))
    base["number"] = build
    merged = merge_results({"build": base}, tool_outputs)

    # ── Schema validation ─────────────────────────────────────────────────────
    errors = validate_merged(merged)
    if errors:
        print(f"\n  ⚠  {len(errors)} validation warning(s):")
        for e in errors:
            print(f"     · {e}")
        print()

    dump_path = output_path.with_suffix(".data.json")
    dump_path.write_text(json.dumps(merged, indent=2, ensure_ascii=False))
    print(f"  data → {dump_path}")

    # ── JIRA enrichment ───────────────────────────────────────────────────────
    if run_jira:
        print("\nJIRA enrichment phase…")
        from jira_enricher import JiraEnricher
        merged = JiraEnricher(config.get("jira", {})).enrich(merged)
        dump_path.write_text(json.dumps(merged, indent=2, ensure_ascii=False))
        print(f"  data (with JIRA) → {dump_path}")
    else:
        print("\nJIRA enrichment phase: off")

    # ── AI analysis ───────────────────────────────────────────────────────────
    if run_ai:
        print("\nAI analysis phase…")
        from ai_analyser import AIAnalyser

        def _incremental_save(data):
            dump_path.write_text(json.dumps(data, indent=2, ensure_ascii=False))

        merged = AIAnalyser(config.get("ai", {})).enrich(merged, save_fn=_incremental_save)
        dump_path.write_text(json.dumps(merged, indent=2, ensure_ascii=False))
        print(f"  data (with AI) → {dump_path}")
    else:
        print("\nAI analysis phase: off")

    # ── Render ────────────────────────────────────────────────────────────────
    print()
    _render_prepared(prepare_data(merged), template_path, output_path)

    # Exit code 2 signals "report generated but test failures were found"
    # so CI pipelines can gate on it without parsing the HTML.
    return 2 if merged.get("failures") else 0


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(
        description="CI dashboard report generator.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
available tools:
{"".join(f"  --{k.replace('_', '-'):<20} {t.description}{chr(10)}" for k, t in TOOLS.items())}
examples:
  python generate_report.py --junit-xml --metrics-json --ai 1247
  python generate_report.py --junit-xml --jira --ai 1247
  python generate_report.py --junit-xml 1247
  python generate_report.py --metrics-json --ai 1247 --out perf_1247.html
  python generate_report.py report.data.json          # render only, no tools
""",
    )

    p.add_argument("build", nargs="?", default=None,
                   help="Build name / number to analyse, OR path to a pre-built "
                        "data JSON file (render-only mode). "
                        "Not required when using --check-credentials.")

    # One flag per registered tool — generated automatically from TOOLS
    for key, tool in TOOLS.items():
        flag = f"--{key.replace('_', '-')}"
        p.add_argument(flag, action="store_true",
                       help=f"Enable: {tool.description}")

    p.add_argument("--jira",     action="store_true",
                   help="Enrich failures with related JIRA tickets (requires JIRA_API_TOKEN)")
    p.add_argument("--ai",       action="store_true",
                   help="Run AI analysis on collected failures (requires ANTHROPIC_API_KEY)")
    p.add_argument("--check-credentials", action="store_true",
                   help="Validate all credentials in config and exit")
    p.add_argument("--config",   type=Path, default=Path("config.json"),
                   help="Project config JSON  [default: config.json]")
    p.add_argument("--template", type=Path, default=None,
                   help="Jinja2 template      [default: template.html next to script]")
    p.add_argument("--out",      type=Path, default=None,
                   help="Output HTML          [default: <build>.html]")

    args = p.parse_args()

    template_path = args.template or (Path(__file__).parent / "template.html")

    # --check-credentials loads config then exits — no build name needed
    if args.check_credentials:
        config: dict = {}
        if args.config.exists():
            config = json.loads(args.config.read_text(encoding="utf-8"))
        ok = check_credentials(config)
        sys.exit(0 if ok else 1)

    if not template_path.exists():
        p.error(f"Template not found: {template_path}")

    if args.build is None:
        p.error("build name is required (e.g. generate_report.py --junit-xml 1247)")

    # ── Render-only mode: positional arg is an existing file ─────────────────
    build_path = Path(args.build)
    if build_path.exists() and build_path.suffix == ".json":
        if any(getattr(args, k) for k in TOOLS) or args.ai or args.jira:
            p.error("Cannot use tool/phase flags with a data file (render-only mode).")
        out = args.out or build_path.with_suffix(".html")
        render(build_path, template_path, out)
        return

    # ── Plugin mode: positional arg is a build name ───────────────────────────
    build = args.build

    # Load and resolve config
    config: dict = {}
    if args.config.exists():
        raw_cfg = json.loads(args.config.read_text(encoding="utf-8"))
        config  = resolve_build(raw_cfg, build)
    elif args.config != Path("config.json"):
        p.error(f"Config not found: {args.config}")

    # Warn about missing credentials for the phases that were requested
    if args.jira or args.ai:
        needed = {}
        if args.jira:
            var = config.get("jira", {}).get("api_token_env", "JIRA_API_TOKEN")
            needed[var] = "required by --jira"
        if args.ai:
            var = config.get("ai", {}).get("api_key_env", "ANTHROPIC_API_KEY")
            needed[var] = "required by --ai"
        missing = [v for v in needed if not os.environ.get(v)]
        if missing:
            for v in missing:
                print(f"  ⚠  {v} not set ({needed[v]}) — phase will be skipped")
            print()

    # Which tools were requested?
    selected = [key for key in TOOLS if getattr(args, key)]

    out = args.out or Path(f"{build}.html")

    sys.exit(run_pipeline(
        build          = build,
        selected_tools = selected,
        run_jira       = args.jira,
        run_ai         = args.ai,
        config         = config,
        template_path  = template_path,
        output_path    = out,
    ))


if __name__ == "__main__":
    main()


