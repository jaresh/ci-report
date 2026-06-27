#!/usr/bin/env python3
"""
generate_sample.py — produce a fully-populated sample CI report.

Creates sample_data.json with randomised-but-realistic CI build data,
then renders it through the Jinja2 template to produce sample_report.html.

Usage:
    python generate_sample.py
    python generate_sample.py --seed 99          # different random data
    python generate_sample.py --out my_report.html
"""

import argparse
import json
import random
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ── Seeded RNG for reproducible but varied output ─────────────────────────────

def make_rng(seed: int) -> random.Random:
    return random.Random(seed)


# ── Time series helpers ───────────────────────────────────────────────────────

def trend_series(rng, start, end, n=8, noise=0.04):
    """Linear interpolation from start→end with proportional Gaussian noise."""
    vals = []
    for i in range(n):
        t      = i / (n - 1)
        base   = start + (end - start) * t
        jitter = base * noise * rng.gauss(0, 1)
        vals.append(round(max(0.1, base + jitter), 1))
    return vals


def stable_series(rng, centre, spread_pct=0.03, n=8):
    """Flat series with small random walk around a centre value."""
    vals = [centre]
    for _ in range(n - 1):
        delta = centre * spread_pct * rng.gauss(0, 1)
        vals.append(round(max(0.1, vals[-1] + delta), 1))
    rng.shuffle(vals)          # randomise order so it looks naturally noisy
    vals = vals[:n]
    # Ensure last value is the "current" one with a slight drift
    vals[-1] = round(centre + centre * spread_pct * rng.gauss(0, 0.5), 1)
    return vals


def history_strip(rng, build_num, n_prior=7, failure_rate=0.12,
                  current_status="fail", current_dur="18.1s"):
    """Generate per-build history for a test case."""
    history = []
    first_build = build_num - n_prior
    for i in range(n_prior):
        b      = first_build + i
        status = "fail" if rng.random() < failure_rate else "pass"
        dur    = f"{rng.uniform(25, 36):.1f}s"
        history.append({"build": str(b), "status": status, "duration": dur})
    history.append({
        "build":    str(build_num),
        "status":   current_status,
        "duration": current_dur,
        "current":  True,
    })
    return history


def random_commit(rng):
    return "".join(rng.choices("0123456789abcdef", k=7))


# ── Pre-written content pools ─────────────────────────────────────────────────
# AI analysis and JIRA tickets are pre-written so the sample looks authentic
# without requiring a live API call.

