#!/bin/sh
set -eu
PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$PROJECT_DIR"
if [ "${1:-}" = "--app-only" ]; then
  exec .venv/bin/python scripts/service.py stop-app
fi
.venv/bin/python scripts/service.py stop-app
.venv/bin/python scripts/environments.py stop
if [ -x ./scripts/infra-stop.sh ] && [ -f infra/.env ]; then
  ./scripts/infra-stop.sh
fi
