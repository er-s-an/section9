"""Recoverable backup/restore primitives for the product registry."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import shutil
import sqlite3
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _integrity(path: Path) -> str:
    connection = sqlite3.connect(path)
    try:
        return str(connection.execute("PRAGMA integrity_check").fetchone()[0])
    finally:
        connection.close()


class ProductBackup:
    """Snapshot only the product DB and already-redacted product artifacts."""

    def __init__(self, registry_path: Path, artifact_root: Path,
                 additional_databases: dict[str, Path] | None = None):
        self.registry_path = Path(registry_path)
        self.artifact_root = Path(artifact_root)
        self.additional_databases = {str(name): Path(path)
                                     for name, path in (additional_databases or {}).items()}

    def create(self, target: Path) -> dict[str, Any]:
        target = Path(target)
        if target.is_symlink():
            raise ValueError("backup target must not be a symlink")
        target = target.resolve()
        registry_path = self.registry_path
        artifact_root = self.artifact_root
        if registry_path.is_symlink() or artifact_root.is_symlink():
            raise ValueError("live product sources must not be symlinks")
        if not registry_path.is_file():
            raise ValueError("product registry database is missing")
        registry_path = registry_path.resolve(strict=True)
        artifact_root_resolved = artifact_root.resolve()
        if (target == registry_path or target == artifact_root_resolved
                or target.is_relative_to(registry_path.parent)
                or target.is_relative_to(artifact_root_resolved)):
            raise ValueError("backup target must be outside live product data")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.mkdir(parents=True, exist_ok=False)
        database = target / "product.sqlite"
        source_db = sqlite3.connect(registry_path)
        destination_db = sqlite3.connect(database)
        try:
            source_db.backup(destination_db)
        finally:
            destination_db.close()
            source_db.close()
        product_integrity = _integrity(database)
        if product_integrity != "ok":
            raise ValueError("product backup database integrity failed")
        product_connection = sqlite3.connect(database)
        try:
            record_count = product_connection.execute("SELECT count(*) FROM product_records").fetchone()[0]
            event_count = product_connection.execute("SELECT count(*) FROM product_events").fetchone()[0]
        finally:
            product_connection.close()
        files: list[dict[str, str]] = [{"path": database.name, "sha256": _sha256(database)}]
        databases = [{"name": "product-registry", "path": database.name,
                      "sha256": _sha256(database), "integrity": product_integrity,
                      "record_count": record_count, "event_count": event_count}]
        for name, source_path in sorted(self.additional_databases.items()):
            if not name or Path(name).name != name:
                raise ValueError("additional database names must be simple path components")
            if source_path.is_symlink():
                raise ValueError(f"additional database must not be a symlink: {name}")
            if not source_path.is_file():
                continue
            relative = Path("databases") / f"{name}.sqlite"
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            source = sqlite3.connect(source_path)
            copy = sqlite3.connect(destination)
            try:
                source.backup(copy)
                integrity = copy.execute("PRAGMA integrity_check").fetchone()[0]
            finally:
                copy.close()
                source.close()
            if integrity != "ok":
                raise ValueError(f"backup database integrity failed: {name}")
            digest = _sha256(destination)
            files.append({"path": str(relative), "sha256": digest})
            databases.append({"name": name, "path": str(relative), "sha256": digest, "integrity": integrity})
        if artifact_root.exists():
            copied_root = target / "product-artifacts"
            entries = sorted(artifact_root.rglob("*"))
            if any(path.is_symlink() for path in entries):
                raise ValueError("product artifacts must not contain symlinks")
            for source in (path for path in entries if path.is_file()):
                resolved = source.resolve(strict=True)
                try:
                    resolved.relative_to(artifact_root_resolved)
                except ValueError as exc:
                    raise ValueError("product artifact resolves outside its root") from exc
                relative = source.relative_to(artifact_root)
                destination = copied_root / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
                files.append({"path": str(Path("product-artifacts") / relative), "sha256": _sha256(destination)})
        manifest = {"schema_version": "product-backup-v1", "created_at": _now(),
                    "databases": databases, "files": files}
        (target / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return {"backup_dir": str(target), "manifest": manifest}

    @staticmethod
    def restore_into(backup_dir: Path, target_dir: Path) -> dict[str, Any]:
        backup_dir = Path(backup_dir)
        target_dir = Path(target_dir)
        if backup_dir.is_symlink() or target_dir.is_symlink():
            raise ValueError("backup and restore directories must not be symlinks")
        backup_dir = backup_dir.resolve(strict=True)
        target_dir = target_dir.resolve()
        if target_dir.exists():
            raise ValueError("restore target must not already exist")
        manifest_path = backup_dir / "manifest.json"
        if manifest_path.is_symlink():
            raise ValueError("backup manifest must not be a symlink")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("schema_version") != "product-backup-v1":
            raise ValueError("unsupported product backup schema")
        staging = target_dir.parent / f".{target_dir.name}.restore-staging"
        if staging.exists():
            raise ValueError("restore staging path already exists")
        staging.mkdir(parents=True, exist_ok=False)
        try:
            seen: set[Path] = set()
            for item in manifest.get("files", []):
                relative = Path(str(item["path"]))
                if relative.is_absolute() or not relative.parts or ".." in relative.parts or "\\" in str(item["path"]):
                    raise ValueError("backup contains an unsafe path")
                source = backup_dir / relative
                if relative in seen:
                    raise ValueError("backup contains a duplicate file path")
                seen.add(relative)
                cursor = backup_dir
                for part in relative.parts:
                    cursor = cursor / part
                    if cursor.is_symlink():
                        raise ValueError("backup file paths must not contain symlinks")
                resolved_source = source.resolve(strict=True)
                try:
                    resolved_source.relative_to(backup_dir)
                except ValueError as exc:
                    raise ValueError("backup file resolves outside its root") from exc
                destination = staging / relative
                if not resolved_source.is_file() or _sha256(resolved_source) != item["sha256"]:
                    raise ValueError(f"backup checksum failed: {relative}")
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(resolved_source, destination)
            staging.rename(target_dir)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        return {"target_dir": str(target_dir), "manifest": manifest}
