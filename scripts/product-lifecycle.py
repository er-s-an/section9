#!/usr/bin/env python3
"""Backup or restore Section9 product evidence without touching lab state."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from s9 import config  # noqa: E402
from s9.product.lifecycle import ProductBackup  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    backup = sub.add_parser("backup")
    backup.add_argument("--output", type=Path)
    restore = sub.add_parser("restore")
    restore.add_argument("--backup", type=Path, required=True)
    restore.add_argument("--target", type=Path, required=True)
    args = parser.parse_args()
    source = ProductBackup(config.DATA / "product.sqlite", config.DATA / "product-artifacts",
                           {"support-agent": config.DATA / "product-external" / "support-agent.sqlite"})
    if args.command == "backup":
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        target = args.output or (ROOT / "artifacts" / "external-support-agent" / "backups" / stamp)
        result = source.create(target)
    else:
        result = ProductBackup.restore_into(args.backup, args.target)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
