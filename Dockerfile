# ── CI Report Generator ───────────────────────────────────────────────────────
#
# Build:
#   docker build -t ci-report .
#
# Run (mount your project directory, pass credentials via env file):
#   docker run --rm --env-file .env -v $(pwd):/workspace ci-report --junit-xml --metrics-json --jira --ai 1247
#
# Check credentials before a run:
#   docker run --rm --env-file .env -v $(pwd):/workspace ci-report --check-credentials
#
# The container working directory is /workspace — all paths in config.json
# are resolved relative to whatever directory you mount there.
# Output reports are written back into the same mounted directory.
# ─────────────────────────────────────────────────────────────────────────────

FROM python:3.11-slim

LABEL org.opencontainers.image.title="ci-report"
LABEL org.opencontainers.image.description="CI dashboard report generator — collect, enrich, analyse, render"

# ── System dependencies ───────────────────────────────────────────────────────
# No OS-level packages needed; slim Python is sufficient.

# ── Python dependencies ───────────────────────────────────────────────────────
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt

# Non-root user — never run CI tooling as root
RUN useradd -m -u 1001 reporter

# ── Application files ─────────────────────────────────────────────────────────
# Scripts and template are baked into the image.
# User config (config.json), test results, and metrics files come from the
# mounted workspace volume — they are never part of the image.
WORKDIR /app

COPY generate_report.py \
     ai_analyser.py     \
     jira_enricher.py   \
     template.html      \
     ./

COPY datasources/ ./datasources/

# ── Runtime ───────────────────────────────────────────────────────────────────
# /workspace is the mount point for the user's project directory.
# generate_report.py looks for config.json in the current working directory,
# so setting WORKDIR here means config.json is expected at /workspace/config.json.
WORKDIR /workspace

USER reporter
ENTRYPOINT ["python", "/app/generate_report.py"]
CMD ["--help"]