FAILING_TESTS = [
    {
        "name":            "Bearer token expiry at chunk boundary [PIPE-458]",
        "status":          "fail",
        "duration":        "18.1s",
        "failure_message": "HTTP 403 Forbidden at chunk 7/8",
        "failure_text":    (
            "HTTP 403 at chunk: 7/8\n"
            "bytes transferred: 56 MB of 64 MB\n"
            "token age at failure: 18m 02s\n"
            "hint: JFROG_CLI_TOKEN_REFRESH env var missing from agent-01"
        ),
        "jira":            "PIPE-458",
        "jira_url":        "https://jira.example.com/browse/PIPE-458",
        "task_url":        "https://ci.example.com/jobs/1247/tasks/38",
        "log_url":         "https://ci.example.com/jobs/1247/logs/pipe-458",
        "ai_analysis": {
            "text": (
                "<strong>HTTP 403</strong> at chunk 7/8 (56 MB into final chunk). "
                "Root cause: <code>JFROG_CLI_TOKEN_REFRESH=true</code> missing from "
                "<strong>agent-01</strong> environment — variable was stripped during "
                "the 2024-01-14 re-provision. Token issued at T+0 expired at T+18m "
                "before the final chunk completed. "
                "Fix: add <code>JFROG_CLI_TOKEN_REFRESH=true</code> to "
                "<code>eu-west-1/agent-01.env</code> and re-trigger."
            ),
            "tag":      "REGRESSION · HTTP 403 · agent-01 env",
            "tag_type": "error",
        },
        "jira_context": [
            {
                "key": "PIPE-458", "summary": "Token expiry during chunked upload on agent-01",
                "status": "Open", "type": "Bug", "priority": "High",
                "url": "https://jira.example.com/browse/PIPE-458", "match": "direct",
            },
            {
                "key": "PIPE-459", "summary": "Agent re-provision checklist missing env var copy step",
                "status": "In Progress", "type": "Task", "priority": "Medium",
                "url": "https://jira.example.com/browse/PIPE-459", "match": "related",
            },
            {
                "key": "INFRA-71", "summary": "Automate env var snapshot before agent re-image",
                "status": "Open", "type": "Improvement", "priority": "Low",
                "url": "https://jira.example.com/browse/INFRA-71", "match": "related",
            },
        ],
        "_history_failure_rate": 0.0,   # first failure
    },
    {
        "name":            "Cross-repo dedup timeout under concurrent load [PIPE-476]",
        "status":          "fail",
        "duration":        "30.4s",
        "failure_message": "DeduplicationTimeout: exceeded 30s threshold",
        "failure_text":    (
            "DeduplicationTimeout: request took 30.4s, threshold is 30s\n"
            "  at DeduplicationService.resolveCollision() line 412\n"
            "  context: k8s pod under 3 concurrent upload requests"
        ),
        "jira":            "PIPE-476",
        "jira_url":        "https://jira.example.com/browse/PIPE-476",
        "task_url":        "https://ci.example.com/jobs/1247/tasks/41",
        "log_url":         "https://ci.example.com/jobs/1247/logs/pipe-476",
        "ai_analysis": {
            "text": (
                "Cross-repo dedup hit the <strong>30s timeout</strong> while the k8s pod "
                "was handling 3 concurrent uploads simultaneously. "
                "<code>artifactory.dedup.timeout=30</code> in <code>k8s-configmap.yaml</code> "
                "was exceeded by 0.4s. This is an <strong>intermittent issue</strong> — "
                "also failed in build #1242 under the same load pattern. "
                "Remediation: increase timeout to <code>45s</code> or add "
                "concurrency guards on the k8s pod spec."
            ),
            "tag":      "TIMEOUT · dedup 30s · k8s concurrent load",
            "tag_type": "error",
        },
        "jira_context": [
            {
                "key": "PIPE-476", "summary": "Cross-repo dedup timeout under concurrent k8s load",
                "status": "Open", "type": "Bug", "priority": "High",
                "url": "https://jira.example.com/browse/PIPE-476", "match": "direct",
            },
            {
                "key": "INFRA-88", "summary": "k8s pod limits too low for parallel upload workloads",
                "status": "In Progress", "type": "Improvement", "priority": "Medium",
                "url": "https://jira.example.com/browse/INFRA-88", "match": "related",
            },
        ],
        "_history_failure_rate": 0.15,   # intermittent
    },
    {
        "name":            "Upload survives 60s network partition mid-transfer [PIPE-481]",
        "status":          "timeout",
        "duration":        "3m 12s",
        "failure_message": "Test timed out after 3 minutes (threshold: 3m 00s)",
        "failure_text":    (
            "TimeoutError: CLI reached MAX_RETRY_WAIT=120s without completing\n"
            "  CLI version: 2.52.0\n"
            "  Regression: MAX_RETRY_WAIT changed from 60s to 120s in 2.52.0"
        ),
        "jira":            "PIPE-481",
        "jira_url":        "https://jira.example.com/browse/PIPE-481",
        "task_url":        "https://ci.example.com/jobs/1247/tasks/55",
        "log_url":         "https://ci.example.com/jobs/1247/logs/pipe-481",
        "ai_analysis": {
            "text": (
                "Test timed out at 3 minutes because JFrog CLI <strong>2.52.0</strong> "
                "introduced a regression where <code>MAX_RETRY_WAIT</code> was silently "
                "doubled from 60s to 120s. After the 60s partition was injected at T+42s "
                "the CLI entered a 120s backoff loop — exceeding the 3-minute test budget. "
                "Workaround: set <code>JFROG_CLI_UPLOAD_RETRY_MAX_WAIT=60</code> on "
                "all agents, or pin CLI to <strong>2.51.x</strong> until upstream fix lands."
            ),
            "tag":      "TIMEOUT · CLI 2.52.0 backoff regression",
            "tag_type": "error",
        },
        "jira_context": [
            {
                "key": "PIPE-481", "summary": "Upload timeout regression in JFrog CLI 2.52.0",
                "status": "Open", "type": "Bug", "priority": "Critical",
                "url": "https://jira.example.com/browse/PIPE-481", "match": "direct",
            },
        ],
        "_history_failure_rate": 0.0,   # first failure
    },
    {
        "name":            "Dedup skips binary on SHA-256 edge case [PIPE-477]",
        "status":          "error",
        "duration":        "0.2s",
        "failure_message": "NullPointerException in DeduplicationService.resolveCollision()",
        "failure_text":    (
            "java.lang.NullPointerException\n"
            "  at com.jfrog.artifactory.DeduplicationService.resolveCollision(line 412)\n"
            "  Artifactory version: 7.71.3\n"
            "  Upstream issue: RTFACT-12345"
        ),
        "jira":            "PIPE-477",
        "jira_url":        "https://jira.example.com/browse/PIPE-477",
        "task_url":        "https://ci.example.com/jobs/1247/tasks/42",
        "log_url":         "https://ci.example.com/jobs/1247/logs/pipe-477",
        "ai_analysis": {
            "text": (
                "<strong>NullPointerException</strong> in "
                "<code>DeduplicationService.resolveCollision()</code> at line 412 — "
                "a new code path introduced in Artifactory <strong>7.71.3</strong> "
                "that assumes a non-null <code>checksumEntity</code> but receives null "
                "when the remote node returns a malformed response under high k8s load. "
                "Filed upstream as <code>RTFACT-12345</code>. "
                "Mitigation: reduce concurrent dedup ops below 2 per pod."
            ),
            "tag":      "ERROR · NPE · Artifactory 7.71.3 upstream",
            "tag_type": "error",
        },
        "jira_context": [
            {
                "key": "PIPE-477", "summary": "NPE in dedup service under k8s high load",
                "status": "In Progress", "type": "Bug", "priority": "High",
                "url": "https://jira.example.com/browse/PIPE-477", "match": "direct",
            },
            {
                "key": "INFRA-88", "summary": "k8s pod limits too low for parallel upload workloads",
                "status": "In Progress", "type": "Improvement", "priority": "Medium",
                "url": "https://jira.example.com/browse/INFRA-88", "match": "related",
            },
        ],
        "_history_failure_rate": 0.0,   # first failure
    },
]

