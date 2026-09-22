#!/bin/sh
set -eu
PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$PROJECT_DIR"
run_stamp=$(date -u +%Y%m%dT%H%M%SZ)-$$
run_dir=".runtime/product-acceptance/$run_stamp"
closure_report="artifacts/external-support-agent/closure-runs/acceptance-$run_stamp.json"
backup_dir="$run_dir/backup"
restored_dir="$run_dir/restored"
backup_report="artifacts/external-support-agent/g5/backup-verification-$run_stamp.json"
mkdir -p "$run_dir"

set +e
.venv/bin/python scripts/product-closure.py \
  --label "acceptance-$run_stamp" \
  --report "$closure_report" \
  --lab-reset \
  --stop-first \
  "$@"
closure_status=$?
set -e

set +e
.venv/bin/python scripts/product-lifecycle.py backup --output "$backup_dir" >"$run_dir/backup-command.log" 2>&1
backup_status=$?
if [ "$backup_status" -eq 0 ]; then
  .venv/bin/python scripts/product-lifecycle.py restore --backup "$backup_dir" --target "$restored_dir" \
    >"$run_dir/restore-command.log" 2>&1
  restore_status=$?
else
  restore_status=1
fi
if [ "$restore_status" -eq 0 ]; then
  .venv/bin/python scripts/verify-product-backup.py \
    --live data/product.sqlite \
    --backup "$backup_dir" \
    --restored "$restored_dir" \
    --report "$backup_report"
  backup_verification_status=$?
else
  backup_verification_status=1
fi
set -e

printf 'acceptance_closure_status=%s backup_status=%s restore_status=%s backup_verification_status=%s\n' \
  "$closure_status" "$backup_status" "$restore_status" "$backup_verification_status"
printf 'closure_report=%s backup_report=%s lifecycle_logs=%s\n' \
  "$closure_report" "$backup_report" "$run_dir"
if [ "$closure_status" -ne 0 ] || [ "$backup_status" -ne 0 ] || [ "$restore_status" -ne 0 ] \
  || [ "$backup_verification_status" -ne 0 ]; then
  exit 1
fi
