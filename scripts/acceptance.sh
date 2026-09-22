#!/bin/sh
set -eu
PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$PROJECT_DIR"
export PYTHONPATH="$PROJECT_DIR"
uv run python -m pytest -q
npm --prefix frontend run build
.venv/bin/python scripts/check-secrets.py
./scripts/reset.sh
./scripts/stop.sh
.venv/bin/python scripts/service.py start
# Browser operations, state changes and provider-backed probes; bounded calls.
node scripts/browser-acceptance.mjs
node scripts/browser-extra.mjs
.venv/bin/python scripts/live-safety.py
.venv/bin/python scripts/environments.py start
EVALUATION_OUTPUT="artifacts/acceptance-$(date +%Y%m%d-%H%M%S)"
.venv/bin/python -u scripts/evaluate.py --conditions swarm --scenarios prompt cost loop composite --max-runs 4 --output "$EVALUATION_OUTPUT"
# Telemetry evidence must identify a run from this evaluation summary. The
# checker rejects historical 24-hour matches when the run id is absent/mismatched.
.venv/bin/python scripts/check-telemetry.py --evidence "$EVALUATION_OUTPUT/summary.json"
./scripts/reset.sh
echo 'Live acceptance complete. Section9 remains running at http://127.0.0.1:9019'