SCENARIO_POOLS = [
    {
        "scenario": "E2E Upload Pipeline",
        "config":   "docker-arm64 · Linux · agent-01",
        "os":       "Linux arm64",
        "agent":    "agent-01",
        "jira":     "PIPE-123",
        "jira_url": "https://jira.example.com/browse/PIPE-123",
        "tests":    [0, 2],
    },
    {
        "scenario": "Checksum Deduplication",
        "config":   "k8s-pod · Linux · pool-eu-west-1",
        "os":       "Linux amd64",
        "agent":    "k8s pool-eu-west-1",
        "jira":     "PIPE-310",
        "jira_url": "https://jira.example.com/browse/PIPE-310",
        "tests":    [1, 3],
    },
    {
        "scenario": "Multipart Upload — Network Resilience",
        "config":   "docker-amd64 · Linux · agent-02",
        "os":       "Linux amd64",
        "agent":    "agent-02",
        "jira":     "PIPE-400",
        "jira_url": "https://jira.example.com/browse/PIPE-400",
        "tests":    [2],
    },
]

# Scenarios where the infrastructure or build itself failed — no test cases run at all.
SCENARIO_ERROR_POOL = [
    {
        "scenario": "E2E Upload Pipeline — Windows",
        "config":   "bare-metal · Windows Server 2022 · agent-03",
        "os":       "Windows Server 2022",
        "agent":    "agent-03",
        "test_cases": [{
            "name":              "Scenario setup failed — Infra Error",
            "status":            "error",
            "is_scenario_error": True,
            "error_label":       "INFRA ERROR",
            "duration":          "—",
            "failure_message":   "agent-03 unreachable: connection timeout after 120s (attempt 3/3)",
            "failure_text":      (
                "ConnectionTimeout: SSH handshake failed\n"
                "  host:    agent-03.eu-west-1.internal\n"
                "  port:    22\n"
                "  timeout: 120s\n"
                "  last:    2024-01-14T09:41:03Z\n"
                "Hint: agent-03 was re-provisioned 2024-01-14 — check SSH key rotation"
            ),
            "jira":     "",
            "jira_url": "#",
            "task_url": "https://ci.example.com/jobs/1247/tasks/scenario-windows",
            "log_url":  "https://ci.example.com/jobs/1247/logs/scenario-windows-setup",
            "history": [], "jira_context": [],
            "ai_analysis": {
                "text": (
                    "<strong>agent-03</strong> failed all 3 SSH connection attempts with a "
                    "120s timeout each. The agent was <strong>re-provisioned on 2024-01-14</strong> "
                    "and the new SSH host key was not added to the CI controller's "
                    "<code>known_hosts</code> file. "
                    "Fix: run <code>ssh-keyscan agent-03.eu-west-1.internal &gt;&gt; ~/.ssh/known_hosts</code> "
                    "on the CI controller, or automate host-key rotation in the provisioning playbook."
                ),
                "tag":      "INFRA · SSH host key not rotated after re-provision",
                "tag_type": "error",
            },
        }],
    },
    {
        "scenario": "Artifact Promotion Pipeline",
        "config":   "k8s-pod · Linux · runner-ephemeral",
        "os":       "Linux amd64",
        "agent":    "runner-ephemeral",
        "test_cases": [{
            "name":              "Scenario setup failed — Build Error",
            "status":            "error",
            "is_scenario_error": True,
            "error_label":       "BUILD ERROR",
            "duration":          "—",
            "failure_message":   "gradle build failed: could not resolve com.jfrog:artifactory-gradle-plugin:1.1.0",
            "failure_text":      (
                "FAILURE: Build failed with an exception.\n"
                "* What went wrong:\n"
                "  Could not resolve com.jfrog:artifactory-gradle-plugin:1.1.0\n"
                "  > Could not get resource 'https://plugins.gradle.org/m2/...'\n"
                "    > Connect to plugins.gradle.org:443 failed — connection refused\n"
                "Hint: Gradle plugin proxy not configured on k8s runner-ephemeral pods"
            ),
            "jira":     "",
            "jira_url": "#",
            "task_url": "https://ci.example.com/jobs/1247/tasks/scenario-promotion",
            "log_url":  "https://ci.example.com/jobs/1247/logs/scenario-promotion-build",
            "history": [], "ai_analysis": {}, "jira_context": [],
        }],
    },
]

