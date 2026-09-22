#!/usr/bin/env bash
set -euo pipefail
root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root_dir"
docker compose --project-name section9-observe --env-file infra/.env -f infra/docker-compose.yml ps
printf 'langfuse_health_http='
curl -sS -o /dev/null -w '%{http_code}\n' --max-time 5 http://127.0.0.1:9030/api/public/health || true
printf 'collector_health_http='
curl -sS -o /dev/null -w '%{http_code}\n' --max-time 5 http://127.0.0.1:9133/ || true
