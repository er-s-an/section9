#!/bin/sh
set -eu
PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$PROJECT_DIR"
uv sync --locked
.venv/bin/python scripts/init-local-config.py
.venv/bin/python scripts/fetch-office-assets.py
if [ ! -d frontend/node_modules ]; then npm --prefix frontend ci; fi
if [ ! -d integrations/gep/node_modules ]; then npm --prefix integrations/gep ci; fi
npm --prefix frontend run build
./scripts/infra-start.sh
exec .venv/bin/python scripts/service.py start