PERF_MODELS = [
    {
        "model":  "JFrog CLI 2.x · Linux arm64",
        "trend":  "improving",
        "note":   "Best throughput in 8-build window; workers auto-scaled to pool max",
        "chips":  [("throughput", "MB/s"), ("latency p95", "ms")],
        "metrics": [
            {
                "name": "Upload Throughput", "unit": "MB/s",
                "direction": "higher_better",
                "start": 74.0, "end": 87.3, "ref_offset": -5,
                "noise": 0.02,
            },
            {
                "name": "Upload Latency p95", "unit": "ms",
                "direction": "lower_better",
                "start": 268, "end": 234, "ref_offset": 21,
                "noise": 0.03,
            },
            {
                "name": "Checksum Computation", "unit": "ms",
                "direction": "lower_better",
                "start": 51, "end": 45, "ref_offset": 2,
                "noise": 0.05,
            },
            {
                "name": "Retry Count", "unit": "retries",
                "direction": "lower_better",
                "start": 2, "end": 0, "ref_offset": 0,
                "noise": 0.0,
            },
            {
                "name": "CPU Utilisation (avg)", "unit": "%",
                "direction": "lower_better",
                "start": 42, "end": 34, "ref_offset": 4,
                "noise": 0.04,
            },
        ],
    },
    {
        "model":  "Curl Fallback · Linux amd64",
        "trend":  "regressing",
        "note":   "Latency spike and conn-reuse drop — likely TCP keep-alive config change",
        "chips":  [("throughput", "MB/s"), ("latency p95", "ms")],
        "metrics": [
            {
                "name": "Upload Throughput", "unit": "MB/s",
                "direction": "higher_better",
                "start": 76.0, "end": 72.1, "ref_offset": 2.8,
                "noise": 0.02,
            },
            {
                "name": "Upload Latency p95", "unit": "ms",
                "direction": "lower_better",
                "start": 280, "end": 312, "ref_offset": -26,
                "noise": 0.02,
            },
            {
                "name": "Connection Reuse Rate", "unit": "%",
                "direction": "higher_better",
                "start": 72.0, "end": 61.4, "ref_offset": 8.6,
                "noise": 0.03,
            },
            {
                "name": "Retry Count", "unit": "retries",
                "direction": "lower_better",
                "start": 1, "end": 3, "ref_offset": -2,
                "noise": 0.0,
            },
        ],
    },
    {
        "model":  "JFrog CLI 2.x · Windows bare-metal",
        "trend":  "stable",
        "note":   "Stable — Windows 5-thread pool cap limits throughput vs Linux arm64",
        "chips":  [("throughput", "MB/s"), ("latency p95", "ms")],
        "metrics": [
            {
                "name": "Upload Throughput", "unit": "MB/s",
                "direction": "higher_better",
                "start": 70.0, "end": 70.2, "ref_offset": -0.2,
                "noise": 0.02,
            },
            {
                "name": "Upload Latency p95", "unit": "ms",
                "direction": "lower_better",
                "start": 290, "end": 291, "ref_offset": 1,
                "noise": 0.03,
            },
            {
                "name": "CPU Utilisation (avg)", "unit": "%",
                "direction": "lower_better",
                "start": 47, "end": 46, "ref_offset": 0,
                "noise": 0.04,
            },
            {
                "name": "TLS Handshake Time", "unit": "ms",
                "direction": "lower_better",
                "start": 48, "end": 42, "ref_offset": 2,
                "noise": 0.06,
            },
        ],
    },
]


