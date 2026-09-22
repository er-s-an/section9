#!/bin/sh
# Stage-one local acceptance. Explicitly invokes paid remote model requests.
set -eu
PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$PROJECT_DIR"
.venv/bin/python -m pytest -q
npm --prefix frontend run build
.venv/bin/python scripts/check-secrets.py
./scripts/stop.sh --app-only
./scripts/start.sh
node scripts/browser-product-scope.mjs
node scripts/browser-acceptance.mjs
result=0
.venv/bin/python scripts/run-pair-cases.py --scenarios prompt cost loop composite || result=1
.venv/bin/python scripts/verify-muted-decision.py || result=1
node scripts/browser-product-live.mjs || result=1
.venv/bin/python scripts/doctor.py --json || result=1
exit "$result"
