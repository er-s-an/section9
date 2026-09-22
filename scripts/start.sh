#!/bin/sh
set -eu
PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$PROJECT_DIR"
uv sync --locked
.venv/bin/python scripts/init-local-config.py
.venv/bin/python scripts/fetch-office-assets.py
# Reconcile installed JavaScript dependencies with their committed locks before
# the build; this prevents a stale node_modules tree from producing a different
# frontend identity.
npm --prefix frontend ci
npm --prefix integrations/gep ci
npm --prefix frontend run build
./scripts/infra-start.sh
exec .venv/bin/python scripts/service.py start