# ── Generator ──────────────────────────────────────────────────────────────────

def generate(seed: int = 2024) -> dict:
    rng = make_rng(seed)
    build_num = rng.randint(1200, 1500)
    ref_num   = build_num - rng.randint(2, 5)
    build_str = str(build_num)
    ref_str   = str(ref_num)
    builds_range = [str(build_num - 7 + i) for i in range(8)]

    branches = [
        "feature/chunked-upload",
        "feature/parallel-upload-v2",
        "fix/token-refresh-regression",
        "feat/artifactory-dedup-v3",
        "fix/curl-fallback-latency",
    ]

    # ── Failures ──────────────────────────────────────────────────────────────
    # Shuffle scenarios and assign 1-2 failing tests each
    chosen_scenarios = rng.sample(SCENARIO_POOLS, k=rng.randint(2, 3))
    failures = []

    used_tests: set = set()
    for s_def in chosen_scenarios:
        available = [i for i in s_def["tests"] if i not in used_tests]
        if not available:
            continue
        n_pick = min(len(available), rng.randint(1, 2))
        chosen = rng.sample(available, k=n_pick)
        used_tests.update(chosen)

        test_cases = []
        for idx in chosen:
            t = dict(FAILING_TESTS[idx])
            rate = t.pop("_history_failure_rate", 0.1)
            t["history"] = history_strip(
                rng, build_num, n_prior=7,
                failure_rate=rate,
                current_status=t["status"],
                current_dur=t["duration"],
            )
            for j, h in enumerate(t["history"]):
                h["build"] = builds_range[j]
            test_cases.append(t)

        failures.append({
            "scenario":   s_def["scenario"],
            "config":     s_def["config"],
            "os":         s_def["os"],
            "agent":      s_def["agent"],
            "jira":       s_def["jira"],
            "jira_url":   s_def["jira_url"],
            "test_cases": test_cases,
        })

    # Always include one scenario-level error to showcase the feature
    scene_err = rng.choice(SCENARIO_ERROR_POOL)
    failures.append({
        "scenario":   scene_err["scenario"],
        "config":     scene_err["config"],
        "os":         scene_err["os"],
        "agent":      scene_err["agent"],
        "jira":       "",
        "jira_url":   "#",
        "test_cases": scene_err["test_cases"],
    })

    # Count total failures for badges
    n_fail = sum(len(s["test_cases"]) for s in failures)

    # ── Performance ───────────────────────────────────────────────────────────
    performance = []
    for m_def in PERF_MODELS:
        metrics_out = []
        chip_values = []

        for i, metric_def in enumerate(m_def["metrics"]):
            values = trend_series(
                rng,
                start=metric_def["start"],
                end=metric_def["end"],
                n=8,
                noise=metric_def.get("noise", 0.03),
            )
            current   = values[-1]
            ref_offset = metric_def.get("ref_offset", 0)
            reference = round(current + ref_offset, 1)

            metrics_out.append({
                "name":           metric_def["name"],
                "unit":           metric_def["unit"],
                "direction":      metric_def["direction"],
                "current":        current,
                "reference":      reference,
                "history_values": values,
                "history_builds": builds_range,
            })

            # Collect chip values for the first 2 metrics
            if i < 2:
                chip_values.append({
                    "label":     m_def["chips"][i][0],
                    "value":     f"{current} {m_def['chips'][i][1]}",
                    "css_class": _chip_class(m_def["trend"], ref_offset, metric_def["direction"]),
                })

        performance.append({
            "model":        m_def["model"],
            "summary_note": m_def["note"],
            "summary_chips": chip_values,
            "metrics":      metrics_out,
        })

    # ── Assemble ──────────────────────────────────────────────────────────────
    duration_mins = rng.randint(8, 25)
    duration_secs = rng.randint(0, 59)

    return {
        "build": {
            "number":    build_str,
            "branch":    rng.choice(branches),
            "commit":    random_commit(rng),
            "ref_build": ref_str,
            "duration":  f"{duration_mins}m {duration_secs:02d}s",
            "badges": {
                "passed":  rng.randint(28, 45),
                "failed":  n_fail,
                "skipped": rng.randint(0, 4),
                "metrics": sum(len(p["metrics"]) for p in performance),
            },
        },
        "failures":    failures,
        "performance": performance,
    }


