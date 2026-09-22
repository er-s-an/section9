"""Persistent product registry for project-scoped external application work.

The existing laboratory Store remains the authority for the built-in lab.  This
module is the product-facing registry: every record and event has an explicit
project/environment scope and cross-scope reads fail closed.  It intentionally
does not execute commands or contact external services.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from s9.product.bundle import redact_secrets


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _uid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def _encode(value: Any) -> str:
    return json.dumps(redact_secrets(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class ProductError(Exception):
    def __init__(self, code: str, message: str, status: int = 409):
        self.code = code
        self.message = message
        self.status = status
        super().__init__(message)


class ProductRegistry:
    """Small SQLite product registry with scope-checked CRUD and an append log."""

    def __init__(self, path: Path, artifact_root: Path | None = None):
        self.path = Path(path)
        self.artifact_root = Path(artifact_root or self.path.parent / "product-artifacts")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.artifact_root.mkdir(parents=True, exist_ok=True)
        with self.tx() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS product_records(
                    id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    environment_id TEXT NOT NULL,
                    incident_id TEXT,
                    data TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS product_records_scope
                  ON product_records(project_id, environment_id, incident_id, kind);
                CREATE TABLE IF NOT EXISTS product_events(
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT UNIQUE NOT NULL,
                    project_id TEXT NOT NULL,
                    environment_id TEXT NOT NULL,
                    incident_id TEXT,
                    data TEXT NOT NULL
                );
                """
            )

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA busy_timeout=15000")
        db.execute("BEGIN IMMEDIATE")
        try:
            yield db
            if db.in_transaction:
                db.commit()
        except BaseException:
            if db.in_transaction:
                db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _scope(project_id: str, environment_id: str, incident_id: str | None = None) -> dict[str, str]:
        if not project_id or not environment_id:
            raise ProductError("SCOPE_REQUIRED", "project_id and environment_id are required", 422)
        scope = {"project_id": project_id, "environment_id": environment_id}
        if incident_id is not None:
            if not incident_id:
                raise ProductError("SCOPE_REQUIRED", "incident_id must not be empty", 422)
            scope["incident_id"] = incident_id
        return scope

    def put(self, kind: str, item_id: str, data: dict[str, Any], *, project_id: str,
            environment_id: str, incident_id: str | None = None) -> dict[str, Any]:
        scope = self._scope(project_id, environment_id, incident_id)
        if not item_id:
            raise ProductError("ID_REQUIRED", "record id is required", 422)
        timestamp = _now()
        with self.tx() as db:
            existing = db.execute("SELECT project_id, environment_id, incident_id, data FROM product_records WHERE id=?", (item_id,)).fetchone()
            if existing and (existing["project_id"], existing["environment_id"], existing["incident_id"]) != (
                project_id, environment_id, incident_id
            ):
                raise ProductError("SCOPE_MISMATCH", "记录属于另一个项目或环境", 403)
            record = {**data, **scope, "id": item_id, "kind": kind, "updated_at": timestamp}
            if existing:
                previous = json.loads(existing["data"])
                if "created_at" not in record and "created_at" in previous:
                    record["created_at"] = previous["created_at"]
                encoded = _encode(record)
                db.execute("UPDATE product_records SET kind=?, data=?, updated_at=? WHERE id=?", (kind, encoded, timestamp, item_id))
            else:
                record.setdefault("created_at", timestamp)
                encoded = _encode(record)
                db.execute("INSERT INTO product_records VALUES(?,?,?,?,?,?,?,?)",
                           (item_id, kind, project_id, environment_id, incident_id, encoded, timestamp, timestamp))
        return record

    def get(self, kind: str, item_id: str, *, project_id: str, environment_id: str,
            incident_id: str | None = None) -> dict[str, Any] | None:
        self._scope(project_id, environment_id, incident_id)
        with self.tx() as db:
            row = db.execute("SELECT * FROM product_records WHERE id=? AND kind=?", (item_id, kind)).fetchone()
        if not row:
            return None
        if (row["project_id"], row["environment_id"], row["incident_id"]) != (project_id, environment_id, incident_id):
            raise ProductError("SCOPE_MISMATCH", "记录属于另一个项目或环境", 403)
        return json.loads(row["data"])

    def list(self, kind: str, *, project_id: str, environment_id: str,
             incident_id: str | None = None) -> list[dict[str, Any]]:
        self._scope(project_id, environment_id, incident_id)
        with self.tx() as db:
            if incident_id is None:
                rows = db.execute("SELECT data FROM product_records WHERE kind=? AND project_id=? AND environment_id=? ORDER BY created_at",
                                  (kind, project_id, environment_id)).fetchall()
            else:
                rows = db.execute("SELECT data FROM product_records WHERE kind=? AND project_id=? AND environment_id=? AND incident_id=? ORDER BY created_at",
                                  (kind, project_id, environment_id, incident_id)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def event(self, event_type: str, payload: dict[str, Any], *, project_id: str,
              environment_id: str, incident_id: str | None = None, producer: str = "product") -> dict[str, Any]:
        scope = self._scope(project_id, environment_id, incident_id)
        event = {**scope, "event_id": _uid("pev"), "event_type": event_type,
                 "occurred_at": _now(), "producer": producer, "payload": redact_secrets(payload)}
        with self.tx() as db:
            cur = db.execute("INSERT INTO product_events(event_id,project_id,environment_id,incident_id,data) VALUES(?,?,?,?,?)",
                             (event["event_id"], project_id, environment_id, incident_id, _encode(event)))
            event["sequence"] = str(cur.lastrowid)
            db.execute("UPDATE product_events SET data=? WHERE sequence=?", (_encode(event), cur.lastrowid))
        return event

    def events(self, *, project_id: str, environment_id: str, incident_id: str | None = None,
               after: int = 0, limit: int = 200) -> list[dict[str, Any]]:
        self._scope(project_id, environment_id, incident_id)
        with self.tx() as db:
            if incident_id is None:
                rows = db.execute("SELECT data FROM product_events WHERE project_id=? AND environment_id=? AND incident_id IS NULL AND sequence>? ORDER BY sequence LIMIT ?",
                                  (project_id, environment_id, after, limit)).fetchall()
            else:
                rows = db.execute("SELECT data FROM product_events WHERE project_id=? AND environment_id=? AND incident_id=? AND sequence>? ORDER BY sequence LIMIT ?",
                                  (project_id, environment_id, incident_id, after, limit)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def project(self, project_id: str, environment_id: str) -> dict[str, Any] | None:
        return self.get("project", project_id, project_id=project_id, environment_id=environment_id)

    def ensure_project(self, manifest: dict[str, Any]) -> dict[str, Any]:
        project_id = str(manifest["project_id"])
        environment_id = str(manifest["environment_id"])
        return self.put("project", project_id, manifest, project_id=project_id, environment_id=environment_id)
