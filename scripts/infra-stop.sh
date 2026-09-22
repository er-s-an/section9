#!/usr/bin/env bash
set -euo pipefail
root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root_dir"
# Deliberately omit --volumes: local Langfuse data survives a stop.
exec docker compose --project-name section9-observe --env-file infra/.env -f infra/docker-compose.yml down "$@"
