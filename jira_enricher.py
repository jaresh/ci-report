"""
jira_enricher.py
─────────────────
JIRA enrichment phase for the CI dashboard pipeline.

Sits between data collection and AI analysis:
    collect → [ jira_enricher.enrich() ] → [ ai_analyser.enrich() ] → render

For each failing test case the enricher:
  1. Fetches the ticket already referenced in the test case (if any) — "direct"
  2. Extracts meaningful terms from the test name + failure message
  3. Searches JIRA for related open tickets using JQL text search — "related"
  4. Attaches a jira_context list to the test case

The AI analyser reads jira_context and includes it in the prompt, giving
Claude the specific ticket history and status before it writes the diagnosis.

Result format added to each test case
──────────────────────────────────────
"jira_context": [
  {
    "key":      "PIPE-458",
    "summary":  "Token expiry during chunked upload",
    "status":   "Open",
    "type":     "Bug",
    "priority": "High",
    "url":      "https://jira.example.com/browse/PIPE-458",
    "match":    "direct"     ← "direct" | "related"
  },
  {
    "key":      "PIPE-459",
    "summary":  "Agent re-provision checklist missing env vars",
    "status":   "In Progress",
    "type":     "Task",
    "priority": "Medium",
    "url":      "...",
    "match":    "related"
  }
]

Config keys (under "jira" in config.json)
──────────────────────────────────────────
base_url         str   https://your-org.atlassian.net  [required]
email            str   service account email           [required for Cloud]
api_token_env    str   env var with API token          [default: JIRA_API_TOKEN]
project_keys     list  ["PIPE", "INFRA"]               [optional but recommended]
max_related      int   max related tickets to attach   [default: 4]
search_terms     int   top N terms to use in JQL       [default: 3]
skip_if_present  bool  skip test cases already enriched [default: true]
timeout          int   HTTP timeout in seconds          [default: 10]

Usage — standalone
──────────────────
    python jira_enricher.py data.json config.json
    python jira_enricher.py data.json config.json --dry-run
    python jira_enricher.py data.json config.json --out enriched.json

Usage — from generate_report.py
────────────────────────────────
    python generate_report.py --junit-xml --jira --ai 1247
"""

from __future__ import annotations

import argparse
import base64
import json
import logging
import os
import re
import sys
import threading as _threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

# Words that add no search value
_STOP = {
    "test", "the", "a", "an", "at", "in", "on", "for", "of", "and", "or",
    "not", "is", "it", "to", "be", "as", "by", "via", "with", "after",
    "before", "when", "under", "over", "into", "from", "that", "this",
    "should", "does", "can", "will", "are", "was", "were", "has", "have",
    "had", "fault", "injection", "case", "using", "load",
}

_JIRA_KEY_RE  = re.compile(r'\b([A-Z][A-Z0-9]+-\d+)\b')
_WORD_RE      = re.compile(r'\b[a-zA-Z]{3,}\b')
_UPPER_WORD   = re.compile(r'\b[A-Z][A-Z0-9_]{2,}\b')   # HTTP, CLI, ENV_VAR, etc.
_HTTP_CODE_RE = re.compile(r'\b[45]\d{2}\b')             # 403, 404, 500, …


# ── Enricher ──────────────────────────────────────────────────────────────────

