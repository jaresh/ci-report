"""
datasources/tool_metrics_json.py
─────────────────────────────────
Data source: structured metrics JSON file reader.

Reads one or more JSON files produced by build instrumentation —
wrapper scripts around JFrog CLI, curl upload tools, monitoring
agents, or any process that captures throughput/latency data
during artifact operations.

Each file describes one model (agent/config combination) and its
per-metric history across recent builds.

Metrics file format
───────────────────
{
  "model": "JFrog CLI 2.x · Linux arm64",     ← displayed in report
  "summary_note": "optional footer text",      ← optional
  "metrics": [
    {
      "name":      "Upload Throughput",
      "unit":      "MB/s",
      "direction": "higher_better",            ← or "lower_better"
      "current":   87.3,                       ← value for this build
      "reference": 82.9                        ← value for ref build
    }
  ],
  "history": {
    "builds": ["1240", "1241", ..., "1247"],   ← build numbers oldest→newest
    "Upload Throughput": [74.0, 77.1, ..., 87.3]
  }
}

history.builds and history.<metric name> must have the same length.
The last entry in each history array is treated as the current build value
and should match "current" in the metric definition.

Run config keys
───────────────
files            list  paths to metrics JSON files (one per model)  [required]
summary_chips    list  override auto-derived summary chips           [optional]

Example run config entry
────────────────────────
{
  "name": "metrics_json",
  "enabled": true,
  "config": {
    "files": [
      "./metrics/jfrog-arm64.json",
      "./metrics/curl-fallback.json",
      "./metrics/jfrog-windows.json"
    ]
  }
}
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from .base import DataSource

log = logging.getLogger(__name__)


class MetricsJSONSource(DataSource):

    @property
    def name(self) -> str:
        return "metrics_json"

    @property
    def description(self) -> str:
        return "Reads structured metrics JSON files from build instrumentation"

    # ── Entry point ───────────────────────────────────────────────────────────

    def collect(self, config: dict) -> dict:
        file_paths = config.get("files", [])
        if not file_paths:
            log.warning("metrics_json: no 'files' specified in config")
            return {}

        models = []
        for fp in file_paths:
            path = Path(fp)
            if not path.exists():
                log.warning("metrics_json: file not found — %s", path)
                continue
            try:
                raw   = json.loads(path.read_text(encoding="utf-8"))
                model = self._parse_model(raw, config)
                if model:
                    models.append(model)
                    log.debug("metrics_json: loaded model %r from %s", model["model"], path)
            except Exception as exc:
                log.error("metrics_json: error reading %s — %s", path, exc)

        return {"performance": models}

    # ── Model parsing ─────────────────────────────────────────────────────────

    def _parse_model(self, data: dict, config: dict):
        model_name = data.get("model")
        if not model_name:
            log.warning("metrics_json: skipping entry — 'model' key missing")
            return None

        history_block = data.get("history", {})
        builds_list   = history_block.get("builds", [])
        raw_metrics   = data.get("metrics", [])

        metrics = []
        for m in raw_metrics:
            name      = m.get("name", "")
            direction = m.get("direction", "higher_better")
            current   = m.get("current")
            reference = m.get("reference")

            if current is None:
                log.warning("metrics_json: skipping %r in %r — 'current' missing", name, model_name)
                continue

            history_vals = [float(v) for v in history_block.get(name, [])]

            metrics.append({
                "name":           name,
                "unit":           m.get("unit", ""),
                "direction":      direction,
                "current":        float(current),
                "reference":      float(reference) if reference is not None else float(current),
                "history_values": history_vals,
                "history_builds": list(builds_list),
            })

        if not metrics:
            log.warning("metrics_json: no valid metrics in model %r", model_name)
            return None

        return {
            "model":         model_name,
            "summary_note":  data.get("summary_note", ""),
            "summary_chips": config.get("summary_chips"),   # None → auto-derived
            "metrics":       metrics,
        }