def _chip_class(trend: str, ref_offset: float, direction: str) -> str:
    """Determine chip colour class from trend and whether ref_offset is good."""
    if trend == "improving":   return "green"
    if trend == "regressing":  return "red"
    return ""


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(description="Generate a sample CI report.")
    p.add_argument("--seed", type=int, default=2024,
                   help="Random seed (change for different data)  [default: 2024]")
    p.add_argument("--out",  type=Path, default=Path("sample_report.html"),
                   help="Output HTML path  [default: sample_report.html]")
    p.add_argument("--data-only", action="store_true",
                   help="Write sample_data.json only, skip rendering")
    args = p.parse_args()

    print(f"Generating sample data (seed={args.seed})…")
    data = generate(args.seed)

    data_path = Path("sample_data.json")
    data_path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    print(f"  data → {data_path}  "
          f"({len(data['failures'])} scenario(s), "
          f"{sum(len(s['test_cases']) for s in data['failures'])} failure(s), "
          f"{len(data['performance'])} perf model(s))")

    if args.data_only:
        return

    # Render via generate_report.py
    HERE = Path(__file__).parent
    sys.path.insert(0, str(HERE))

    try:
        from generate_report import prepare_data, _render_prepared
        template = HERE / "template.html"
        if not template.exists():
            sys.exit(f"template.html not found in {HERE}")

        enriched = prepare_data(data)
        _render_prepared(enriched, template, args.out)
    except Exception as exc:
        print(f"Render failed: {exc}")
        raise


if __name__ == "__main__":
    main()