class JiraEnricher:

    def __init__(self, jira_config: dict):
        self.cfg          = jira_config
        self.base_url     = jira_config.get("base_url", "").rstrip("/")
        self.email        = jira_config.get("email", "")
        self.api_token    = os.environ.get(
            jira_config.get("api_token_env", "JIRA_API_TOKEN"), ""
        )
        self.projects     = jira_config.get("project_keys", [])
        self.max_related  = int(jira_config.get("max_related", 4))
        self.n_terms      = int(jira_config.get("search_terms", 3))
        self.skip_present      = jira_config.get("skip_if_present", True)
        self.timeout           = int(jira_config.get("timeout", 10))
        self.parallel_requests = int(jira_config.get("parallel_requests", 8))
        self._auth             = self._make_auth_header()

    # ── Public ────────────────────────────────────────────────────────────────

    def enrich(self, report_data: dict, dry_run: bool = False) -> dict:
        """
        Attach jira_context to every failing test case that lacks one.
        Returns the modified report_data dict.
        """
        total = sum(
            1 for s in report_data.get("failures", [])
            for tc in s.get("test_cases", [])
            if self._needs_enrichment(tc)
        )

        if total == 0:
            log.info("jira_enricher: nothing to enrich")
            return report_data

        if not self.base_url:
            log.error("jira_enricher: base_url not set in config — skipping")
            return report_data

        if not self.api_token and not dry_run:
            log.error("jira_enricher: %s not set — skipping",
                      self.cfg.get("api_token_env", "JIRA_API_TOKEN"))
            return report_data

        log.info("jira_enricher: enriching %d test case(s) (parallel_requests=%d)",
                 total, self.parallel_requests)

        cases = [
            tc
            for scenario in report_data.get("failures", [])
            for tc in scenario.get("test_cases", [])
            if self._needs_enrichment(tc)
        ]

        if dry_run:
            for tc in cases:
                label      = tc.get("name", "")[:60]
                jql        = self._build_jql(tc)
                direct_key = self._direct_key(tc)
                print(f"\n{'─'*60}")
                print(f"Test case : {label}")
                if direct_key:
                    print(f"Direct    : GET /issue/{direct_key}")
                print(f"Search JQL: {jql}")
                tc["jira_context"] = []
            return report_data

        counter = [0]
        lock    = _threading.Lock()

        def _enrich_one(tc: dict) -> None:
            tc["jira_context"] = self._find_tickets(tc)
            with lock:
                counter[0] += 1
                log.info("  [%d/%d] %s → %d ticket(s)",
                         counter[0], total,
                         tc.get("name", "")[:60],
                         len(tc["jira_context"]))

        with ThreadPoolExecutor(max_workers=self.parallel_requests) as pool:
            list(pool.map(_enrich_one, cases))

        return report_data

    # ── Core lookup ───────────────────────────────────────────────────────────

    def _find_tickets(self, tc: dict) -> list[dict]:
        results: list[dict] = []

        # 1. Direct lookup — ticket already referenced in test case
        direct_key = self._direct_key(tc)
        if direct_key:
            issue = self._get_issue(direct_key)
            if issue:
                issue["match"] = "direct"
                results.append(issue)

        # 2. Text search for related open tickets
        jql = self._build_jql(tc)
        if jql:
            try:
                related = self._search(jql)
                for r in related:
                    if r["key"] != direct_key and len(results) < self.max_related + 1:
                        r["match"] = "related"
                        results.append(r)
            except Exception as exc:
                log.warning("    search failed: %s", exc)

        return results

    def _direct_key(self, tc: dict) -> str:
        """Return the JIRA key already associated with this test case, if any."""
        # Explicit jira field
        if tc.get("jira"):
            return tc["jira"]
        # Key embedded in test name like [PIPE-458]
        m = _JIRA_KEY_RE.search(tc.get("name", ""))
        return m.group(1) if m else ""

    # ── Term extraction ───────────────────────────────────────────────────────

    def _extract_terms(self, tc: dict) -> list[str]:
        """
        Pull meaningful search terms from the test case.
        Checks search_hint first (set by tools or manually in the JSON)
        before falling back to automatic extraction from name + failure.
        """
        # Explicit hint wins — tools or authors know best
        hint = (tc.get("search_hint") or "").strip()
        if hint:
            words = _WORD_RE.findall(hint)
            return [w for w in words if w.lower() not in _STOP][: self.n_terms * 2]

        terms: list[str] = []

        # HTTP/error codes (e.g. 403, 503) — very specific, high priority
        for src in (tc.get("failure_message", ""), tc.get("failure_text", "")):
            terms += _HTTP_CODE_RE.findall(src)

        # Uppercase identifiers (ENV_VARS, ClassName, HTTP) — high value
        name = _JIRA_KEY_RE.sub("", tc.get("name", ""))
        for src in (name, tc.get("failure_message", "")[:300]):
            terms += [w for w in _UPPER_WORD.findall(src) if w not in _STOP]

        # Regular words from test name — deduplicated, stop words removed
        for w in _WORD_RE.findall(name.lower()):
            if w not in _STOP and len(w) > 3:
                terms.append(w)

        # Deduplicate while preserving order
        seen: set = set()
        unique: list[str] = []
        for t in terms:
            tl = t.lower()
            if tl not in seen:
                seen.add(tl)
                unique.append(t)

        return unique[:self.n_terms * 2]     # keep extras; caller will slice

    def _build_jql(self, tc: dict) -> str:
        terms = self._extract_terms(tc)[: self.n_terms]
        if not terms:
            return ""

        text_clause = " OR ".join(f'text ~ "{t}"' for t in terms)
        parts = []

        if self.projects:
            proj_list = ", ".join(self.projects)
            parts.append(f"project in ({proj_list})")

        parts.append(f"({text_clause})")
        parts.append("ORDER BY updated DESC")

        # Join non-ORDER parts with AND, append ORDER
        return " AND ".join(parts[:-1]) + " " + parts[-1]

    # ── JIRA API ──────────────────────────────────────────────────────────────

    def _get_issue(self, key: str) -> Optional[dict]:
        """Fetch a single issue by key."""
        url = f"{self.base_url}/rest/api/2/issue/{key}"
        try:
            data = self._http_get(url, params={"fields": "summary,status,issuetype,priority"})
            return self._normalise(data)
        except Exception as exc:
            log.warning("    could not fetch %s: %s", key, exc)
            return None

    def _search(self, jql: str) -> list[dict]:
        """Run a JQL search and return normalised issue list."""
        url = f"{self.base_url}/rest/api/2/search"
        data = self._http_get(url, params={
            "jql":        jql,
            "maxResults": self.max_related + 5,
            "fields":     "summary,status,issuetype,priority",
        })
        return [self._normalise(i) for i in data.get("issues", [])]

    def _normalise(self, issue: dict) -> dict:
        f = issue.get("fields", {})
        key = issue.get("key", "")
        return {
            "key":      key,
            "summary":  f.get("summary", ""),
            "status":   (f.get("status")   or {}).get("name", ""),
            "type":     (f.get("issuetype") or {}).get("name", ""),
            "priority": (f.get("priority")  or {}).get("name", ""),
            "url":      f"{self.base_url}/browse/{key}",
            "match":    "",     # set by caller
        }

    # ── HTTP ──────────────────────────────────────────────────────────────────

    def _http_get(self, url: str, params: dict = None) -> dict:
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": self._auth,
                "Accept":        "application/json",
                "Content-Type":  "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read())

    def _make_auth_header(self) -> str:
        if self.email and self.api_token:
            token = base64.b64encode(f"{self.email}:{self.api_token}".encode()).decode()
            return f"Basic {token}"
        if self.api_token:
            return f"Bearer {self.api_token}"
        return ""

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _needs_enrichment(self, tc: dict) -> bool:
        if not self.skip_present:
            return True
        ctx = tc.get("jira_context")
        return ctx is None     # empty list [] is fine; None means never ran


