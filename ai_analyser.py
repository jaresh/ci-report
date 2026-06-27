"""
ai_analyser.py
──────────────
AI analysis phase for the CI dashboard pipeline.

Sits between data collection and report rendering:
    collect → [ ai_analyser.enrich() ] → render

For each failing test case that has no ai_analysis yet, the analyser:
  1. Assembles a prompt from: failure message, stacktrace, history,
     system context file, and optional local log file
  2. Calls the Claude API (uses anthropic SDK if installed, else urllib)
  3. Parses the JSON response into {text, tag, tag_type}
  4. Writes the result back into the data dict

The caller never sees API calls — just passes in the raw merged dict
and gets back the same dict with ai_analysis populated.

Config keys (under "ai" in run.json)
─────────────────────────────────────
enabled           bool   master switch                      [required]
model             str    Claude model                       [default: claude-sonnet-4-6]
context_file      str    path to plain-text system context  [optional]
log_dir           str    directory for local .log files     [optional]
max_tokens        int    max tokens per call                [default: 600]
api_key_env       str    env var holding the API key        [default: ANTHROPIC_API_KEY]
delay_between     float  seconds to wait between API calls  [default: 0.5]
skip_if_present   bool   skip cases that already have text  [default: true]

Usage — standalone
──────────────────
    python ai_analyser.py data.json run.json          # writes data_analysed.json
    python ai_analyser.py data.json run.json --dry-run # prints prompts, no API calls

Usage — from generate_report.py
────────────────────────────────
    from ai_analyser import AIAnalyser
    analyser = AIAnalyser(config["ai"])
    merged   = analyser.enrich(merged, build_info=merged["build"])
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

_SAFE = re.compile(r"[^\w\-]")
_CODE_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


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
        self.cfg          = ai_config
        self.model        = ai_config.get("model", "claude-sonnet-4-6")
        self.max_tokens   = int(ai_config.get("max_tokens", 600))
        self.delay        = float(ai_config.get("delay_between", 0.5))
        self.skip_present = ai_config.get("skip_if_present", True)
        self.log_dir      = ai_config.get("log_dir")
        self.context      = self._load_file(ai_config.get("context_file"))
        self._api_key     = os.environ.get(
            ai_config.get("api_key_env", "ANTHROPIC_API_KEY"), ""
        )

    # ── Public entry point ────────────────────────────────────────────────────

    def enrich(self, report_data: dict, dry_run: bool = False,
               save_fn=None) -> dict:
        """
        Walk every failing test case in report_data["failures"],
        generate AI analysis for those that lack one, and return
        the enriched dict.

        save_fn — optional callable(report_data) called after every
        successful API response so partial results survive interruptions.
        """
        build   = report_data.get("build", {})
        total   = sum(
            1 for s in report_data.get("failures", [])
            for tc in s.get("test_cases", [])
            if self._needs_analysis(tc)
        )

        if total == 0:
            log.info("ai_analyser: nothing to analyse (all cases already have ai_analysis)")
            return report_data

        log.info("ai_analyser: analysing %d test case(s) with %s", total, self.model)

        done = 0
        for scenario in report_data.get("failures", []):
            for tc in scenario.get("test_cases", []):
                if not self._needs_analysis(tc):
                    continue

                done += 1
                label = tc.get("name", "")[:60]
                log.info("  [%d/%d] %s", done, total, label)

                prompt = self._build_prompt(tc, build)

                if dry_run:
                    print(f"\n{'─'*60}\nPROMPT for: {label}\n{'─'*60}")
                    print(prompt)
                    tc["ai_analysis"] = {
                        "text":     "<em>(dry-run — no API call made)</em>",
                        "tag":      "DRY RUN",
                        "tag_type": "warn",
                    }
                    continue

                if not self._api_key:
                    log.error("ai_analyser: %s is not set — skipping",
                              self.cfg.get("api_key_env", "ANTHROPIC_API_KEY"))
                    break

                try:
                    raw    = self._call_with_retry(prompt)
                    result = self._parse_response(raw)
                    tc["ai_analysis"] = result
                    log.info("    tag: %s", result.get("tag", ""))
                    if save_fn:
                        save_fn(report_data)   # incremental save — survives interruption
                except Exception as exc:
                    log.error("    failed: %s", exc)
                    tc["ai_analysis"] = {
                        "text":     f"Analysis failed: {exc}",
                        "tag":      "ANALYSIS ERROR",
                        "tag_type": "warn",
                    }

                if done < total:
                    time.sleep(self.delay)

        return report_data

    # ── Prompt builder ────────────────────────────────────────────────────────

    def _build_prompt(self, tc: dict, build: dict) -> str:
        parts = []

        # Build context
        parts.append(
            f"## Build\n"
            f"Build #{build.get('number', '?')}  "
            f"Branch: {build.get('branch', '?')}  "
            f"Ref: #{build.get('ref_build', '?')}"
        )

        # System context (from external file)
        if self.context:
            parts.append(f"## System context\n{self.context}")

        # Test case identity
        parts.append(
            f"## Failing test\n"
            f"Name: {tc.get('name', '')}\n"
            f"Status: {tc.get('status', 'fail').upper()}  "
            f"Duration: {tc.get('duration', '—')}"
        )

        # Failure message and stacktrace
        msg  = tc.get("failure_message", "").strip()
        text = tc.get("failure_text",    "").strip()
        fail_block = "## Failure\n"
        if msg:
            fail_block += f"Message: {msg}\n"
        if text:
            fail_block += f"\nDetails:\n{text[:2000]}"   # cap at 2 000 chars
        if not msg and not text:
            fail_block += "(no failure details captured)"
        parts.append(fail_block)

        # JIRA context (populated by the JIRA enrichment phase, if it ran)
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

        # History
        history  = tc.get("history", [])
        h_label  = _history_label(history)
        h_note   = _history_note(history)
        hist_str = h_label
        if h_note:
            hist_str += f"\n{h_note}"
        if history:
            lines = [f"  #{h['build']}: {h['status']}" for h in history[-8:]]
            hist_str += "\n" + "\n".join(lines)
        parts.append(f"## History\n{hist_str}")

        # Local log file (optional)
        log_content = self._load_log(tc)
        if log_content:
            parts.append(f"## Log\n{log_content[:3000]}")

        # Output instructions
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

    # ── API call ──────────────────────────────────────────────────────────────

    def _call_with_retry(self, prompt: str, max_retries: int = 3) -> str:
        last_exc: Optional[Exception] = None
        for attempt in range(max_retries):
            try:
                return self._call_api(prompt)
            except Exception as exc:
                last_exc = exc
                if attempt < max_retries - 1:
                    wait = 2 ** attempt
                    log.warning("    attempt %d failed: %s — retrying in %ds",
                                attempt + 1, exc, wait)
                    time.sleep(wait)
        raise last_exc  # type: ignore[misc]

    def _call_api(self, prompt: str) -> str:
        """Try anthropic SDK first, fall back to urllib (stdlib)."""

        # ── anthropic SDK ──────────────────────────────────────────────────
        try:
            import anthropic  # type: ignore
            client = anthropic.Anthropic(api_key=self._api_key)
            msg = client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=self.SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            )
            return msg.content[0].text
        except ImportError:
            pass  # fall through to urllib

        # ── urllib fallback (no extra deps) ────────────────────────────────
        import urllib.request

        payload = json.dumps({
            "model":      self.model,
            "max_tokens": self.max_tokens,
            "system":     self.SYSTEM_PROMPT,
            "messages":   [{"role": "user", "content": prompt}],
        }).encode()

        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=payload,
            headers={
                "x-api-key":         self._api_key,
                "anthropic-version": "2023-06-01",
                "content-type":      "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=45) as resp:
            data = json.loads(resp.read())
        return data["content"][0]["text"]

    # ── Response parsing ──────────────────────────────────────────────────────

    def _parse_response(self, raw: str) -> dict:
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

    def _load_file(self, path_str: Optional[str], max_chars: int = 4000) -> str:
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
        """Try to find a local log file for the test case."""
        # 1. Explicit log_file field on the test case
        if tc.get("log_file"):
            return self._load_file(tc["log_file"], max_chars=3000)

        # 2. log_dir from AI config + safe test name
        if self.log_dir:
            ld = Path(self.log_dir)
            candidates = [
                ld / f"{_safe_name(tc.get('name', ''))}.log",
                ld / f"{tc.get('jira', '')}.log",
                ld / f"{_safe_name(tc.get('name', ''))}.txt",
            ]
            for c in candidates:
                if c.exists():
                    return self._load_file(str(c), max_chars=3000)

        return ""


# ── Standalone CLI ────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(
        description="Run AI analysis phase on a collected data JSON file.",
        epilog=(
            "Examples:\n"
            "  python ai_analyser.py data.json run.json\n"
            "  python ai_analyser.py data.json run.json --dry-run\n"
            "  python ai_analyser.py data.json run.json --out enriched.json\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("data",      type=Path, help="Collected data JSON (from generate_report.py)")
    p.add_argument("config",    type=Path, help="Run config JSON containing the 'ai' block")
    p.add_argument("--out",     type=Path, default=None,
                   help="Output path [default: <data>_analysed.json]")
    p.add_argument("--dry-run", action="store_true",
                   help="Print prompts to stdout, make no API calls")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)-8s %(name)s — %(message)s",
    )

    if not args.data.exists():
        sys.exit(f"Data file not found: {args.data}")
    if not args.config.exists():
        sys.exit(f"Config file not found: {args.config}")

    run_cfg  = json.loads(args.config.read_text(encoding="utf-8"))
    ai_cfg   = run_cfg.get("ai", {})
    if not ai_cfg.get("enabled", False) and not args.dry_run:
        sys.exit('AI is disabled in config (ai.enabled is false). Pass --dry-run to test prompts.')

    report   = json.loads(args.data.read_text(encoding="utf-8"))
    analyser = AIAnalyser(ai_cfg)
    enriched = analyser.enrich(report, dry_run=args.dry_run)

    out_path = args.out or args.data.with_name(args.data.stem + "_analysed.json")
    out_path.write_text(json.dumps(enriched, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n✓ Enriched data written → {out_path}")


if __name__ == "__main__":
    main()
