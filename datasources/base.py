"""
datasources/base.py
───────────────────
Abstract DataSource class, Profiler, and result merger.

To add a new tool:
    1. Create  datasources/tool_<name>.py  and subclass DataSource
    2. Import it in generate_report.py and add an entry to TOOLS
    3. argparse will automatically generate a --<name> flag for it
"""

from __future__ import annotations

import contextlib
import logging
import time
from abc import ABC, abstractmethod
from typing import NotRequired, Optional, TypedDict

log = logging.getLogger(__name__)


# ── Profiler ──────────────────────────────────────────────────────────────────

class Profiler:
    """
    Lightweight timing recorder for one collect() call.

    Usage inside a tool:

        prof = Profiler()
        with prof.span("connect_s"):
            conn = self._connect(...)
        with prof.span("failures_query_s"):
            rows = self._query(...)

    The recorded spans are returned under the "profiling" key in collect()
    output so agents can read them from the saved .data.json file and decide
    which operations to optimise (e.g. raise history_limit, add connection
    pooling, or skip a slow JIRA phase).
    """

    def __init__(self) -> None:
        self.spans: dict[str, float] = {}

    @contextlib.contextmanager
    def span(self, name: str):
        t0 = time.perf_counter()
        try:
            yield
        finally:
            self.spans[name] = round(time.perf_counter() - t0, 3)

    def to_dict(self) -> dict[str, float]:
        return dict(self.spans)


# ── Report schema types ─────────────────────────────────────────────────────────
# Single source of truth for the JSON contract every collect() must satisfy.
# See CLAUDE.md "The JSON output contract". With `from __future__ import
# annotations` these cost nothing at runtime — they exist for the type checker,
# IDE autocompletion, and as living documentation that cannot drift from code.

class HistoryEntry(TypedDict):
    build: str
    status: str
    duration: str
    current: NotRequired[bool]             # only the current-build entry sets this


class TestCase(TypedDict):
    name: str
    status: str                            # "fail" | "error" | "timeout"
    duration: str
    failure_message: str
    failure_text: str
    jira: str
    jira_url: str
    task_url: str
    log_url: str
    history: list[HistoryEntry]
    ai_analysis: dict                      # always present; AI phase fills it in
    log_file: NotRequired[str]             # local path to a fetched log; AI excerpts it
    search_hint: NotRequired[str]
    jira_context: NotRequired[list[dict]]  # added by the JIRA phase


class Scenario(TypedDict):
    scenario: str
    config: str
    jira: str
    jira_url: str
    test_cases: list[TestCase]


class Metric(TypedDict):
    name: str
    unit: str
    direction: str                         # "higher_better" | "lower_better"
    current: float
    reference: float
    history_values: list[float]
    history_builds: list[str]              # same length as history_values


class ModelPerformance(TypedDict):
    model: str
    summary_note: str
    summary_chips: Optional[list[dict]]    # None = auto-derive in prepare_data()
    metrics: list[Metric]


class LogProfiling(TypedDict):
    count: int                             # log URLs attempted
    ok: int
    failed: int
    cached: int                            # skipped because already on disk
    workers: int                           # parallelism actually used
    wall_s: float                          # wall-clock of the parallel fetch
    sum_s: float                           # Σ per-file durations (sequential-equivalent)
    speedup: float                         # sum_s / wall_s → parallel efficiency
    bytes_total: int                       # bytes actually downloaded
    throughput_mb_s: float
    connect_sum_s: float                   # Σ connect/TTFB time (reveals keep-alive wins)
    transfer_sum_s: float                  # Σ body-read time
    per_file_s: dict[str, float]           # min / p50 / p95 / max / mean
    slowest: list[dict]                    # top-N slowest files for investigation


class ToolProfiling(TypedDict):
    tool: str
    total_s: float
    spans: dict[str, float]
    logs: NotRequired[LogProfiling]        # present when the tool fetched logs


class ToolOutput(TypedDict, total=False):
    """Return type of DataSource.collect(). Every key is optional — an empty
    {} is a valid return (config error or no data)."""
    failures: list[Scenario]
    performance: list[ModelPerformance]
    profiling: ToolProfiling
    build: dict


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
    def collect(self, config: dict) -> ToolOutput:
        """
        Run the data source and return a partial report dict.
        `config` is the value of the matching key in config.json.
        """


# ── Merger ────────────────────────────────────────────────────────────────────

def merge_results(base_config: dict, tool_outputs: list[ToolOutput]) -> dict:
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
        "profiling":   {"tools": {}},
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
        if "profiling" in output:
            prof = output["profiling"]
            tool_name = prof.get("tool", "unknown")
            tool_prof = {
                "total_s": prof.get("total_s", 0.0),
                "spans":   prof.get("spans", {}),
            }
            if "logs" in prof:
                tool_prof["logs"] = prof["logs"]
            result["profiling"]["tools"][tool_name] = tool_prof

    fail_count = sum(
        len(s.get("test_cases", []))
        for s in result["failures"]
    )
    result["build"].setdefault("badges", {})
    result["build"]["badges"]["failed"] = fail_count

    return result