# ── Standalone CLI ────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(
        description="JIRA enrichment phase for CI dashboard data.",
        epilog=(
            "Examples:\n"
            "  python jira_enricher.py data.json config.json\n"
            "  python jira_enricher.py data.json config.json --dry-run\n"
            "  python jira_enricher.py data.json config.json --out enriched.json\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("data",      type=Path, help="Collected data JSON")
    p.add_argument("config",    type=Path, help="Config JSON with a 'jira' block")
    p.add_argument("--out",     type=Path, default=None,
                   help="Output path  [default: <data>_jira.json]")
    p.add_argument("--dry-run", action="store_true",
                   help="Print JQL queries to stdout; make no API calls")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(levelname)-8s %(name)s — %(message)s")

    if not args.data.exists():
        sys.exit(f"Data file not found: {args.data}")
    if not args.config.exists():
        sys.exit(f"Config file not found: {args.config}")

    cfg  = json.loads(args.config.read_text(encoding="utf-8"))
    data = json.loads(args.data.read_text(encoding="utf-8"))

    enricher = JiraEnricher(cfg.get("jira", {}))
    enriched = enricher.enrich(data, dry_run=args.dry_run)

    out = args.out or args.data.with_name(args.data.stem + "_jira.json")
    out.write_text(json.dumps(enriched, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n✓ Enriched data written → {out}")


if __name__ == "__main__":
    main()
