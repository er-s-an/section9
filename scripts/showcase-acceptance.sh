#!/bin/sh
set -eu
PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$PROJECT_DIR"
.venv/bin/python -m pytest -q
npm --prefix frontend run build
.venv/bin/python scripts/check-secrets.py
./scripts/stop.sh
.venv/bin/python scripts/service.py start
.venv/bin/python scripts/verify-pair-reset.py
node scripts/browser-acceptance.mjs
node scripts/browser-showcase.mjs
.venv/bin/python - <<'PY'
import httpx, subprocess
with httpx.Client(trust_env=False) as client:
    response = client.get("http://127.0.0.1:9019/api/pairs/active")
    response.raise_for_status()
    pair_id = response.json()["active_pair"]["pair_id"]
subprocess.run([".venv/bin/python", "scripts/check-pair-telemetry.py", "--pair-id", pair_id], check=True)
PY
printf '%s\n' 'Paired evidence saved under artifacts/showcase-browser and artifacts/showcase-reset.'
