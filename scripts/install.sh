#!/bin/sh
set -eu
PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$PROJECT_DIR"
echo "Initializing local configuration (secrets are never printed)..."
python3 scripts/init-local-config.py
echo "Installing locked Python dependencies..."
uv sync --locked
echo "Installing locked JavaScript dependencies..."
npm --prefix frontend ci
npm --prefix integrations/gep ci
if ! .venv/bin/python scripts/fetch-office-assets.py; then
  echo "warning: optional office assets unavailable; the UI fallback remains usable" >&2
fi
echo "Building the frontend (including available pinned assets)..."
npm --prefix frontend run build
echo "Install complete. Run ./scripts/doctor.py, then ./scripts/start.sh."
