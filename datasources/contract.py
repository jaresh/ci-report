"""
datasources/contract.py
───────────────────────
Shared builders that turn raw DB rows into the report JSON contract
(the TypedDicts in datasources/base.py). These helpers are schema- and
driver-agnostic: a data source tool maps its own rows onto the column names
used here, then calls these helpers to assemble the contract pieces.

Keeping them in one place means the contract-construction logic is defined
once. Per-framework tools differ only in their SQL and their row→column
mapping (`_collect_failures` / `_collect_performance`), not in how the final
JSON is shaped.
"""

from __future__ import annotations

import re
from collections import defaultdict

from .base import HistoryEntry, Metric, ModelPerformance

_SAFE_RE = re.compile(r'[^\w\-]')


def _safe(name: str) -> str:
    return _SAFE_RE.sub('_', name)[:80]


def _fmt_dur(secs) -> str:
    try:
        s = float(secs or 0)
    except (TypeError, ValueError):
        return "—"
    if s <= 0:
        return "—"
    if s >= 60:
        m, r = divmod(s, 60)
        return f"{int(m)}m {r:.0f}s"
    return f"{s:.1f}s"


def _sort_builds(builds) -> list:
    try:
        return sorted(builds, key=int)
    except (ValueError, TypeError):
        return sorted(builds)


def _group_history(rows: list, limit: int) -> dict:
    """Return {name: [rows DESC, capped at limit]} from a batch history query."""
    result: dict = {}
    for row in rows:   # already ordered (name, ran_at DESC) by the query
        name    = str(row["name"])
        entries = result.setdefault(name, [])
        if len(entries) < limit:
            entries.append(row)
    return result


def _history_list(hist_entries: list, cur_status: str, cur_dur: str) -> list[HistoryEntry]:
    """Convert DESC-ordered history rows to a chronological list + current entry."""
    entries: list[HistoryEntry] = []
    for row in reversed(hist_entries):
        entries.append({
            "build":    str(row["build"]),
            "status":   str(row["status"]),
            "duration": _fmt_dur(row.get("duration_s")),
        })
    entries.append({"build": "current", "status": cur_status, "duration": cur_dur, "current": True})
    return entries


def _build_performance(rows: list, current_build: str, ref_build: str, limit: int) -> list[ModelPerformance]:
    """
    Group flat performance rows into the per-model structure the template expects.
    Models and their order are discovered from the data — no config list needed.
    The global build window (last `limit` builds across all models) is computed
    once so all models share the same x-axis.
    """
    groups: dict      = defaultdict(list)
    all_builds_seen: set = set()

    for row in rows:
        key = (str(row["model"]), str(row["metric_name"]))
        groups[key].append(row)
        all_builds_seen.add(str(row["build"]))

    # Shared x-axis: last `limit` builds across the whole table
    window     = set(_sort_builds(all_builds_seen)[-limit:])
    # Models in the order they first appear in the query result
    models_ord = list(dict.fromkeys(str(r["model"]) for r in rows))

    performance: list[ModelPerformance] = []
    for model_name in models_ord:
        model_keys = [k for k in groups if k[0] == model_name]
        if not model_keys:
            continue

        # Builds that exist for this model within the window
        model_window_builds = _sort_builds(
            {str(r["build"]) for key in model_keys for r in groups[key]} & window
        )
        build_window_set = set(model_window_builds)

        metrics: list[Metric] = []
        for key in model_keys:
            metric_name  = key[1]
            filtered     = [r for r in groups[key] if str(r["build"]) in build_window_set]
            build_to_val = {str(r["build"]): float(r["value"]) for r in filtered}
            history_values = [build_to_val.get(b, 0.0) for b in model_window_builds]

            current   = build_to_val.get(str(current_build))
            if current is None:
                current = history_values[-1] if history_values else 0.0
            reference = build_to_val.get(str(ref_build), current)

            if not filtered:
                continue

            sample = filtered[0]
            metrics.append({
                "name":           metric_name,
                "unit":           str(sample["unit"]),
                "direction":      str(sample["direction"]),
                "current":        current,
                "reference":      reference,
                "history_values": history_values,
                "history_builds": model_window_builds,
            })

        if not metrics:
            continue

        performance.append({
            "model":         model_name,
            "summary_note":  "",
            "summary_chips": None,
            "metrics":       metrics,
        })

    return performance
