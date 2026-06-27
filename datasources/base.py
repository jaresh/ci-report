"""
datasources/base.py
───────────────────
Abstract DataSource class and result merger.

To add a new tool:
    1. Create  datasources/tool_<name>.py  and subclass DataSource
    2. Import it in generate_report.py and add an entry to TOOLS
    3. argparse will automatically generate a --<name> flag for it
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

log = logging.getLogger(__name__)


# ── Abstract base ─────────────────────────────────────────────────────────────

class DataSource(ABC):
    """
    Base class for all data source tools.

    collect() must return a dict with zero or more of these keys:

        {
          "build":       { ...fields that override the base build config... },
          "failures":    [ ...scenario dicts per report schema... ],
          "performance": [ ...model dicts per report schema... ],
        }

    Partial or empty returns are fine — missing keys are skipped in the merge.
    Never raise from collect(): log the error and return {}.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique key used in TOOLS dict and as the config section name."""

    @property
    def description(self) -> str:
        return "(no description)"

    @abstractmethod
    def collect(self, config: dict) -> dict:
        """
        Run the data source and return a partial report dict.
        `config` is the value of the matching key in config.json.
        """


# ── Merger ────────────────────────────────────────────────────────────────────

def merge_results(base_config: dict, tool_outputs: list[dict]) -> dict:
    """
    Combine the base build config with all tool outputs into one report dict.

    Merge rules
    ───────────
    build        dict update — tool values win over base config values
    failures     list concat — ordered by tool order on the CLI
    performance  list concat — ordered by tool order on the CLI

    The failure badge count is always recomputed from the merged data.
    """
    result: dict = {
        "build":       dict(base_config.get("build", {})),
        "failures":    list(base_config.get("failures", [])),
        "performance": list(base_config.get("performance", [])),
    }

    for output in tool_outputs:
        if not output:
            continue
        if "build" in output:
            if "badges" in output["build"] and "badges" in result["build"]:
                result["build"]["badges"].update(output["build"].pop("badges"))
            result["build"].update(output["build"])
        result["failures"].extend(output.get("failures", []))
        result["performance"].extend(output.get("performance", []))

    fail_count = sum(
        len(s.get("test_cases", []))
        for s in result["failures"]
    )
    result["build"].setdefault("badges", {})
    result["build"]["badges"]["failed"] = fail_count

    return result
