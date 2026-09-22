"""Verify a product backup by opening only the backup and restore copies."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any


def db_info(path: Path) -> dict[str, Any]:
    connection = sqlite3.connect(path)
    try:
        return {
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "integrity": connection.execute("PRAGMA integrity_check").fetchone()[0],
            "records": connection.execute("SELECT count(*) FROM product_records").fetchone()[0],
            "events": connection.execute("SELECT count(*) FROM product_events").fetchone()[0],
        }
    finally:
        connection.close()


def database_integrity(path: Path) -> bool:
    connection = sqlite3.connect(path)
    try:
        return connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", type=Path, required=True)
    parser.add_argument("--restored", type=Path, required=True,
                        help="restore directory produced by scripts/product-lifecycle.py restore")
    parser.add_argument("--backup", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    manifest = json.loads((args.backup / "manifest.json").read_text(encoding="utf-8"))
    restored_database = args.restored / "product.sqlite"
    restored = db_info(restored_database)
    expected = next(item["sha256"] for item in manifest["files"] if item["path"] == "product.sqlite")
    registry_snapshot = next((item for item in manifest.get("databases", [])
                              if item.get("name") == "product-registry"), {})
    artifact_items = [item for item in manifest["files"] if item["path"].startswith("product-artifacts/")]
    artifact_hashes_match = all(
        hashlib.sha256((args.restored / item["path"]).read_bytes()).hexdigest() == item["sha256"]
        for item in artifact_items
    )
    file_hashes_match = all(
        hashlib.sha256((args.restored / item["path"]).read_bytes()).hexdigest() == item["sha256"]
        for item in manifest.get("files", [])
    )
    databases_integrity_ok = all(database_integrity(args.restored / item["path"])
                                 for item in manifest.get("databases", []))
    live = db_info(args.live)
    report = {
        "schema_version": "product-backup-verification-v1",
        "backup": str(args.backup.resolve()),
        "live": live,
        "restored": restored,
        "checks": {
            "manifest_hash_matches_restored": expected == restored["sha256"],
            "restored_integrity_ok": restored["integrity"] == "ok",
            "live_integrity_ok": live["integrity"] == "ok",
            "record_count_matches_snapshot": restored["records"] == registry_snapshot.get("record_count", live["records"]),
            "event_count_matches_snapshot": restored["events"] == registry_snapshot.get("event_count", live["events"]),
            "live_records_include_snapshot": live["records"] >= restored["records"],
            "live_events_include_snapshot": live["events"] >= restored["events"],
            "artifact_hashes_match": artifact_hashes_match,
            "file_hashes_match": file_hashes_match,
            "all_database_integrity_ok": databases_integrity_ok,
        },
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(args.report.resolve()), "checks": report["checks"],
                      "all_passed": all(report["checks"].values())}, ensure_ascii=False))
    return 0 if all(report["checks"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
