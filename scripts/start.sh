#!/bin/sh
set -eu
PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$PROJECT_DIR"
if [ ! -x .venv/bin/python ] || [ ! -f frontend/dist/index.html ]; then
  echo "Section9 is not installed. Run ./scripts/install.sh first." >&2
  exit 2
fi
.venv/bin/python scripts/init-local-config.py --check-runtime
exec .venv/bin/python scripts/service.py start
