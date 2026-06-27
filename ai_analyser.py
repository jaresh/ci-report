"""
ai_analyser.py
──────────────
Prompt builder and response parser for the AI analysis phase.

Assembles structured prompts from test failure data and parses
LLM JSON responses back into the report schema.  The actual LLM
call is left to external tooling — pass a call_fn to enrich() or
use build_prompt() / parse_response() directly.

call_fn signature
─────────────────
    def call_fn(system_prompt: str, user_prompt: str) -> str:
        # Call any LLM and return the raw text response
        ...

Config keys (under "ai" in config.json)
────────────────────────────────────────
context_file      str    path to plain-text system context  [optional]
log_dir           str    directory for local .log files     [optional]
skip_if_present   bool   skip cases that already have text  [default: true]

Usage — preview prompts
───────────────────────
    python ai_analyser.py data.json config.json

Usage — from generate_report.py
────────────────────────────────
    from ai_analyser import AIAnalyser

    def my_llm(system: str, user: str) -> str:
        ...  # call your preferred LLM, return raw text
        return response_text

    analyser = AIAnalyser(config["ai"])
    merged   = analyser.enrich(merged, call_fn=my_llm)
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import threading as _threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

log = logging.getLogger(__name__)

_CODE_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)
_SAFE       = re.compile(r"[^\w\-]")


def _safe_name(s: str) -> str:
    return _SAFE.sub("_", s)[:80]


def _history_label(history: list) -> str:
    if not history:
        return "no prior history"
    passed = sum(1 for h in history if h.get("status") == "pass")
    return f"{passed} / {len(history)} passed"


def _history_note(history: list) -> str:
    prev  = [h for h in history if not h.get("current")]
    if not prev:
        return ""
    fails = sum(1 for h in prev if h.get("status") in ("fail", "error", "timeout"))
    if fails == 0:
        return "first failure — was consistently passing before this build"
    if fails >= max(1, len(prev) // 2):
        return f"recurring — {fails} of {len(prev)} prior runs also failed"
    return f"intermittent — {fails} prior failure(s) in window"


# ── Analyser ──────────────────────────────────────────────────────────────────

class AIAnalyser:

    SYSTEM_PROMPT = (
        "You are a CI/CD failure analyst embedded in a build dashboard. "
        "Given structured information about a test failure, produce a concise "
        "diagnostic in JSON. "
        "Always respond with a single JSON object and nothing else — "
        "no markdown fences, no preamble, no trailing text."
    )

    def __init__(self, ai_config: dict):
        self.skip_present      = ai_config.get("skip_if_present", True)
        self.log_dir           = ai_config.get("log_dir")
        self.context           = self._load_file(ai_config.get("context_file"))
        self.parallel_requests = int(ai_config.get("parallel_requests", 4))

    # ── Public entry point ────────────────────────────────────────────────────

    def enrich(self, report_data: dict, call_fn=None,
               dry_run: bool = False, save_fn=None) -> dict:
        """
        Walk every failing test case and populate ai_analysis.

        call_fn(system_prompt, user_prompt) -> str
            Called for each test case that needs analysis.
            Return the raw LLM response text.
            If omitted, enrich() is a no-op unless dry_run is True.

        dry_run=True  — print prompts to stdout instead of calling call_fn.
        save_fn       — called with report_data after each result so partial
                        runs survive interruption. Calls are serialized under
                        a lock so concurrent threads never corrupt the file.
        """
        if not call_fn and not dry_run:
            log.info("ai_analyser: no call_fn provided — skipping AI phase")
            return report_data

        build = report_data.get("build", {})
        cases = [
            (scenario, tc)
            for scenario in report_data.get("failures", [])
            for tc in scenario.get("test_cases", [])
            if self._needs_analysis(tc)
        ]

        if not cases:
            log.info("ai_analyser: nothing to analyse (all cases already have ai_analysis)")
            return report_data

        log.info("ai_analyser: processing %d test case(s) (parallel_requests=%d)",
                 len(cases), self.parallel_requests)

        if dry_run:
            for i, (_, tc) in enumerate(cases, 1):
                label  = tc.get("name", "")[:60]
                prompt = self.build_prompt(tc, build)
                print(f"\n{'─'*60}\n[{i}/{len(cases)}] {label}\n{'─'*60}")
                print(f"SYSTEM:\n{self.SYSTEM_PROMPT}\n\nUSER:\n{prompt}")
            return report_data

        total   = len(cases)
        counter = [0]
        lock    = _threading.Lock()

        def _analyse_one(pair: tuple) -> None:
            _, tc  = pair
            label  = tc.get("name", "")[:60]
            prompt = self.build_prompt(tc, build)
            try:
                raw    = call_fn(self.SYSTEM_PROMPT, prompt)
                result = self.parse_response(raw)
                tc["ai_analysis"] = result
                with lock:
                    counter[0] += 1
                    log.info("  [%d/%d] %s — tag: %s",
                             counter[0], total, label, result.get("tag", ""))
                    if save_fn:
                        save_fn(report_data)
            except Exception as exc:
                log.error("  %s — failed: %s", label, exc)
                tc["ai_analysis"] = {
                    "text":     f"Analysis failed: {exc}",
                    "tag":      "ANALYSIS ERROR",
                    "tag_type": "warn",
                }

        with ThreadPoolExecutor(max_workers=self.parallel_requests) as pool:
            list(pool.map(_analyse_one, cases))

        return report_data

    # ── Prompt builder ────────────────────────────────────────────────────────

    def build_prompt(self, tc: dict, build: dict) -> str:
        """Build the analysis prompt for a single test case."""
        parts = []

        parts.append(
            f"## Build\n"
            f"Build #{build.get('number', '?')}  "
            f"Branch: {build.get('branch', '?')}  "
            f"Ref: #{build.get('ref_build', '?')}"
        )

        if self.context:
            parts.append(f"## System context\n{self.context}")

        parts.append(
            f"## Failing test\n"
            f"Name: {tc.get('name', '')}\n"
            f"Status: {tc.get('status', 'fail').upper()}  "
            f"Duration: {tc.get('duration', '—')}"
        )

        msg  = tc.get("failure_message", "").strip()
        text = tc.get("failure_text",    "").strip()
        fail_block = "## Failure\n"
        if msg:
            fail_block += f"Message: {msg}\n"
        if text:
            fail_block += f"\nDetails:\n{text[:2000]}"
        if not msg and not text:
            fail_block += "(no failure details captured)"
        parts.append(fail_block)

        jira_ctx = [t for t in tc.get("jira_context") or [] if t.get("key")]
        if jira_ctx:
            lines = []
            for t in jira_ctx:
                tag = "■" if t.get("match") == "direct" else "·"
                lines.append(
                    f"{tag} {t['key']} [{t.get('type','?')} · {t.get('status','?')}] "
                    f"{t.get('priority','')} — {t.get('summary','')}"
                )
            parts.append("## JIRA tickets\n" + "\n".join(lines))

        history  = tc.get("history", [])
        hist_str = _history_label(history)
        note     = _history_note(history)
        if note:
            hist_str += f"\n{note}"
        if history:
            lines = [f"  #{h['build']}: {h['status']}" for h in history[-8:]]
            hist_str += "\n" + "\n".join(lines)
        parts.append(f"## History\n{hist_str}")

        log_content = self._load_log(tc)
        if log_content:
            parts.append(f"## Log\n{log_content[:3000]}")

        parts.append(
            "---\n"
            "Respond with ONLY this JSON object (no backticks, no extra text):\n"
            "{\n"
            '  "text":     "<2-3 sentence HTML diagnostic. Use <strong> for key values, '
            "<code> for identifiers/env vars/paths. State root cause + remediation.>\",\n"
            '  "tag":      "CATEGORY · SPECIFIC DETAIL (max 6 words, uppercase)",\n'
            '  "tag_type": "error"\n'
            "}\n"
            'tag_type must be exactly one of: "error" | "warn" | "ok"\n'
            "Be precise: name exact env vars, error codes, line numbers if available."
        )

        return "\n\n".join(parts)

    # ── Response parser ───────────────────────────────────────────────────────

    def parse_response(self, raw: str) -> dict:
        """Parse a raw LLM response into {text, tag, tag_type}."""
        cleaned = _CODE_FENCE.sub("", raw).strip()
        try:
            obj = json.loads(cleaned)
            return {
                "text":     str(obj.get("text",     "")).strip(),
                "tag":      str(obj.get("tag",      "AI ANALYSIS")).strip(),
                "tag_type": str(obj.get("tag_type", "error")).strip(),
            }
        except json.JSONDecodeError:
            log.warning("ai_analyser: response was not valid JSON — using raw text")
            return {
                "text":     cleaned[:500],
                "tag":      "AI ANALYSIS",
                "tag_type": "warn",
            }

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _needs_analysis(self, tc: dict) -> bool:
        if not self.skip_present:
            return True
        existing = tc.get("ai_analysis") or {}
        return not existing.get("text", "").strip()

    def _load_file(self, path_str: str | None, max_chars: int = 4000) -> str:
        if not path_str:
            return ""
        p = Path(path_str)
        if not p.exists():
            log.warning("ai_analyser: file not found — %s", p)
            return ""
        content = p.read_text(encoding="utf-8", errors="replace").strip()
        if len(content) > max_chars:
            content = content[:max_chars] + "\n… (truncated)"
        return content

    def _load_log(self, tc: dict) -> str:
        if tc.get("log_file"):
            return self._load_file(tc["log_file"], max_chars=3000)
        if self.log_dir:
            ld = Path(self.log_dir)
            for candidate in [
                ld / f"{_safe_name(tc.get('name', ''))}.log",
                ld / f"{tc.get('jira', '')}.log",
                ld / f"{_safe_name(tc.get('name', ''))}.txt",
            ]:
                if candidate.exists():
                    return self._load_file(str(candidate), max_chars=3000)
        return ""


# ── Standalone CLI — prompt preview ──────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(
        description="Preview AI prompts for a collected data JSON file.",
        epilog=(
            "Prints the system prompt and user prompt that would be sent to an LLM\n"
            "for each failing test case.  No API calls are made.\n\n"
            "Examples:\n"
            "  python ai_analyser.py data.json config.json\n"
            "  python ai_analyser.py data.json config.json --out prompts.txt\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("data",   type=Path, help="Collected data JSON")
    p.add_argument("config", type=Path, help="Config JSON containing the 'ai' block")
    p.add_argument("--out",  type=Path, default=None,
                   help="Write prompt output to file instead of stdout")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)-8s %(name)s — %(message)s")

    if not args.data.exists():
        sys.exit(f"Data file not found: {args.data}")
    if not args.config.exists():
        sys.exit(f"Config file not found: {args.config}")

    ai_cfg   = json.loads(args.config.read_text(encoding="utf-8")).get("ai", {})
    report   = json.loads(args.data.read_text(encoding="utf-8"))
    analyser = AIAnalyser(ai_cfg)

    if args.out:
        import io
        buf = io.StringIO()
        _orig_stdout, sys.stdout = sys.stdout, buf
        analyser.enrich(report, dry_run=True)
        sys.stdout = _orig_stdout
        args.out.write_text(buf.getvalue(), encoding="utf-8")
        print(f"Prompts written → {args.out}")
    else:
        analyser.enrich(report, dry_run=True)


if __name__ == "__main__":
    main()
