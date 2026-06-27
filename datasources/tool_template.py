"""
datasources/tool_template.py
─────────────────────────────
Scaffold for a new data source tool.

Steps to create your own tool:
  1. Copy this file to  datasources/tool_<name>.py
  2. Fill in name, description, and collect()
  3. Add to generate_report.py:
       from datasources.tool_<name> import <Name>Source
       TOOLS["<name>"] = <Name>Source()
  4. argparse automatically adds  --<name>  to the CLI
  5. Add a  "<name>": { ...settings... }  block to config.json

The collect() contract
─────────────────────
Return a dict with zero or more of these top-level keys:

    {
      "failures": [           # list of scenario dicts
        {
          "scenario":  "Suite name shown in report",
          "config":    "Agent / environment label",
          "jira":      "PROJ-123",
          "jira_url":  "https://jira.example.com/browse/PROJ-123",
          "test_cases": [
            {
              "name":            "Test case display name",
              "duration":        "3.2s",
              "status":          "fail",          # fail | error | timeout
              "jira":            "PROJ-456",
              "jira_url":        "...",
              "task_url":        "...",
              "log_url":         "...",
              "failure_message": "short error line",
              "failure_text":    "full stacktrace or log snippet",
              "search_hint":     "optional terms for JIRA search",
              "history": [
                {"build": "1245", "status": "pass", "duration": "3.1s"},
                {"build": "1247", "status": "fail", "duration": "3.2s",
                 "current": True}
              ],
              "ai_analysis": {}   # leave empty; AI phase fills it in
            }
          ]
        }
      ],

      "performance": [        # list of model dicts
        {
          "model":        "Display name (e.g. 'JFrog CLI · Linux arm64')",
          "summary_note": "Optional footer text",
          "summary_chips": None,   # None = auto-derive from first 2 metrics
          "metrics": [
            {
              "name":          "Upload Throughput",
              "unit":          "MB/s",
              "direction":     "higher_better",   # or "lower_better"
              "current":       87.3,
              "reference":     82.9,
              "history_values": [74.0, 77.1, 78.4, 79.2, 81.0, 83.3, 85.1, 87.3],
              "history_builds": ["1240", "1241", "1242", "1243", "1244", "1245", "1246", "1247"]
            }
          ]
        }
      ],

      "build": {              # optional partial override of base build config
        "duration": "18m 43s",
        "badges": { "passed": 34 }
      }
    }

All keys are optional — return only what your source provides.
Returning {} is valid (e.g. source found no failures today).
"""

from __future__ import annotations

import logging
from .base import DataSource

log = logging.getLogger(__name__)


class TemplateSource(DataSource):
    """
    Replace this docstring with a description of what your tool reads.
    """

    @property
    def name(self) -> str:
        # Must match the key you'll add to TOOLS in generate_report.py
        # and the section name in config.json.
        return "template"

    @property
    def description(self) -> str:
        return "Brief one-line description shown in --help and tool discovery"

    def collect(self, config: dict) -> dict:
        """
        Read data from your source and return a partial report dict.

        `config` is the value of the "template" key in config.json, e.g.:
            {
              "url":      "https://...",
              "token_env": "MY_API_TOKEN",
              "project":  "my-project"
            }

        Guidelines:
          · Never raise — catch exceptions, log them, and return {} or partial data.
          · Use config for all settings; never hardcode paths or credentials.
          · Read credentials from environment variables referenced by config keys.
          · Log at INFO level so --verbose shows what's happening.
          · Return only the keys your source actually has data for.
        """
        import os

        log.info("template: starting collection")

        # Example: read a credential from the environment
        token_env = config.get("token_env", "MY_API_TOKEN")
        token = os.environ.get(token_env, "")
        if not token:
            log.warning("template: %s not set — skipping", token_env)
            return {}

        # ── Your implementation here ──────────────────────────────────────────

        failures    = []   # populate from your data source
        performance = []   # populate from your data source

        log.info("template: collected %d scenario(s), %d model(s)",
                 len(failures), len(performance))

        return {
            "failures":    failures,
            "performance": performance,
        }
