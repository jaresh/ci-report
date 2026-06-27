# ── CI Report Generator — Makefile ───────────────────────────────────────────
#
# Requires Docker.  All commands run in the current directory.
#
# Usage examples:
#   make build
#   make check
#   make report BUILD=1247
#   make report BUILD=1247 TOOLS="--junit-xml --metrics-json" FLAGS="--jira --ai"
#   make report BUILD=1247 FLAGS="--junit-xml --ai" OUT="--out reports/1247.html"
# ─────────────────────────────────────────────────────────────────────────────

IMAGE   ?= ci-report
ENVFILE ?= .env
VOL      = -v $(PWD):/workspace
RUN      = docker run --rm --env-file $(ENVFILE) $(VOL) $(IMAGE)

# Default tool selection (override on the command line)
TOOLS   ?= --junit-xml --metrics-json
FLAGS   ?= --jira --ai
OUT     ?=

# ── Targets ───────────────────────────────────────────────────────────────────

.PHONY: build check report help

## Build the Docker image
build:
	docker build -t $(IMAGE) .

## Validate that all required credentials are set in .env
check:
	@cp -n .env.example .env 2>/dev/null && echo "Created .env from .env.example — fill in your values." || true
	$(RUN) --check-credentials

## Generate a report  (required: BUILD=<build-name>)
##   make report BUILD=1247
##   make report BUILD=1247 TOOLS="--junit-xml" FLAGS="--ai"
report:
ifndef BUILD
	$(error BUILD is required: make report BUILD=1247)
endif
	$(RUN) $(TOOLS) $(FLAGS) $(OUT) $(BUILD)

## Show this help
help:
	@echo ""
	@echo "  make build              Build the Docker image"
	@echo "  make check              Validate credentials in .env"
	@echo "  make report BUILD=1247  Generate report for build 1247"
	@echo ""
	@echo "  Override defaults:"
	@echo "    TOOLS=\"--junit-xml --metrics-json\"   which data tools to run"
	@echo "    FLAGS=\"--jira --ai\"                  which enrichment phases"
	@echo "    OUT=\"--out path/report.html\"         output file location"
	@echo "    IMAGE=ci-report                      Docker image name"
	@echo "    ENVFILE=.env                         credentials file"
	@echo ""
