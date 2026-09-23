"""Persistent registry for legacy scopes and additive Workspace product data.

The existing laboratory Store remains the authority for the built-in lab.  This
module is the product-facing registry: every record and event has an explicit
project/environment scope and cross-scope reads fail closed.  It intentionally
does not execute commands or contact external services.
"""

from __future__ import annotations

import json
import hashlib
import secrets
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from s9.product.bundle import redact_secrets
# The product v1 setup tables intentionally coexist with legacy product_records.
from s9.product.v1_contracts import (
    Application,
    BudgetPolicy,
    Connection,
    ConnectionState,
    DeploymentMode,
    Evidence,
    EvidenceClass,
    Incident,
    IncidentState,
    InvestigationRun,
    InvestigationRunState,
    Hypothesis,
    HypothesisState,
    ScopeBinding,
    Signal,
    Task,
    TaskGraph,
    TaskGraphCreate,
    TaskSpec,
    TaskResult,
    TaskState,
    TaskAttemptFailure,
    Workspace,
)


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
    """SQLite product registry for both legacy project scopes and Workspace v1.

    Workspace v1 records are additive. Existing ``project_id`` and
    ``environment_id`` rows are neither reinterpreted nor migrated here.
    HTTP callers must apply the local operator boundary; production
    multi-user callers still need authenticated scope resolution.
    """

    _WORKSPACE_SCHEMA_VERSION = 8

    def __init__(self, path: Path, artifact_root: Path | None = None):
        self.path = Path(path)
        self.artifact_root = Path(artifact_root or self.path.parent / "product-artifacts")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.artifact_root.mkdir(parents=True, exist_ok=True)
        with self.tx() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS product_records(
                    id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    environment_id TEXT NOT NULL,
                    incident_id TEXT,
                    data TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )""")
            db.execute("CREATE INDEX IF NOT EXISTS product_records_scope "
                       "ON product_records(project_id, environment_id, incident_id, kind)")
            db.execute("""CREATE TABLE IF NOT EXISTS product_events(
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT UNIQUE NOT NULL,
                    project_id TEXT NOT NULL,
                    environment_id TEXT NOT NULL,
                    incident_id TEXT,
                    data TEXT NOT NULL
                )""")
            self._migrate_workspace_schema(db)

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
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

    def _migrate_workspace_schema(self, db: sqlite3.Connection) -> None:
        """Apply additive Workspace schema migrations under the caller transaction."""
        db.execute("""CREATE TABLE IF NOT EXISTS product_schema_migrations(
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL
        )""")
        row = db.execute("SELECT MAX(version) AS version FROM product_schema_migrations").fetchone()
        current = int(row["version"] or 0)
        if current > self._WORKSPACE_SCHEMA_VERSION:
            raise RuntimeError(f"product database schema {current} is newer than this application")
        statements = (
            """CREATE TABLE IF NOT EXISTS workspaces(
                id TEXT PRIMARY KEY,
                revision INTEGER NOT NULL CHECK(revision > 0),
                policy_revision INTEGER NOT NULL CHECK(policy_revision > 0),
                data TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS applications(
                id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL,
                revision INTEGER NOT NULL CHECK(revision > 0),
                data TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(id, workspace_id),
                FOREIGN KEY(workspace_id) REFERENCES workspaces(id) ON DELETE RESTRICT
            )""",
            """CREATE INDEX IF NOT EXISTS applications_by_workspace
                ON applications(workspace_id, created_at, id)""",
            """CREATE TABLE IF NOT EXISTS connections(
                id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL,
                application_id TEXT NOT NULL,
                revision INTEGER NOT NULL CHECK(revision > 0),
                data TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(id, workspace_id, application_id),
                FOREIGN KEY(application_id, workspace_id)
                    REFERENCES applications(id, workspace_id) ON DELETE RESTRICT
            )""",
            """CREATE INDEX IF NOT EXISTS connections_by_application
                ON connections(workspace_id, application_id, created_at, id)""",
            """CREATE TABLE IF NOT EXISTS scope_bindings(
                id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL,
                application_id TEXT NOT NULL,
                environment_id TEXT NOT NULL,
                connection_id TEXT NOT NULL,
                resource_type TEXT NOT NULL,
                external_resource_id TEXT NOT NULL,
                revision INTEGER NOT NULL CHECK(revision > 0),
                data TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(workspace_id, application_id, environment_id, resource_type, external_resource_id),
                FOREIGN KEY(application_id, workspace_id)
                    REFERENCES applications(id, workspace_id) ON DELETE RESTRICT,
                FOREIGN KEY(connection_id, workspace_id, application_id)
                    REFERENCES connections(id, workspace_id, application_id) ON DELETE RESTRICT
            )""",
            """CREATE INDEX IF NOT EXISTS scope_bindings_by_scope
                ON scope_bindings(workspace_id, application_id, environment_id, created_at, id)""",
            """CREATE TABLE IF NOT EXISTS workspace_events(
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT UNIQUE NOT NULL,
                workspace_id TEXT NOT NULL,
                application_id TEXT,
                resource_type TEXT NOT NULL,
                resource_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                recorded_at TEXT NOT NULL,
                data TEXT NOT NULL,
                FOREIGN KEY(workspace_id) REFERENCES workspaces(id) ON DELETE RESTRICT,
                FOREIGN KEY(application_id, workspace_id)
                    REFERENCES applications(id, workspace_id) ON DELETE RESTRICT
            )""",
            """CREATE INDEX IF NOT EXISTS workspace_events_by_scope
                ON workspace_events(workspace_id, sequence)""",
            """CREATE TABLE IF NOT EXISTS product_command_receipts(
                command_name TEXT NOT NULL,
                scope_key TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                request_sha256 TEXT NOT NULL,
                response_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY(command_name, scope_key, idempotency_key)
            )""",
        )
        for statement in statements:
            db.execute(statement)
        db.execute("INSERT OR IGNORE INTO product_schema_migrations(version, applied_at) VALUES(1, ?)", (_now(),))

        workspace_product_tables = (
            """CREATE TABLE IF NOT EXISTS signals(
                id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL,
                application_id TEXT NOT NULL,
                environment_id TEXT NOT NULL,
                source_binding_id TEXT NOT NULL,
                deduplication_key TEXT NOT NULL,
                revision INTEGER NOT NULL CHECK(revision > 0),
                data TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(id, workspace_id, application_id, environment_id),
                UNIQUE(workspace_id, application_id, environment_id, source_binding_id, deduplication_key),
                FOREIGN KEY(application_id, workspace_id)
                    REFERENCES applications(id, workspace_id) ON DELETE RESTRICT,
                CHECK(length(source_binding_id) > 0)
            )""",
            """CREATE INDEX IF NOT EXISTS signals_by_inbox
                ON signals(workspace_id, application_id, environment_id, created_at, id)""",
            """CREATE TRIGGER IF NOT EXISTS signals_binding_scope_insert
                BEFORE INSERT ON signals
                WHEN NOT EXISTS(
                    SELECT 1 FROM scope_bindings b
                    WHERE b.id=NEW.source_binding_id AND b.workspace_id=NEW.workspace_id
                      AND b.application_id=NEW.application_id AND b.environment_id=NEW.environment_id
                )
                BEGIN SELECT RAISE(ABORT, 'signal binding scope mismatch'); END""",
            """CREATE TABLE IF NOT EXISTS incidents(
                id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL,
                application_id TEXT NOT NULL,
                environment_id TEXT NOT NULL,
                revision INTEGER NOT NULL CHECK(revision > 0),
                data TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(id, workspace_id, application_id, environment_id),
                FOREIGN KEY(application_id, workspace_id)
                    REFERENCES applications(id, workspace_id) ON DELETE RESTRICT
            )""",
            """CREATE INDEX IF NOT EXISTS incidents_by_scope
                ON incidents(workspace_id, application_id, environment_id, created_at, id)""",
            """CREATE TABLE IF NOT EXISTS incident_signals(
                workspace_id TEXT NOT NULL,
                application_id TEXT NOT NULL,
                environment_id TEXT NOT NULL,
                incident_id TEXT NOT NULL,
                signal_id TEXT NOT NULL,
                linked_at TEXT NOT NULL,
                PRIMARY KEY(workspace_id, application_id, environment_id, signal_id),
                FOREIGN KEY(incident_id, workspace_id, application_id, environment_id)
                    REFERENCES incidents(id, workspace_id, application_id, environment_id) ON DELETE RESTRICT,
                FOREIGN KEY(signal_id, workspace_id, application_id, environment_id)
                    REFERENCES signals(id, workspace_id, application_id, environment_id) ON DELETE RESTRICT
            )""",
            """CREATE INDEX IF NOT EXISTS incident_signals_by_incident
                ON incident_signals(workspace_id, application_id, environment_id, incident_id)""",
        )
        for statement in workspace_product_tables:
            db.execute(statement)
        db.execute("INSERT OR IGNORE INTO product_schema_migrations(version, applied_at) VALUES(2, ?)", (_now(),))
        db.execute("""CREATE TABLE IF NOT EXISTS signal_import_checkpoints(
            workspace_id TEXT NOT NULL,
            application_id TEXT NOT NULL,
            environment_id TEXT NOT NULL,
            source_binding_id TEXT NOT NULL,
            from_start_time TEXT NOT NULL,
            to_start_time TEXT NOT NULL,
            revision INTEGER NOT NULL CHECK(revision > 0),
            next_cursor TEXT,
            complete INTEGER NOT NULL CHECK(complete IN (0,1)),
            coverage_json TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(workspace_id,application_id,environment_id,source_binding_id,from_start_time,to_start_time),
            FOREIGN KEY(application_id, workspace_id)
                REFERENCES applications(id, workspace_id) ON DELETE RESTRICT
        )""")
        db.execute("""CREATE TRIGGER IF NOT EXISTS signal_checkpoint_binding_scope_insert
            BEFORE INSERT ON signal_import_checkpoints
            WHEN NOT EXISTS(
                SELECT 1 FROM scope_bindings b
                WHERE b.id=NEW.source_binding_id AND b.workspace_id=NEW.workspace_id
                  AND b.application_id=NEW.application_id AND b.environment_id=NEW.environment_id
            )
            BEGIN SELECT RAISE(ABORT, 'signal checkpoint binding scope mismatch'); END""")
        db.execute("INSERT OR IGNORE INTO product_schema_migrations(version, applied_at) VALUES(3, ?)", (_now(),))
        db.execute("""CREATE TABLE IF NOT EXISTS investigation_runs(
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            application_id TEXT NOT NULL,
            environment_id TEXT NOT NULL,
            incident_id TEXT NOT NULL,
            revision INTEGER NOT NULL CHECK(revision > 0),
            state TEXT NOT NULL,
            data TEXT NOT NULL,
            snapshot_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(id,workspace_id,application_id,environment_id,incident_id),
            FOREIGN KEY(incident_id,workspace_id,application_id,environment_id)
                REFERENCES incidents(id,workspace_id,application_id,environment_id) ON DELETE RESTRICT
        )""")
        db.execute("""CREATE INDEX IF NOT EXISTS investigation_runs_by_incident
            ON investigation_runs(workspace_id,application_id,environment_id,incident_id,created_at,id)""")
        db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS investigation_runs_one_active_per_incident
            ON investigation_runs(workspace_id,application_id,environment_id,incident_id)
            WHERE state IN ('queued','running')""")
        db.execute("""CREATE TABLE IF NOT EXISTS investigation_evidence(
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            application_id TEXT NOT NULL,
            environment_id TEXT NOT NULL,
            incident_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            revision INTEGER NOT NULL CHECK(revision > 0),
            data TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(id,workspace_id,application_id,environment_id,incident_id,run_id),
            FOREIGN KEY(run_id,workspace_id,application_id,environment_id,incident_id)
                REFERENCES investigation_runs(id,workspace_id,application_id,environment_id,incident_id) ON DELETE RESTRICT
        )""")
        db.execute("""CREATE INDEX IF NOT EXISTS investigation_evidence_by_run
            ON investigation_evidence(workspace_id,application_id,environment_id,incident_id,run_id,created_at,id)""")
        db.execute("""CREATE TABLE IF NOT EXISTS investigation_hypotheses(
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            application_id TEXT NOT NULL,
            environment_id TEXT NOT NULL,
            incident_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            revision INTEGER NOT NULL CHECK(revision > 0),
            data TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(id,workspace_id,application_id,environment_id,incident_id,run_id),
            FOREIGN KEY(run_id,workspace_id,application_id,environment_id,incident_id)
                REFERENCES investigation_runs(id,workspace_id,application_id,environment_id,incident_id) ON DELETE RESTRICT
        )""")
        db.execute("""CREATE INDEX IF NOT EXISTS investigation_hypotheses_by_run
            ON investigation_hypotheses(workspace_id,application_id,environment_id,incident_id,run_id,created_at,id)""")
        db.execute("INSERT OR IGNORE INTO product_schema_migrations(version, applied_at) VALUES(4, ?)", (_now(),))
        db.execute("""CREATE TABLE IF NOT EXISTS signal_sync_monitors(
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            application_id TEXT NOT NULL,
            environment_id TEXT NOT NULL,
            binding_id TEXT NOT NULL,
            config_revision INTEGER NOT NULL CHECK(config_revision > 0),
            enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
            next_run_at TEXT,
            lease_owner TEXT,
            lease_until TEXT,
            lease_run_id TEXT,
            data TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(workspace_id,application_id,environment_id,binding_id),
            FOREIGN KEY(application_id,workspace_id)
                REFERENCES applications(id,workspace_id) ON DELETE RESTRICT
        )""")
        db.execute("""CREATE INDEX IF NOT EXISTS signal_sync_monitors_due
            ON signal_sync_monitors(enabled,next_run_at,lease_until)""")
        db.execute("""CREATE TABLE IF NOT EXISTS signal_sync_runs(
            id TEXT PRIMARY KEY,
            monitor_id TEXT NOT NULL,
            workspace_id TEXT NOT NULL,
            application_id TEXT NOT NULL,
            environment_id TEXT NOT NULL,
            state TEXT NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            data TEXT NOT NULL,
            FOREIGN KEY(monitor_id) REFERENCES signal_sync_monitors(id) ON DELETE RESTRICT
        )""")
        db.execute("""CREATE INDEX IF NOT EXISTS signal_sync_runs_by_monitor
            ON signal_sync_runs(monitor_id,started_at DESC,id)""")
        db.execute("INSERT OR IGNORE INTO product_schema_migrations(version, applied_at) VALUES(5, ?)", (_now(),))
        db.execute("""CREATE TABLE IF NOT EXISTS investigation_task_graphs(
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            application_id TEXT NOT NULL,
            environment_id TEXT NOT NULL,
            incident_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            revision INTEGER NOT NULL CHECK(revision > 0),
            state TEXT NOT NULL,
            graph_sha256 TEXT NOT NULL,
            data TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(id,workspace_id,application_id,environment_id,incident_id,run_id),
            UNIQUE(workspace_id,application_id,environment_id,run_id),
            FOREIGN KEY(run_id,workspace_id,application_id,environment_id,incident_id)
                REFERENCES investigation_runs(id,workspace_id,application_id,environment_id,incident_id)
                ON DELETE RESTRICT
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS investigation_tasks(
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            application_id TEXT NOT NULL,
            environment_id TEXT NOT NULL,
            incident_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            graph_id TEXT NOT NULL,
            task_key TEXT NOT NULL,
            revision INTEGER NOT NULL CHECK(revision > 0),
            state TEXT NOT NULL,
            epoch INTEGER NOT NULL CHECK(epoch >= 0),
            holder_id TEXT,
            lease_deadline TEXT,
            data TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(id,workspace_id,application_id,environment_id,incident_id,run_id),
            UNIQUE(workspace_id,application_id,environment_id,run_id,task_key),
            FOREIGN KEY(graph_id,workspace_id,application_id,environment_id,incident_id,run_id)
                REFERENCES investigation_task_graphs(id,workspace_id,application_id,environment_id,incident_id,run_id)
                ON DELETE RESTRICT,
            FOREIGN KEY(run_id,workspace_id,application_id,environment_id,incident_id)
                REFERENCES investigation_runs(id,workspace_id,application_id,environment_id,incident_id)
                ON DELETE RESTRICT
        )""")
        db.execute("""CREATE INDEX IF NOT EXISTS investigation_tasks_by_state
            ON investigation_tasks(workspace_id,application_id,environment_id,run_id,state,created_at,id)""")
        db.execute("INSERT OR IGNORE INTO product_schema_migrations(version, applied_at) VALUES(6, ?)", (_now(),))
        db.execute("""CREATE TABLE IF NOT EXISTS investigation_model_usage(
            request_id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            application_id TEXT NOT NULL,
            environment_id TEXT NOT NULL,
            incident_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            task_id TEXT NOT NULL,
            task_epoch INTEGER NOT NULL CHECK(task_epoch > 0),
            provider TEXT NOT NULL,
            model TEXT NOT NULL,
            prompt_sha256 TEXT NOT NULL,
            reserved_tokens INTEGER NOT NULL CHECK(reserved_tokens > 0),
            actual_tokens INTEGER CHECK(actual_tokens IS NULL OR actual_tokens >= 0),
            state TEXT NOT NULL CHECK(state IN ('reserved','dispatch_committed','settled','outcome_unknown')),
            provider_called INTEGER NOT NULL DEFAULT 0 CHECK(provider_called IN (0,1)),
            error_code TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(workspace_id,application_id,environment_id,run_id,task_id,task_epoch),
            FOREIGN KEY(task_id,workspace_id,application_id,environment_id,incident_id,run_id)
                REFERENCES investigation_tasks(id,workspace_id,application_id,environment_id,incident_id,run_id)
                ON DELETE RESTRICT
        )""")
        db.execute("""CREATE INDEX IF NOT EXISTS investigation_model_usage_by_run
            ON investigation_model_usage(workspace_id,application_id,environment_id,run_id,created_at,request_id)""")
        db.execute("INSERT OR IGNORE INTO product_schema_migrations(version, applied_at) VALUES(7, ?)", (_now(),))
        db.execute("""CREATE TABLE IF NOT EXISTS investigation_workers(
            workspace_id TEXT NOT NULL,
            application_id TEXT NOT NULL,
            environment_id TEXT NOT NULL,
            worker_id TEXT NOT NULL,
            token_sha256 TEXT NOT NULL UNIQUE,
            revision INTEGER NOT NULL CHECK(revision > 0),
            state TEXT NOT NULL CHECK(state IN ('active','revoked')),
            data TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(workspace_id,application_id,environment_id,worker_id),
            FOREIGN KEY(application_id,workspace_id)
                REFERENCES applications(id,workspace_id) ON DELETE RESTRICT
        )""")
        db.execute("""CREATE INDEX IF NOT EXISTS investigation_workers_by_scope
            ON investigation_workers(workspace_id,application_id,environment_id,state,created_at)""")
        db.execute("INSERT OR IGNORE INTO product_schema_migrations(version, applied_at) VALUES(8, ?)", (_now(),))

    @staticmethod
    def _workspace_event(
        db: sqlite3.Connection,
        *,
        workspace_id: str,
        application_id: str | None,
        resource_type: str,
        resource_id: str,
        event_type: str,
        record: dict[str, Any],
        event_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        recorded_at = _now()
        event_payload = {
            "revision": record["revision"],
            "record_sha256": hashlib.sha256(_encode(record).encode("utf-8")).hexdigest(),
        }
        if event_metadata:
            event_payload["metadata"] = redact_secrets(event_metadata)
        event = {
            "schema_version": "product-event-v1",
            "event_id": _uid("wev"),
            "event_type": event_type,
            "occurred_at": recorded_at,
            "recorded_at": recorded_at,
            "workspace_id": workspace_id,
            "application_id": application_id,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "payload": event_payload,
        }
        cursor = db.execute(
            """INSERT INTO workspace_events(
                event_id, workspace_id, application_id, resource_type, resource_id, event_type, recorded_at, data
            ) VALUES(?,?,?,?,?,?,?,?)""",
            (event["event_id"], workspace_id, application_id, resource_type, resource_id,
             event_type, recorded_at, _encode(event)),
        )
        event["sequence"] = str(cursor.lastrowid)
        db.execute("UPDATE workspace_events SET data=? WHERE sequence=?", (_encode(event), cursor.lastrowid))
        return event

    @staticmethod
    def _require_workspace(db: sqlite3.Connection, workspace_id: str) -> None:
        if db.execute("SELECT 1 FROM workspaces WHERE id=?", (workspace_id,)).fetchone() is None:
            raise ProductError("WORKSPACE_NOT_FOUND", "工作区不存在", 404)

    @staticmethod
    def _require_application(db: sqlite3.Connection, workspace_id: str, application_id: str) -> None:
        if db.execute("SELECT 1 FROM applications WHERE id=? AND workspace_id=?",
                      (application_id, workspace_id)).fetchone() is None:
            raise ProductError("APPLICATION_NOT_FOUND", "应用不属于该工作区或不存在", 404)

    @staticmethod
    def _command_request_sha256(payload: dict[str, Any]) -> str:
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    @classmethod
    def _replay_command(
        cls,
        db: sqlite3.Connection,
        *,
        command_name: str,
        scope_key: str,
        idempotency_key: str,
        payload: dict[str, Any],
    ) -> dict[str, Any] | None:
        if not idempotency_key.strip() or len(idempotency_key) > 200:
            raise ProductError("IDEMPOTENCY_KEY_REQUIRED", "需要有效的 Idempotency-Key", 422)
        request_hash = cls._command_request_sha256(payload)
        row = db.execute(
            """SELECT request_sha256,response_json FROM product_command_receipts
               WHERE command_name=? AND scope_key=? AND idempotency_key=?""",
            (command_name, scope_key, idempotency_key),
        ).fetchone()
        if row is None:
            return None
        if row["request_sha256"] != request_hash:
            raise ProductError("IDEMPOTENCY_CONFLICT", "同一 Idempotency-Key 不能用于不同请求内容", 409)
        return json.loads(row["response_json"])

    @staticmethod
    def _save_command_receipt(
        db: sqlite3.Connection,
        *,
        command_name: str,
        scope_key: str,
        idempotency_key: str,
        payload: dict[str, Any],
        response: dict[str, Any],
    ) -> None:
        request_hash = ProductRegistry._command_request_sha256(payload)
        db.execute(
            """INSERT INTO product_command_receipts(
                command_name,scope_key,idempotency_key,request_sha256,response_json,created_at
            ) VALUES(?,?,?,?,?,?)""",
            (command_name, scope_key, idempotency_key, request_hash, _encode(response), _now()),
        )

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

    def create_workspace(
        self,
        name: str,
        *,
        idempotency_key: str,
        budget: BudgetPolicy | None = None,
        deployment_mode: DeploymentMode = DeploymentMode.LOCAL,
        workspace_id: str | None = None,
    ) -> dict[str, Any]:
        if not name.strip():
            raise ProductError("INVALID_WORKSPACE_NAME", "工作区名称不能为空", 422)
        item_id = workspace_id or _uid("wsp")
        timestamp = _now()
        effective_budget = budget or BudgetPolicy()
        payload = {
            "name": name.strip(),
            "budget": effective_budget.model_dump(mode="json"),
            "deployment_mode": deployment_mode.value,
            "workspace_id": workspace_id,
        }
        try:
            workspace = Workspace(
                id=item_id,
                workspace_id=item_id,
                revision=1,
                created_at=timestamp,
                updated_at=timestamp,
                name=name.strip(),
                policy_revision=1,
                budget=effective_budget,
                deployment_mode=deployment_mode,
            )
        except ValueError as exc:
            raise ProductError("INVALID_WORKSPACE", str(exc), 422) from exc
        record = workspace.model_dump(mode="json")
        with self.tx() as db:
            replay = self._replay_command(db, command_name="workspace.create", scope_key="local",
                idempotency_key=idempotency_key, payload=payload)
            if replay is not None:
                return replay
            if db.execute("SELECT 1 FROM workspaces WHERE id=?", (item_id,)).fetchone():
                raise ProductError("WORKSPACE_EXISTS", "工作区 ID 已存在", 409)
            db.execute(
                "INSERT INTO workspaces(id,revision,policy_revision,data,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                (item_id, workspace.revision, workspace.policy_revision, _encode(record), timestamp, timestamp),
            )
            self._workspace_event(db, workspace_id=item_id, application_id=None, resource_type="workspace",
                                  resource_id=item_id, event_type="workspace.created", record=record)
            self._save_command_receipt(db, command_name="workspace.create", scope_key="local",
                idempotency_key=idempotency_key, payload=payload, response=record)
        return record

    def list_workspaces(self) -> list[dict[str, Any]]:
        with self.tx() as db:
            rows = db.execute("SELECT data FROM workspaces ORDER BY created_at,id").fetchall()
        return [json.loads(row["data"]) for row in rows]

    def get_workspace(self, workspace_id: str) -> dict[str, Any] | None:
        with self.tx() as db:
            row = db.execute("SELECT data FROM workspaces WHERE id=?", (workspace_id,)).fetchone()
        return json.loads(row["data"]) if row else None

    def update_workspace_budget(
        self, workspace_id: str, budget: BudgetPolicy, *, expected_revision: int,
        idempotency_key: str,
    ) -> dict[str, Any]:
        payload = {"workspace_id": workspace_id, "expected_revision": expected_revision,
                   "budget": budget.model_dump(mode="json")}
        with self.tx() as db:
            replay = self._replay_command(db, command_name="workspace.budget.update",
                scope_key=workspace_id, idempotency_key=idempotency_key, payload=payload)
            if replay is not None:
                return replay
            row = db.execute("SELECT revision,data FROM workspaces WHERE id=?", (workspace_id,)).fetchone()
            if row is None:
                raise ProductError("WORKSPACE_NOT_FOUND", "工作区不存在", 404)
            if int(row["revision"]) != expected_revision:
                raise ProductError("STALE_WORKSPACE_REVISION", "工作区已变化，请刷新后再更新预算", 409)
            current = Workspace.model_validate_json(row["data"])
            timestamp = _now()
            updated = current.model_copy(update={
                "revision": expected_revision + 1,
                "policy_revision": current.policy_revision + 1,
                "budget": budget,
                "updated_at": timestamp,
            })
            record = updated.model_dump(mode="json")
            db.execute(
                "UPDATE workspaces SET revision=?,policy_revision=?,data=?,updated_at=? WHERE id=? AND revision=?",
                (record["revision"], record["policy_revision"], _encode(record), timestamp,
                 workspace_id, expected_revision),
            )
            self._workspace_event(db, workspace_id=workspace_id, application_id=None,
                resource_type="workspace", resource_id=workspace_id,
                event_type="workspace.budget_updated", record=record,
                event_metadata={"policy_revision": record["policy_revision"],
                    "invalidates_active_run_dispatch": True})
            self._save_command_receipt(db, command_name="workspace.budget.update",
                scope_key=workspace_id, idempotency_key=idempotency_key,
                payload=payload, response=record)
        return record

    def create_application(
        self,
        workspace_id: str,
        name: str,
        *,
        idempotency_key: str,
        owner_id: str | None = None,
    ) -> dict[str, Any]:
        if not name.strip():
            raise ProductError("INVALID_APPLICATION_NAME", "应用名称不能为空", 422)
        item_id = _uid("app")
        timestamp = _now()
        try:
            application = Application(
                id=item_id,
                workspace_id=workspace_id,
                revision=1,
                created_at=timestamp,
                updated_at=timestamp,
                name=name.strip(),
                owner_id=owner_id,
            )
        except ValueError as exc:
            raise ProductError("INVALID_APPLICATION", str(exc), 422) from exc
        record = application.model_dump(mode="json")
        payload = {"workspace_id": workspace_id, "name": name.strip(), "owner_id": owner_id}
        with self.tx() as db:
            self._require_workspace(db, workspace_id)
            replay = self._replay_command(db, command_name="application.create", scope_key=workspace_id,
                idempotency_key=idempotency_key, payload=payload)
            if replay is not None:
                return replay
            db.execute(
                "INSERT INTO applications(id,workspace_id,revision,data,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                (item_id, workspace_id, application.revision, _encode(record), timestamp, timestamp),
            )
            self._workspace_event(db, workspace_id=workspace_id, application_id=item_id,
                                  resource_type="application", resource_id=item_id,
                                  event_type="application.created", record=record)
            self._save_command_receipt(db, command_name="application.create", scope_key=workspace_id,
                idempotency_key=idempotency_key, payload=payload, response=record)
        return record

    def get_application(self, workspace_id: str, application_id: str) -> dict[str, Any] | None:
        with self.tx() as db:
            row = db.execute("SELECT data FROM applications WHERE id=? AND workspace_id=?",
                             (application_id, workspace_id)).fetchone()
        return json.loads(row["data"]) if row else None

    def list_applications(self, workspace_id: str) -> list[dict[str, Any]]:
        with self.tx() as db:
            self._require_workspace(db, workspace_id)
            rows = db.execute("SELECT data FROM applications WHERE workspace_id=? ORDER BY created_at,id",
                              (workspace_id,)).fetchall()
        return [json.loads(row["data"]) for row in rows]

    def create_connection(
        self,
        workspace_id: str,
        application_id: str,
        provider: str,
        *,
        idempotency_key: str,
        endpoint: str | None = None,
        credential_ref: str | None = None,
        capabilities: list[str] | None = None,
    ) -> dict[str, Any]:
        item_id = _uid("conn")
        timestamp = _now()
        try:
            connection = Connection(
                id=item_id,
                workspace_id=workspace_id,
                application_id=application_id,
                revision=1,
                created_at=timestamp,
                updated_at=timestamp,
                provider=provider,
                endpoint=endpoint,
                credential_ref=credential_ref,
                capabilities=capabilities or [],
                status=ConnectionState.PENDING,
                checked_at=None,
            )
        except ValueError as exc:
            raise ProductError("INVALID_CONNECTION", str(exc), 422) from exc
        record = connection.model_dump(mode="json")
        payload = {
            "workspace_id": workspace_id,
            "application_id": application_id,
            "provider": provider,
            "endpoint": endpoint,
            "credential_ref": credential_ref,
            "capabilities": capabilities or [],
        }
        with self.tx() as db:
            self._require_application(db, workspace_id, application_id)
            replay = self._replay_command(db, command_name="connection.create", scope_key=workspace_id,
                idempotency_key=idempotency_key, payload=payload)
            if replay is not None:
                return replay
            db.execute(
                """INSERT INTO connections(
                    id,workspace_id,application_id,revision,data,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?)""",
                (item_id, workspace_id, application_id, connection.revision,
                 _encode(record), timestamp, timestamp),
            )
            self._workspace_event(db, workspace_id=workspace_id, application_id=application_id,
                                  resource_type="connection", resource_id=item_id,
                                  event_type="connection.created", record=record)
            self._save_command_receipt(db, command_name="connection.create", scope_key=workspace_id,
                idempotency_key=idempotency_key, payload=payload, response=record)
        return record

    def get_connection(
        self, workspace_id: str, application_id: str, connection_id: str,
    ) -> dict[str, Any] | None:
        with self.tx() as db:
            row = db.execute(
                "SELECT data FROM connections WHERE id=? AND workspace_id=? AND application_id=?",
                (connection_id, workspace_id, application_id),
            ).fetchone()
        return json.loads(row["data"]) if row else None

    def list_connections(self, workspace_id: str, application_id: str) -> list[dict[str, Any]]:
        with self.tx() as db:
            self._require_application(db, workspace_id, application_id)
            rows = db.execute(
                "SELECT data FROM connections WHERE workspace_id=? AND application_id=? ORDER BY created_at,id",
                (workspace_id, application_id),
            ).fetchall()
        return [json.loads(row["data"]) for row in rows]

    def create_scope_binding(
        self,
        workspace_id: str,
        application_id: str,
        environment_id: str,
        connection_id: str,
        resource_type: str,
        external_resource_id: str,
        *,
        idempotency_key: str,
        read_scopes: list[str] | None = None,
        write_scopes: list[str] | None = None,
    ) -> dict[str, Any]:
        item_id = _uid("bind")
        timestamp = _now()
        try:
            binding = ScopeBinding(
                id=item_id,
                workspace_id=workspace_id,
                application_id=application_id,
                environment_id=environment_id,
                connection_id=connection_id,
                resource_type=resource_type,
                external_resource_id=external_resource_id,
                read_scopes=read_scopes or [],
                write_scopes=write_scopes or [],
                status="pending",
                revision=1,
                created_at=timestamp,
                updated_at=timestamp,
            )
        except ValueError as exc:
            raise ProductError("INVALID_SCOPE_BINDING", str(exc), 422) from exc
        record = binding.model_dump(mode="json")
        payload = {
            "workspace_id": workspace_id,
            "application_id": application_id,
            "environment_id": environment_id,
            "connection_id": connection_id,
            "resource_type": resource_type,
            "external_resource_id": external_resource_id,
            "read_scopes": read_scopes or [],
            "write_scopes": write_scopes or [],
        }
        with self.tx() as db:
            self._require_application(db, workspace_id, application_id)
            replay = self._replay_command(db, command_name="scope_binding.create", scope_key=workspace_id,
                idempotency_key=idempotency_key, payload=payload)
            if replay is not None:
                return replay
            connection = db.execute(
                """SELECT 1 FROM connections
                   WHERE id=? AND workspace_id=? AND application_id=?""",
                (connection_id, workspace_id, application_id),
            ).fetchone()
            if connection is None:
                raise ProductError("CONNECTION_SCOPE_MISMATCH", "连接不属于该工作区和应用", 404)
            duplicate = db.execute(
                """SELECT 1 FROM scope_bindings WHERE workspace_id=? AND application_id=?
                   AND environment_id=? AND resource_type=? AND external_resource_id=?""",
                (workspace_id, application_id, environment_id, resource_type, external_resource_id),
            ).fetchone()
            if duplicate:
                raise ProductError("SCOPE_BINDING_EXISTS", "该资源范围已绑定", 409)
            db.execute(
                """INSERT INTO scope_bindings(
                    id,workspace_id,application_id,environment_id,connection_id,resource_type,
                    external_resource_id,revision,data,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (item_id, workspace_id, application_id, environment_id, connection_id,
                 resource_type, external_resource_id, binding.revision, _encode(record), timestamp, timestamp),
            )
            self._workspace_event(db, workspace_id=workspace_id, application_id=application_id,
                                  resource_type="scope_binding", resource_id=item_id,
                                  event_type="scope_binding.created", record=record)
            self._save_command_receipt(db, command_name="scope_binding.create", scope_key=workspace_id,
                idempotency_key=idempotency_key, payload=payload, response=record)
        return record

    def get_scope_binding(
        self, workspace_id: str, application_id: str, environment_id: str, binding_id: str,
    ) -> dict[str, Any] | None:
        with self.tx() as db:
            row = db.execute(
                """SELECT data FROM scope_bindings WHERE id=? AND workspace_id=?
                   AND application_id=? AND environment_id=?""",
                (binding_id, workspace_id, application_id, environment_id),
            ).fetchone()
        return json.loads(row["data"]) if row else None

    def list_scope_bindings(
        self, workspace_id: str, application_id: str, environment_id: str,
    ) -> list[dict[str, Any]]:
        with self.tx() as db:
            self._require_application(db, workspace_id, application_id)
            rows = db.execute(
                """SELECT data FROM scope_bindings WHERE workspace_id=? AND application_id=?
                   AND environment_id=? ORDER BY created_at,id""",
                (workspace_id, application_id, environment_id),
            ).fetchall()
        return [json.loads(row["data"]) for row in rows]

    def record_connection_check(
        self,
        workspace_id: str,
        application_id: str,
        connection_id: str,
        binding_id: str,
        *,
        expected_connection_revision: int,
        expected_binding_revision: int,
        idempotency_key: str,
        request_parameters: dict[str, Any],
        check: dict[str, Any],
    ) -> dict[str, Any]:
        """Persist sanitized read-check status and its audit event atomically."""
        connection_status = check.get("connection_status")
        binding_status = check.get("binding_status")
        if connection_status not in {"connected", "degraded", "permission_denied"}:
            raise ProductError("INVALID_CONNECTION_CHECK", "连接验证状态无效", 422)
        if binding_status not in {"pending", "confirmed", "permission_denied"}:
            raise ProductError("INVALID_CONNECTION_CHECK", "资源范围验证状态无效", 422)
        command_payload = {
            "workspace_id": workspace_id,
            "application_id": application_id,
            "connection_id": connection_id,
            "binding_id": binding_id,
            "expected_connection_revision": expected_connection_revision,
            "expected_binding_revision": expected_binding_revision,
            "request_parameters": request_parameters,
        }
        with self.tx() as db:
            self._require_application(db, workspace_id, application_id)
            replay = self._replay_command(db, command_name="connection.verify", scope_key=workspace_id,
                idempotency_key=idempotency_key, payload=command_payload)
            if replay is not None:
                return replay
            connection_row = db.execute(
                """SELECT revision,data FROM connections WHERE id=? AND workspace_id=? AND application_id=?""",
                (connection_id, workspace_id, application_id),
            ).fetchone()
            if connection_row is None:
                raise ProductError("CONNECTION_NOT_FOUND", "连接不属于该应用或不存在", 404)
            binding_row = db.execute(
                """SELECT revision,data FROM scope_bindings WHERE id=? AND workspace_id=?
                   AND application_id=? AND connection_id=?""",
                (binding_id, workspace_id, application_id, connection_id),
            ).fetchone()
            if binding_row is None:
                raise ProductError("SCOPE_BINDING_NOT_FOUND", "资源范围不属于该连接或不存在", 404)
            if int(connection_row["revision"]) != expected_connection_revision:
                raise ProductError("STALE_CONNECTION_REVISION", "连接已变化，请刷新后重新验证", 409)
            if int(binding_row["revision"]) != expected_binding_revision:
                raise ProductError("STALE_SCOPE_BINDING_REVISION", "资源范围已变化，请刷新后重新验证", 409)

            timestamp = _now()
            connection = json.loads(connection_row["data"])
            binding = json.loads(binding_row["data"])
            connection.update({
                "revision": expected_connection_revision + 1,
                "updated_at": timestamp,
                "status": connection_status,
                "checked_at": timestamp,
                "latest_check": check,
            })
            binding.update({
                "revision": expected_binding_revision + 1,
                "updated_at": timestamp,
                "status": binding_status,
                "checked_at": timestamp,
                "latest_check": check,
            })
            db.execute("UPDATE connections SET revision=?,data=?,updated_at=? WHERE id=?",
                (connection["revision"], _encode(connection), timestamp, connection_id))
            db.execute("UPDATE scope_bindings SET revision=?,data=?,updated_at=? WHERE id=?",
                (binding["revision"], _encode(binding), timestamp, binding_id))
            result = {"connection": connection, "scope_binding": binding, "check": check}
            self._workspace_event(db, workspace_id=workspace_id, application_id=application_id,
                resource_type="scope_binding", resource_id=binding_id,
                event_type="connection.check_completed", record={
                    "revision": binding["revision"], "result_sha256": hashlib.sha256(_encode(check).encode("utf-8")).hexdigest(),
                })
            self._save_command_receipt(db, command_name="connection.verify", scope_key=workspace_id,
                idempotency_key=idempotency_key, payload=command_payload, response=result)
        return result

    def replay_connection_check(
        self,
        workspace_id: str,
        application_id: str,
        connection_id: str,
        binding_id: str,
        *,
        expected_connection_revision: int,
        expected_binding_revision: int,
        idempotency_key: str,
        request_parameters: dict[str, Any],
    ) -> dict[str, Any] | None:
        payload = {
            "workspace_id": workspace_id,
            "application_id": application_id,
            "connection_id": connection_id,
            "binding_id": binding_id,
            "expected_connection_revision": expected_connection_revision,
            "expected_binding_revision": expected_binding_revision,
            "request_parameters": request_parameters,
        }
        with self.tx() as db:
            self._require_application(db, workspace_id, application_id)
            return self._replay_command(db, command_name="connection.verify", scope_key=workspace_id,
                idempotency_key=idempotency_key, payload=payload)

    @staticmethod
    def _find_repeat_source_incident(
        db: sqlite3.Connection,
        workspace_id: str,
        application_id: str,
        environment_id: str,
        source_binding_id: str,
        record: dict[str, Any],
    ) -> tuple[sqlite3.Row | None, list[str]]:
        """Find the unique active Incident for a recently updated, previously linked source object."""
        if record.get("source_kind") != "poll" or not record.get("source_id"):
            return None, []
        rows = db.execute(
            """SELECT i.id,i.revision,i.data AS incident_data,s.data AS signal_data
               FROM incident_signals link
               JOIN signals s ON s.id=link.signal_id AND s.workspace_id=link.workspace_id
                 AND s.application_id=link.application_id AND s.environment_id=link.environment_id
               JOIN incidents i ON i.id=link.incident_id AND i.workspace_id=link.workspace_id
                 AND i.application_id=link.application_id AND i.environment_id=link.environment_id
               WHERE link.workspace_id=? AND link.application_id=? AND link.environment_id=?
                 AND s.source_binding_id=? AND json_extract(s.data,'$.source_id')=?
                 AND json_extract(s.data,'$.signal_type')=? AND json_extract(s.data,'$.source_kind')='poll'
                 AND json_extract(s.data,'$.status')='clustered'
                 AND json_extract(i.data,'$.state') IN ('open','investigating','needs_input')
               ORDER BY link.linked_at DESC,s.id""",
            (workspace_id, application_id, environment_id, source_binding_id,
             record["source_id"], record["signal_type"]),
        ).fetchall()
        observed_at = datetime.fromisoformat(record["observed_at"].replace("Z", "+00:00")).astimezone(timezone.utc)
        cooldown = timedelta(hours=24)
        matches: dict[str, sqlite3.Row] = {}
        for row in rows:
            previous = json.loads(row["signal_data"])
            if previous.get("source_version") == record.get("source_version"):
                continue
            try:
                previous_observed = datetime.fromisoformat(
                    str(previous["observed_at"]).replace("Z", "+00:00"),
                ).astimezone(timezone.utc)
            except (KeyError, TypeError, ValueError):
                continue
            elapsed = observed_at - previous_observed
            if timedelta(0) <= elapsed <= cooldown:
                matches[str(row["id"])] = row
        if len(matches) == 1:
            return next(iter(matches.values())), []
        if len(matches) > 1:
            return None, sorted(matches)
        return None, []

    def ingest_signals(
        self,
        workspace_id: str,
        application_id: str,
        environment_id: str,
        source_binding_id: str,
        items: list[dict[str, Any]],
        *,
        idempotency_key: str,
        coverage: dict[str, Any] | None = None,
        command_name: str = "signal.ingest",
        idempotency_payload: dict[str, Any] | None = None,
        checkpoint: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Insert source-identified signals once and atomically record coverage."""
        if not environment_id or len(items) > 1000:
            raise ProductError("INVALID_SIGNAL_BATCH", "信号批次范围无效或超过 1000 条", 422)
        timestamp = _now()
        normalized: list[dict[str, Any]] = []
        for item in items:
            signal_id = _uid("sig")
            try:
                signal = Signal(
                    id=signal_id,
                    workspace_id=workspace_id,
                    application_id=application_id,
                    environment_id=environment_id,
                    source_binding_id=source_binding_id,
                    revision=1,
                    created_at=timestamp,
                    updated_at=timestamp,
                    status="new",
                    **item,
                )
            except ValueError as exc:
                raise ProductError("INVALID_SIGNAL", "信号记录不符合产品契约", 422) from exc
            normalized.append(signal.model_dump(mode="json"))

        command_payload = idempotency_payload or {
            "workspace_id": workspace_id, "application_id": application_id,
            "environment_id": environment_id, "source_binding_id": source_binding_id,
            "items": items,
            "coverage": coverage,
        }

        created: list[dict[str, Any]] = []
        duplicates: list[dict[str, Any]] = []
        auto_linked_count = 0
        correlation_ambiguous_count = 0
        with self.tx() as db:
            self._require_application(db, workspace_id, application_id)
            replay = self._replay_command(db, command_name=command_name, scope_key=workspace_id,
                idempotency_key=idempotency_key, payload=command_payload)
            if replay is not None:
                return replay
            binding = db.execute(
                """SELECT data FROM scope_bindings WHERE id=? AND workspace_id=?
                   AND application_id=? AND environment_id=?""",
                (source_binding_id, workspace_id, application_id, environment_id),
            ).fetchone()
            if binding is None:
                raise ProductError("SCOPE_BINDING_NOT_FOUND", "信号来源范围不存在或不匹配", 404)
            binding_record = json.loads(binding["data"])
            if binding_record.get("status") not in {"confirmed", "pending"}:
                raise ProductError("SCOPE_BINDING_UNAVAILABLE", "已撤销或无权限的范围不能接收信号", 409)
            for record in normalized:
                existing = db.execute(
                    """SELECT data FROM signals WHERE workspace_id=? AND application_id=?
                       AND environment_id=? AND source_binding_id=? AND deduplication_key=?""",
                    (workspace_id, application_id, environment_id, source_binding_id,
                     record["deduplication_key"]),
                ).fetchone()
                if existing is not None:
                    previous = json.loads(existing["data"])
                    if (previous.get("source_id"), previous.get("source_version")) != (
                        record.get("source_id"), record.get("source_version")
                    ):
                        raise ProductError("SIGNAL_DEDUPLICATION_CONFLICT",
                                           "相同去重键对应了不同来源记录", 409)
                    duplicates.append(previous)
                    continue
                db.execute(
                    """INSERT INTO signals(
                        id,workspace_id,application_id,environment_id,source_binding_id,
                        deduplication_key,revision,data,created_at,updated_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (record["id"], workspace_id, application_id, environment_id, source_binding_id,
                     record["deduplication_key"], record["revision"], _encode(record), timestamp, timestamp),
                )
                created.append(record)
                latest_check = binding_record.get("latest_check")
                scope_verified = (
                    binding_record.get("status") == "confirmed"
                    and isinstance(latest_check, dict)
                    and latest_check.get("scope_confirmed") is True
                )
                incident_row, ambiguous_incident_ids = (
                    self._find_repeat_source_incident(
                        db, workspace_id, application_id, environment_id, source_binding_id, record,
                    ) if scope_verified else (None, [])
                )
                if ambiguous_incident_ids:
                    correlation_ambiguous_count += 1
                    self._workspace_event(
                        db, workspace_id=workspace_id, application_id=application_id,
                        resource_type="signal", resource_id=record["id"],
                        event_type="signal.correlation_ambiguous", record=record,
                        event_metadata={"rule_id": "repeat_source_object_v1",
                            "candidate_incident_ids": ambiguous_incident_ids,
                            "reason": "multiple_active_incidents_match"},
                    )
                    continue
                if incident_row is None:
                    continue
                incident_record = json.loads(incident_row["incident_data"])
                incident_updated = dict(incident_record)
                incident_updated.update({
                    "revision": int(incident_row["revision"]) + 1,
                    "updated_at": timestamp,
                    "signal_ids": [*incident_record["signal_ids"], record["id"]],
                })
                try:
                    incident_record = Incident.model_validate_json(
                        json.dumps(incident_updated),
                    ).model_dump(mode="json")
                except ValueError as exc:
                    raise ProductError("INVALID_INCIDENT", "自动关联后的事故不符合产品契约", 422) from exc
                signal_record = dict(record)
                signal_record.update({"revision": int(record["revision"]) + 1,
                    "updated_at": timestamp, "status": "clustered"})
                db.execute(
                    """INSERT INTO incident_signals(
                       workspace_id,application_id,environment_id,incident_id,signal_id,linked_at
                    ) VALUES(?,?,?,?,?,?)""",
                    (workspace_id, application_id, environment_id, incident_record["id"], record["id"], timestamp),
                )
                db.execute(
                    """UPDATE signals SET revision=?,data=?,updated_at=? WHERE id=? AND revision=?""",
                    (signal_record["revision"], _encode(signal_record), timestamp,
                     record["id"], record["revision"]),
                )
                db.execute(
                    """UPDATE incidents SET revision=?,data=?,updated_at=? WHERE id=? AND revision=?""",
                    (incident_record["revision"], _encode(incident_record), timestamp,
                     incident_record["id"], incident_row["revision"]),
                )
                created[-1] = signal_record
                auto_linked_count += 1
                correlation_metadata = {
                    "rule_id": "repeat_source_object_v1",
                    "incident_id": incident_record["id"],
                    "source_binding_id": source_binding_id,
                    "source_id": record["source_id"],
                    "cooldown_hours": 24,
                    "reason": "same_external_object_recently_linked_by_operator",
                }
                self._workspace_event(
                    db, workspace_id=workspace_id, application_id=application_id,
                    resource_type="signal", resource_id=record["id"],
                    event_type="signal.auto_linked", record=signal_record,
                    event_metadata=correlation_metadata,
                )
                self._workspace_event(
                    db, workspace_id=workspace_id, application_id=application_id,
                    resource_type="incident", resource_id=incident_record["id"],
                    event_type="incident.signal_auto_linked", record=incident_record,
                    event_metadata={key: value for key, value in correlation_metadata.items()
                                    if key != "source_id"},
                )
            checkpoint_record = None
            if checkpoint is not None:
                checkpoint_row = db.execute(
                    """SELECT revision FROM signal_import_checkpoints WHERE workspace_id=? AND application_id=?
                       AND environment_id=? AND source_binding_id=? AND from_start_time=? AND to_start_time=?""",
                    (workspace_id, application_id, environment_id, source_binding_id,
                     checkpoint["from_start_time"], checkpoint["to_start_time"]),
                ).fetchone()
                current_revision = int(checkpoint_row["revision"]) if checkpoint_row else 0
                if current_revision != checkpoint["expected_revision"]:
                    raise ProductError("STALE_SIGNAL_IMPORT", "该时间窗口已由另一个读取任务更新，请刷新后继续", 409)
                next_revision = current_revision + 1
                checkpoint_data = {
                    "workspace_id": workspace_id,
                    "application_id": application_id,
                    "environment_id": environment_id,
                    "source_binding_id": source_binding_id,
                    "from_start_time": checkpoint["from_start_time"],
                    "to_start_time": checkpoint["to_start_time"],
                    "revision": next_revision,
                    "next_cursor": checkpoint.get("next_cursor"),
                    "complete": bool(checkpoint.get("complete")),
                    "coverage": coverage or {},
                    "updated_at": timestamp,
                }
                db.execute(
                    """INSERT INTO signal_import_checkpoints(
                        workspace_id,application_id,environment_id,source_binding_id,
                        from_start_time,to_start_time,revision,next_cursor,complete,coverage_json,updated_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(workspace_id,application_id,environment_id,source_binding_id,from_start_time,to_start_time)
                    DO UPDATE SET revision=excluded.revision,next_cursor=excluded.next_cursor,
                        complete=excluded.complete,coverage_json=excluded.coverage_json,updated_at=excluded.updated_at""",
                    (workspace_id, application_id, environment_id, source_binding_id,
                     checkpoint_data["from_start_time"], checkpoint_data["to_start_time"], next_revision,
                     checkpoint_data["next_cursor"], int(checkpoint_data["complete"]),
                     _encode(checkpoint_data["coverage"]), timestamp),
                )
                checkpoint_record = checkpoint_data
            summary = {
                "revision": int(binding_record["revision"]),
                "created_count": len(created),
                "duplicate_count": len(duplicates),
                "coverage": coverage or {},
            }
            self._workspace_event(
                db, workspace_id=workspace_id, application_id=application_id,
                resource_type="scope_binding", resource_id=source_binding_id,
                event_type="signals.ingested", record=summary,
                event_metadata={
                    "coverage": {key: (coverage or {}).get(key) for key in (
                        "from_start_time", "to_start_time", "complete", "pages_read",
                        "rows_seen", "invalid_rows", "error",
                    )},
                    "checkpoint_revision": checkpoint_record["revision"] if checkpoint_record else None,
                    "auto_linked_count": auto_linked_count,
                    "correlation_ambiguous_count": correlation_ambiguous_count,
                },
            )
            result = {"items": created + duplicates, "created_count": len(created),
                      "duplicate_count": len(duplicates), "auto_linked_count": auto_linked_count,
                      "correlation_ambiguous_count": correlation_ambiguous_count,
                      "coverage": coverage or {}}
            if checkpoint_record is not None:
                result["checkpoint"] = checkpoint_record
            self._save_command_receipt(db, command_name=command_name, scope_key=workspace_id,
                idempotency_key=idempotency_key, payload=command_payload, response=result)
        return result

    def get_signal_import_checkpoint(
        self,
        workspace_id: str,
        application_id: str,
        environment_id: str,
        source_binding_id: str,
        from_start_time: str,
        to_start_time: str,
    ) -> dict[str, Any] | None:
        with self.tx() as db:
            self._require_application(db, workspace_id, application_id)
            row = db.execute(
                """SELECT revision,next_cursor,complete,coverage_json,updated_at
                   FROM signal_import_checkpoints WHERE workspace_id=? AND application_id=?
                   AND environment_id=? AND source_binding_id=? AND from_start_time=? AND to_start_time=?""",
                (workspace_id, application_id, environment_id, source_binding_id,
                 from_start_time, to_start_time),
            ).fetchone()
        if row is None:
            return None
        return {"revision": int(row["revision"]), "next_cursor": row["next_cursor"],
                "complete": bool(row["complete"]), "coverage": json.loads(row["coverage_json"]),
                "updated_at": row["updated_at"]}

    def list_signal_import_checkpoints(
        self,
        workspace_id: str,
        application_id: str,
        environment_id: str,
        *,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        if not environment_id or not 1 <= limit <= 200:
            raise ProductError("INVALID_SIGNAL_IMPORT_PAGE", "信号导入状态分页参数无效", 422)
        with self.tx() as db:
            self._require_application(db, workspace_id, application_id)
            rows = db.execute(
                """SELECT source_binding_id,from_start_time,to_start_time,revision,next_cursor,
                          complete,coverage_json,updated_at
                   FROM signal_import_checkpoints WHERE workspace_id=? AND application_id=? AND environment_id=?
                   ORDER BY updated_at DESC,source_binding_id LIMIT ?""",
                (workspace_id, application_id, environment_id, limit),
            ).fetchall()
        return [{
            "source_binding_id": row["source_binding_id"],
            "from_start_time": row["from_start_time"],
            "to_start_time": row["to_start_time"],
            "revision": int(row["revision"]),
            "next_cursor": row["next_cursor"],
            "complete": bool(row["complete"]),
            "coverage": json.loads(row["coverage_json"]),
            "updated_at": row["updated_at"],
        } for row in rows]

    def replay_signal_ingest(
        self,
        workspace_id: str,
        application_id: str,
        *,
        idempotency_key: str,
        request_payload: dict[str, Any],
        command_name: str = "signal.ingest",
    ) -> dict[str, Any] | None:
        with self.tx() as db:
            self._require_application(db, workspace_id, application_id)
            return self._replay_command(db, command_name=command_name, scope_key=workspace_id,
                idempotency_key=idempotency_key, payload=request_payload)

    def configure_signal_sync_monitor(
        self,
        workspace_id: str,
        application_id: str,
        environment_id: str,
        binding_id: str,
        *,
        enabled: bool,
        interval_seconds: int,
        expected_revision: int,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Create, resume, or pause bounded read-only signal polling for one verified binding."""
        if not 60 <= interval_seconds <= 86_400 or expected_revision < 0:
            raise ProductError("INVALID_SIGNAL_MONITOR", "监护间隔必须为 60 秒到 24 小时", 422)
        timestamp = _now()
        payload = {
            "workspace_id": workspace_id, "application_id": application_id,
            "environment_id": environment_id, "binding_id": binding_id,
            "enabled": enabled, "interval_seconds": interval_seconds,
            "expected_revision": expected_revision,
        }
        with self.tx() as db:
            self._require_application(db, workspace_id, application_id)
            replay = self._replay_command(db, command_name="signal.monitor.configure",
                scope_key=workspace_id, idempotency_key=idempotency_key, payload=payload)
            if replay is not None:
                return replay
            binding_row = db.execute(
                """SELECT b.data AS binding_data,c.data AS connection_data
                   FROM scope_bindings b JOIN connections c
                     ON c.id=b.connection_id AND c.workspace_id=b.workspace_id
                    AND c.application_id=b.application_id
                   WHERE b.id=? AND b.workspace_id=? AND b.application_id=? AND b.environment_id=?""",
                (binding_id, workspace_id, application_id, environment_id),
            ).fetchone()
            if binding_row is None:
                raise ProductError("SCOPE_BINDING_NOT_FOUND", "监护范围不属于该应用和环境", 404)
            binding = json.loads(binding_row["binding_data"])
            connection = json.loads(binding_row["connection_data"])
            provider = str(connection.get("provider", "")).casefold()
            resource_type = binding.get("resource_type")
            supported = (provider == "langfuse" and resource_type == "langfuse_project") or (
                provider == "github" and resource_type == "github_repository")
            if not supported:
                raise ProductError("CONNECTOR_NOT_AVAILABLE", "自动信号读取仅支持已实现的 Langfuse 和 GitHub 范围", 422)
            if enabled and (connection.get("status") != "connected" or binding.get("status") != "confirmed"
                            or (binding.get("latest_check") or {}).get("scope_confirmed") is not True):
                raise ProductError("SOURCE_SCOPE_UNVERIFIED", "自动读取前必须完成该资源的只读范围验证", 409)

            row = db.execute(
                """SELECT config_revision,data FROM signal_sync_monitors WHERE workspace_id=?
                   AND application_id=? AND environment_id=? AND binding_id=?""",
                (workspace_id, application_id, environment_id, binding_id),
            ).fetchone()
            if row is None:
                if expected_revision != 0:
                    raise ProductError("STALE_SIGNAL_MONITOR_REVISION", "监护配置已变化，请刷新后重试", 409)
                monitor_id = _uid("mon")
                enabled_at = timestamp if enabled else None
                record = {
                    "id": monitor_id, "workspace_id": workspace_id, "application_id": application_id,
                    "environment_id": environment_id, "binding_id": binding_id,
                    "connection_id": connection["id"], "provider": provider,
                    "revision": 1, "enabled": enabled, "interval_seconds": interval_seconds,
                    "status": "starting" if enabled else "paused", "enabled_at": enabled_at,
                    "next_run_at": timestamp if enabled else None,
                    "last_successful_watermark": enabled_at, "current_window": None,
                    "consecutive_failures": 0, "last_run": None,
                    "created_at": timestamp, "updated_at": timestamp,
                }
                db.execute(
                    """INSERT INTO signal_sync_monitors(
                       id,workspace_id,application_id,environment_id,binding_id,config_revision,
                       enabled,next_run_at,data,created_at,updated_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (monitor_id, workspace_id, application_id, environment_id, binding_id, 1,
                     int(enabled), record["next_run_at"], _encode(record), timestamp, timestamp),
                )
                event_type = "signal.monitor.enabled" if enabled else "signal.monitor.paused"
            else:
                current_revision = int(row["config_revision"])
                if current_revision != expected_revision:
                    raise ProductError("STALE_SIGNAL_MONITOR_REVISION", "监护配置已变化，请刷新后重试", 409)
                monitor_id = str(db.execute(
                    """SELECT id FROM signal_sync_monitors WHERE workspace_id=? AND application_id=?
                       AND environment_id=? AND binding_id=?""",
                    (workspace_id, application_id, environment_id, binding_id),
                ).fetchone()["id"])
                record = json.loads(row["data"])
                next_revision = current_revision + 1
                record.update({"revision": next_revision, "enabled": enabled,
                               "interval_seconds": interval_seconds, "updated_at": timestamp})
                if enabled:
                    record["enabled_at"] = record.get("enabled_at") or timestamp
                    record["status"] = "starting"
                    record["next_run_at"] = timestamp
                else:
                    record["status"] = "paused"
                    record["next_run_at"] = None
                db.execute(
                    """UPDATE signal_sync_monitors SET config_revision=?,enabled=?,next_run_at=?,
                       lease_owner=NULL,lease_until=NULL,lease_run_id=NULL,data=?,updated_at=? WHERE id=?""",
                    (next_revision, int(enabled), record["next_run_at"], _encode(record), timestamp, monitor_id),
                )
                event_type = "signal.monitor.enabled" if enabled else "signal.monitor.paused"
            self._workspace_event(db, workspace_id=workspace_id, application_id=application_id,
                resource_type="signal_sync_monitor", resource_id=monitor_id,
                event_type=event_type, record=record,
                event_metadata={"provider": provider, "interval_seconds": interval_seconds})
            self._save_command_receipt(db, command_name="signal.monitor.configure", scope_key=workspace_id,
                idempotency_key=idempotency_key, payload=payload, response=record)
        return record

    def list_signal_sync_monitors(
        self, workspace_id: str, application_id: str, environment_id: str,
    ) -> list[dict[str, Any]]:
        with self.tx() as db:
            self._require_application(db, workspace_id, application_id)
            rows = db.execute(
                """SELECT data FROM signal_sync_monitors WHERE workspace_id=? AND application_id=?
                   AND environment_id=? ORDER BY created_at,id""",
                (workspace_id, application_id, environment_id),
            ).fetchall()
        return [json.loads(row["data"]) for row in rows]

    def list_signal_sync_runs(
        self, workspace_id: str, application_id: str, monitor_id: str, *, limit: int = 50,
    ) -> list[dict[str, Any]]:
        if not 1 <= limit <= 200:
            raise ProductError("INVALID_SIGNAL_MONITOR_PAGE", "监护运行分页参数无效", 422)
        with self.tx() as db:
            self._require_application(db, workspace_id, application_id)
            rows = db.execute(
                """SELECT id,state,started_at,finished_at,data FROM signal_sync_runs
                   WHERE monitor_id=? AND workspace_id=? AND application_id=?
                   ORDER BY started_at DESC,id DESC LIMIT ?""",
                (monitor_id, workspace_id, application_id, limit),
            ).fetchall()
        return [{"id": row["id"], "state": row["state"], "started_at": row["started_at"],
                 "finished_at": row["finished_at"], **json.loads(row["data"])} for row in rows]

    def claim_due_signal_sync_monitors(
        self, worker_id: str, *, now: str | None = None, lease_seconds: int = 180,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        timestamp = now or _now()
        parsed_now = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        lease_until = (parsed_now + timedelta(seconds=lease_seconds)).isoformat(timespec="milliseconds")
        claimed: list[dict[str, Any]] = []
        with self.tx() as db:
            rows = db.execute(
                """SELECT id,config_revision,data,lease_run_id FROM signal_sync_monitors
                   WHERE enabled=1 AND next_run_at<=?
                     AND (lease_until IS NULL OR lease_until<=?)
                   ORDER BY next_run_at,id LIMIT ?""",
                (timestamp, timestamp, limit),
            ).fetchall()
            for row in rows:
                record = json.loads(row["data"])
                if row["lease_run_id"]:
                    interrupted = db.execute(
                        "SELECT id,data FROM signal_sync_runs WHERE id=? AND state='running'",
                        (row["lease_run_id"],),
                    ).fetchone()
                    if interrupted:
                        run_data = json.loads(interrupted["data"])
                        run_data.update({"error_code": "SYNC_WORKER_LEASE_EXPIRED",
                                         "detail": "上次同步进程中断；将用相同窗口安全恢复"})
                        db.execute("UPDATE signal_sync_runs SET state='interrupted',finished_at=?,data=? WHERE id=?",
                                   (timestamp, _encode(run_data), interrupted["id"]))
                window = record.get("current_window")
                if not isinstance(window, dict):
                    start_text = record.get("last_successful_watermark") or record.get("enabled_at")
                    start = datetime.fromisoformat(str(start_text).replace("Z", "+00:00"))
                    end = parsed_now
                    if end <= start:
                        next_run = (parsed_now + timedelta(seconds=int(record["interval_seconds"]))).isoformat(
                            timespec="milliseconds")
                        record["next_run_at"] = next_run
                        db.execute("UPDATE signal_sync_monitors SET next_run_at=?,data=?,updated_at=? WHERE id=?",
                                   (next_run, _encode(record), timestamp, row["id"]))
                        continue
                    # Do not replay an unbounded backlog after a long outage. Keep the missed interval
                    # visible, then resume from the newest bounded day; an operator can import a
                    # specific older window manually when that evidence is needed.
                    max_window = timedelta(hours=24)
                    if end - start > max_window:
                        gap_end = end - max_window
                        record["coverage_gap"] = {
                            "from": start.isoformat(timespec="milliseconds"),
                            "to": gap_end.isoformat(timespec="milliseconds"),
                            "reason": "monitor_offline_window_capped",
                        }
                        start = gap_end
                    end = min(end, start + max_window)
                    window = {"from": start.isoformat(timespec="milliseconds"),
                              "to": end.isoformat(timespec="milliseconds"), "cursor": None}
                    record["current_window"] = window
                run_id = _uid("sync")
                run_data = {"window_from": window["from"], "window_to": window["to"],
                            "cursor": window.get("cursor")}
                db.execute(
                    """INSERT INTO signal_sync_runs(
                       id,monitor_id,workspace_id,application_id,environment_id,state,started_at,data)
                       VALUES(?,?,?,?,?,'running',?,?)""",
                    (run_id, row["id"], record["workspace_id"], record["application_id"],
                     record["environment_id"], timestamp, _encode(run_data)),
                )
                record["status"] = "running"
                record["next_run_at"] = lease_until
                db.execute(
                    """UPDATE signal_sync_monitors SET next_run_at=?,lease_owner=?,lease_until=?,
                       lease_run_id=?,data=?,updated_at=? WHERE id=?""",
                    (lease_until, worker_id, lease_until, run_id, _encode(record), timestamp, row["id"]),
                )
                claimed.append({**record, "run_id": run_id})
        return claimed

    def finish_signal_sync_monitor_run(
        self,
        monitor_id: str,
        worker_id: str,
        run_id: str,
        *,
        outcome: dict[str, Any],
        state: str,
        next_run_at: str | None,
        current_window: dict[str, Any] | None,
        last_successful_watermark: str | None,
        consecutive_failures: int,
        disable_monitor: bool = False,
        now: str | None = None,
    ) -> dict[str, Any]:
        if state not in {"healthy", "partial", "degraded", "blocked", "paused"}:
            raise ProductError("INVALID_SIGNAL_MONITOR_STATE", "同步运行状态无效", 422)
        timestamp = now or _now()
        safe_outcome = redact_secrets(outcome)
        with self.tx() as db:
            row = db.execute(
                """SELECT config_revision,data,lease_owner,lease_run_id FROM signal_sync_monitors WHERE id=?""",
                (monitor_id,),
            ).fetchone()
            if row is None:
                raise ProductError("SIGNAL_MONITOR_NOT_FOUND", "信号监护不存在", 404)
            if row["lease_owner"] != worker_id or row["lease_run_id"] != run_id:
                raise ProductError("STALE_SIGNAL_MONITOR_LEASE", "该同步运行的租约已失效", 409)
            record = json.loads(row["data"])
            enabled = bool(record.get("enabled")) and not disable_monitor
            record.update({
                "enabled": enabled, "status": state if disable_monitor else ("paused" if not enabled else state),
                "current_window": current_window,
                "last_successful_watermark": last_successful_watermark or record.get("last_successful_watermark"),
                "consecutive_failures": consecutive_failures,
                "last_run": {"id": run_id, "state": state, "started_at": timestamp,
                             "finished_at": timestamp, **safe_outcome},
                "next_run_at": next_run_at if enabled else None,
                "coverage_gap": None if current_window is None else record.get("coverage_gap"),
                "updated_at": timestamp,
            })
            db.execute(
                """UPDATE signal_sync_monitors SET enabled=?,next_run_at=?,lease_owner=NULL,lease_until=NULL,
                   lease_run_id=NULL,data=?,updated_at=? WHERE id=?""",
                (int(enabled), record["next_run_at"], _encode(record), timestamp, monitor_id),
            )
            db.execute(
                """UPDATE signal_sync_runs SET state=?,finished_at=?,data=? WHERE id=? AND state='running'""",
                (state, timestamp, _encode({"window": record.get("current_window"), **safe_outcome}), run_id),
            )
            self._workspace_event(
                db, workspace_id=record["workspace_id"], application_id=record["application_id"],
                resource_type="signal_sync_monitor", resource_id=monitor_id,
                event_type=f"signal.sync.{state}", record=record,
                event_metadata={"run_id": run_id, "error_code": safe_outcome.get("error_code"),
                                "created_count": safe_outcome.get("created_count"),
                                "coverage_complete": (safe_outcome.get("coverage") or {}).get("complete")},
            )
        return record

    def list_signals(
        self,
        workspace_id: str,
        application_id: str,
        environment_id: str,
        *,
        status: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        if not environment_id or not 1 <= limit <= 1000:
            raise ProductError("INVALID_SIGNAL_PAGE", "信号分页参数无效", 422)
        with self.tx() as db:
            self._require_application(db, workspace_id, application_id)
            if status is None:
                rows = db.execute(
                    """SELECT data FROM signals WHERE workspace_id=? AND application_id=? AND environment_id=?
                       ORDER BY created_at DESC,id DESC LIMIT ?""",
                    (workspace_id, application_id, environment_id, limit),
                ).fetchall()
            else:
                rows = db.execute(
                    """SELECT data FROM signals WHERE workspace_id=? AND application_id=? AND environment_id=?
                       AND json_extract(data,'$.status')=? ORDER BY created_at DESC,id DESC LIMIT ?""",
                    (workspace_id, application_id, environment_id, status, limit),
                ).fetchall()
        return [json.loads(row["data"]) for row in rows]

    def create_incident(
        self,
        workspace_id: str,
        application_id: str,
        environment_id: str,
        title: str,
        severity: str,
        signals: list[dict[str, Any]],
        *,
        idempotency_key: str,
    ) -> dict[str, Any]:
        if not title.strip() or not signals or len(signals) > 100:
            raise ProductError("INVALID_INCIDENT", "事故标题不能为空，且须关联 1–100 个信号", 422)
        signal_ids = [str(item.get("signal_id", "")) for item in signals]
        if any(not item for item in signal_ids) or len(set(signal_ids)) != len(signal_ids):
            raise ProductError("INVALID_INCIDENT_SIGNALS", "事故中的信号 ID 必须唯一且有效", 422)
        item_id = _uid("inc")
        timestamp = _now()
        payload = {
            "workspace_id": workspace_id,
            "application_id": application_id,
            "environment_id": environment_id,
            "title": title.strip(),
            "severity": severity,
            "signals": signals,
        }
        with self.tx() as db:
            self._require_application(db, workspace_id, application_id)
            replay = self._replay_command(db, command_name="incident.create", scope_key=workspace_id,
                idempotency_key=idempotency_key, payload=payload)
            if replay is not None:
                return replay
            current_signals: list[dict[str, Any]] = []
            for reference in signals:
                row = db.execute(
                    """SELECT revision,data FROM signals WHERE id=? AND workspace_id=?
                       AND application_id=? AND environment_id=?""",
                    (reference["signal_id"], workspace_id, application_id, environment_id),
                ).fetchone()
                if row is None:
                    raise ProductError("SIGNAL_NOT_FOUND", "一个或多个信号不属于此应用和环境", 404)
                if int(row["revision"]) != reference["expected_revision"]:
                    raise ProductError("STALE_SIGNAL_REVISION", "信号已变化，请刷新后重新选择", 409)
                record = json.loads(row["data"])
                if record.get("status") != "new":
                    raise ProductError("SIGNAL_ALREADY_TRIAGED", "信号已处理，不能重复归并到新事故", 409)
                current_signals.append(record)

            try:
                incident = Incident(
                    id=item_id,
                    workspace_id=workspace_id,
                    application_id=application_id,
                    environment_id=environment_id,
                    revision=1,
                    created_at=timestamp,
                    updated_at=timestamp,
                    title=title.strip(),
                    severity=severity,
                    signal_ids=signal_ids,
                    state=IncidentState.OPEN,
                    outcome=None,
                )
            except ValueError as exc:
                raise ProductError("INVALID_INCIDENT", "事故不符合产品契约", 422) from exc
            incident_record = incident.model_dump(mode="json")
            db.execute(
                """INSERT INTO incidents(
                    id,workspace_id,application_id,environment_id,revision,data,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?)""",
                (item_id, workspace_id, application_id, environment_id, 1,
                 _encode(incident_record), timestamp, timestamp),
            )
            for signal in current_signals:
                db.execute(
                    """INSERT INTO incident_signals(
                        workspace_id,application_id,environment_id,incident_id,signal_id,linked_at
                    ) VALUES(?,?,?,?,?,?)""",
                    (workspace_id, application_id, environment_id, item_id, signal["id"], timestamp),
                )
                updated_signal = dict(signal)
                updated_signal.update({"revision": signal["revision"] + 1, "updated_at": timestamp,
                                       "status": "clustered"})
                db.execute("UPDATE signals SET revision=?,data=?,updated_at=? WHERE id=?",
                    (updated_signal["revision"], _encode(updated_signal), timestamp, signal["id"]))
                self._workspace_event(db, workspace_id=workspace_id, application_id=application_id,
                    resource_type="signal", resource_id=signal["id"], event_type="signal.clustered",
                    record=updated_signal)
            self._workspace_event(db, workspace_id=workspace_id, application_id=application_id,
                resource_type="incident", resource_id=item_id, event_type="incident.created",
                record=incident_record)
            self._save_command_receipt(db, command_name="incident.create", scope_key=workspace_id,
                idempotency_key=idempotency_key, payload=payload, response=incident_record)
        return incident_record

    def advance_demo_resolution(self, workspace_id, application_id, environment_id, incident_id, plan_id):
        from s9.product.demo_incident import advance
        return advance(self, workspace_id, application_id, environment_id, incident_id, plan_id)

    def transition_incident(
        self,
        workspace_id: str,
        application_id: str,
        environment_id: str,
        incident_id: str,
        target_state: IncidentState,
        reason: str,
        *,
        expected_revision: int,
        idempotency_key: str,
    ) -> dict[str, Any]:
        reason = reason.strip()
        if not reason or len(reason) > 1000:
            raise ProductError("INVALID_INCIDENT_TRANSITION", "状态变更原因须为 1–1000 个字符", 422)
        payload = {
            "workspace_id": workspace_id,
            "application_id": application_id,
            "environment_id": environment_id,
            "incident_id": incident_id,
            "expected_revision": expected_revision,
            "target_state": target_state.value,
            "reason": reason,
        }
        with self.tx() as db:
            self._require_application(db, workspace_id, application_id)
            replay = self._replay_command(db, command_name="incident.transition", scope_key=workspace_id,
                idempotency_key=idempotency_key, payload=payload)
            if replay is not None:
                return replay
            row = db.execute(
                """SELECT revision,data FROM incidents WHERE id=? AND workspace_id=?
                   AND application_id=? AND environment_id=?""",
                (incident_id, workspace_id, application_id, environment_id),
            ).fetchone()
            if row is None:
                raise ProductError("INCIDENT_NOT_FOUND", "事故不属于该应用和环境或不存在", 404)
            if int(row["revision"]) != expected_revision:
                raise ProductError("STALE_INCIDENT_REVISION", "事故已变化，请刷新后重新提交状态变更", 409)
            current = json.loads(row["data"])
            current_state = IncidentState(current["state"])
            if target_state == IncidentState.RESOLVED:
                raise ProductError(
                    "INDEPENDENT_VALIDATION_REQUIRED",
                    "事故只能在独立恢复验证通过后解决；当前产品尚无该验证流程",
                    409,
                )
            allowed = {
                IncidentState.OPEN: {IncidentState.INVESTIGATING, IncidentState.NEEDS_INPUT, IncidentState.DISMISSED},
                IncidentState.INVESTIGATING: {IncidentState.OPEN, IncidentState.NEEDS_INPUT, IncidentState.DISMISSED},
                IncidentState.NEEDS_INPUT: {IncidentState.OPEN, IncidentState.INVESTIGATING, IncidentState.DISMISSED},
            }
            if target_state not in allowed.get(current_state, set()):
                raise ProductError("INCIDENT_TRANSITION_NOT_AVAILABLE", "此事故状态变更尚无可验证的工作流支持", 409)
            timestamp = _now()
            updated = dict(current)
            updated.update({
                "revision": expected_revision + 1,
                "updated_at": timestamp,
                "state": target_state.value,
                "outcome": "dismissed" if target_state == IncidentState.DISMISSED else None,
            })
            try:
                validated = Incident.model_validate_json(json.dumps(updated)).model_dump(mode="json")
            except ValueError as exc:
                raise ProductError("INVALID_INCIDENT", "事故状态不符合产品契约", 422) from exc
            db.execute(
                """UPDATE incidents SET revision=?,data=?,updated_at=?
                   WHERE id=? AND workspace_id=? AND application_id=? AND environment_id=? AND revision=?""",
                (validated["revision"], _encode(validated), timestamp, incident_id, workspace_id,
                 application_id, environment_id, expected_revision),
            )
            self._workspace_event(
                db, workspace_id=workspace_id, application_id=application_id,
                resource_type="incident", resource_id=incident_id, event_type="incident.state_changed",
                record=validated,
                event_metadata={"from_state": current_state.value, "to_state": target_state.value, "reason": reason},
            )
            self._save_command_receipt(db, command_name="incident.transition", scope_key=workspace_id,
                idempotency_key=idempotency_key, payload=payload, response=validated)
        return validated

    def attach_signals_to_incident(
        self,
        workspace_id: str,
        application_id: str,
        environment_id: str,
        incident_id: str,
        signals: list[dict[str, Any]],
        reason: str,
        *,
        expected_incident_revision: int,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Attach operator-correlated new signals to one active Incident atomically."""
        reason = reason.strip()
        if not reason or len(reason) > 1000:
            raise ProductError("INVALID_SIGNAL_ATTACHMENT", "关联原因须为 1–1000 个字符", 422)
        if not signals or len(signals) > 100:
            raise ProductError("INVALID_INCIDENT_SIGNALS", "须关联 1–100 个新信号", 422)
        signal_ids = [str(item.get("signal_id", "")) for item in signals]
        if any(not item for item in signal_ids) or len(set(signal_ids)) != len(signal_ids):
            raise ProductError("INVALID_INCIDENT_SIGNALS", "事故中的信号 ID 必须唯一且有效", 422)
        payload = {
            "workspace_id": workspace_id,
            "application_id": application_id,
            "environment_id": environment_id,
            "incident_id": incident_id,
            "expected_incident_revision": expected_incident_revision,
            "signals": signals,
            "reason": reason,
        }
        timestamp = _now()
        with self.tx() as db:
            self._require_application(db, workspace_id, application_id)
            replay = self._replay_command(
                db, command_name="incident.attach_signals", scope_key=workspace_id,
                idempotency_key=idempotency_key, payload=payload,
            )
            if replay is not None:
                return replay
            incident_row = db.execute(
                """SELECT revision,data FROM incidents WHERE id=? AND workspace_id=?
                   AND application_id=? AND environment_id=?""",
                (incident_id, workspace_id, application_id, environment_id),
            ).fetchone()
            if incident_row is None:
                raise ProductError("INCIDENT_NOT_FOUND", "事故不属于该应用和环境或不存在", 404)
            if int(incident_row["revision"]) != expected_incident_revision:
                raise ProductError("STALE_INCIDENT_REVISION", "事故已变化，请刷新后重新关联信号", 409)
            current_incident = json.loads(incident_row["data"])
            if current_incident["state"] not in {
                IncidentState.OPEN.value, IncidentState.INVESTIGATING.value, IncidentState.NEEDS_INPUT.value,
            }:
                raise ProductError("INCIDENT_NOT_ATTACHABLE", "仅待处理、调查中或等待资料的事故可接收信号", 409)

            current_signals: list[dict[str, Any]] = []
            for reference in signals:
                row = db.execute(
                    """SELECT revision,data FROM signals WHERE id=? AND workspace_id=?
                       AND application_id=? AND environment_id=?""",
                    (reference["signal_id"], workspace_id, application_id, environment_id),
                ).fetchone()
                if row is None:
                    raise ProductError("SIGNAL_NOT_FOUND", "一个或多个信号不属于此应用和环境", 404)
                if int(row["revision"]) != reference["expected_revision"]:
                    raise ProductError("STALE_SIGNAL_REVISION", "信号已变化，请刷新后重新选择", 409)
                signal = json.loads(row["data"])
                if signal.get("status") != "new":
                    raise ProductError("SIGNAL_ALREADY_TRIAGED", "信号已处理，不能重复关联", 409)
                current_signals.append(signal)

            updated_incident = dict(current_incident)
            updated_incident.update({
                "revision": expected_incident_revision + 1,
                "updated_at": timestamp,
                "signal_ids": [*current_incident["signal_ids"], *signal_ids],
            })
            try:
                validated_incident = Incident.model_validate_json(
                    json.dumps(updated_incident),
                ).model_dump(mode="json")
            except ValueError as exc:
                raise ProductError("INVALID_INCIDENT", "事故更新不符合产品契约", 422) from exc

            for signal in current_signals:
                db.execute(
                    """INSERT INTO incident_signals(
                        workspace_id,application_id,environment_id,incident_id,signal_id,linked_at
                    ) VALUES(?,?,?,?,?,?)""",
                    (workspace_id, application_id, environment_id, incident_id, signal["id"], timestamp),
                )
                updated_signal = dict(signal)
                updated_signal.update({"revision": signal["revision"] + 1, "updated_at": timestamp,
                                       "status": "clustered"})
                db.execute(
                    "UPDATE signals SET revision=?,data=?,updated_at=? WHERE id=? AND revision=?",
                    (updated_signal["revision"], _encode(updated_signal), timestamp,
                     signal["id"], signal["revision"]),
                )
                self._workspace_event(
                    db, workspace_id=workspace_id, application_id=application_id,
                    resource_type="signal", resource_id=signal["id"],
                    event_type="signal.clustered", record=updated_signal,
                    event_metadata={"incident_id": incident_id, "reason": reason},
                )
            db.execute(
                """UPDATE incidents SET revision=?,data=?,updated_at=?
                   WHERE id=? AND workspace_id=? AND application_id=? AND environment_id=? AND revision=?""",
                (validated_incident["revision"], _encode(validated_incident), timestamp,
                 incident_id, workspace_id, application_id, environment_id, expected_incident_revision),
            )
            self._workspace_event(
                db, workspace_id=workspace_id, application_id=application_id,
                resource_type="incident", resource_id=incident_id,
                event_type="incident.signals_attached", record=validated_incident,
                event_metadata={"signal_ids": signal_ids, "reason": reason},
            )
            self._save_command_receipt(
                db, command_name="incident.attach_signals", scope_key=workspace_id,
                idempotency_key=idempotency_key, payload=payload, response=validated_incident,
            )
        return validated_incident

    def set_incident_claim(
        self,
        workspace_id: str,
        application_id: str,
        environment_id: str,
        incident_id: str,
        claimed: bool,
        reason: str,
        *,
        expected_revision: int,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Claim or release an Incident for the single local operator principal."""
        reason = reason.strip()
        if not reason or len(reason) > 1000:
            raise ProductError("INVALID_INCIDENT_CLAIM", "认领说明须为 1–1000 个字符", 422)
        actor_id = "local-operator"
        payload = {
            "workspace_id": workspace_id, "application_id": application_id,
            "environment_id": environment_id, "incident_id": incident_id,
            "claimed": claimed, "expected_revision": expected_revision, "reason": reason,
            "actor_id": actor_id,
        }
        timestamp = _now()
        active_states = {IncidentState.OPEN, IncidentState.INVESTIGATING, IncidentState.NEEDS_INPUT}
        command_name = "incident.claim" if claimed else "incident.release"
        with self.tx() as db:
            self._require_application(db, workspace_id, application_id)
            replay = self._replay_command(db, command_name=command_name, scope_key=workspace_id,
                idempotency_key=idempotency_key, payload=payload)
            if replay is not None:
                return replay
            row = db.execute(
                """SELECT revision,data FROM incidents WHERE id=? AND workspace_id=?
                   AND application_id=? AND environment_id=?""",
                (incident_id, workspace_id, application_id, environment_id),
            ).fetchone()
            if row is None:
                raise ProductError("INCIDENT_NOT_FOUND", "事故不属于该应用和环境或不存在", 404)
            if int(row["revision"]) != expected_revision:
                raise ProductError("STALE_INCIDENT_REVISION", "事故已变化，请刷新后重试认领", 409)
            current = json.loads(row["data"])
            if IncidentState(current["state"]) not in active_states:
                raise ProductError("INCIDENT_NOT_CLAIMABLE", "仅未结案事故可以认领", 409)
            if claimed and current.get("assignee_id") is not None:
                raise ProductError("INCIDENT_ALREADY_CLAIMED", "该事故已有处理人；请先由其释放认领", 409)
            if not claimed and current.get("assignee_id") != actor_id:
                raise ProductError("INCIDENT_NOT_CLAIMED_BY_OPERATOR", "当前本地操作员没有认领该事故", 409)
            updated = dict(current)
            updated.update({
                "revision": expected_revision + 1,
                "updated_at": timestamp,
                "assignee_id": actor_id if claimed else None,
            })
            try:
                record = Incident.model_validate_json(json.dumps(updated)).model_dump(mode="json")
            except ValueError as exc:
                raise ProductError("INVALID_INCIDENT", "事故认领状态不符合产品契约", 422) from exc
            db.execute(
                """UPDATE incidents SET revision=?,data=?,updated_at=? WHERE id=? AND revision=?""",
                (record["revision"], _encode(record), timestamp, incident_id, expected_revision),
            )
            self._workspace_event(
                db, workspace_id=workspace_id, application_id=application_id,
                resource_type="incident", resource_id=incident_id,
                event_type="incident.claimed" if claimed else "incident.released", record=record,
                event_metadata={"actor_id": actor_id, "reason": reason},
            )
            self._save_command_receipt(db, command_name=command_name, scope_key=workspace_id,
                idempotency_key=idempotency_key, payload=payload, response=record)
        return record

    def update_incident_severity(
        self,
        workspace_id: str,
        application_id: str,
        environment_id: str,
        incident_id: str,
        severity: str,
        reason: str,
        *,
        expected_revision: int,
        idempotency_key: str,
    ) -> dict[str, Any]:
        reason = reason.strip()
        if severity not in {"critical", "high", "medium", "low", "info"} or not reason or len(reason) > 1000:
            raise ProductError("INVALID_INCIDENT_SEVERITY", "严重度或变更原因无效", 422)
        payload = {
            "workspace_id": workspace_id, "application_id": application_id,
            "environment_id": environment_id, "incident_id": incident_id,
            "severity": severity, "expected_revision": expected_revision, "reason": reason,
        }
        timestamp = _now()
        active_states = {IncidentState.OPEN, IncidentState.INVESTIGATING, IncidentState.NEEDS_INPUT}
        with self.tx() as db:
            self._require_application(db, workspace_id, application_id)
            replay = self._replay_command(db, command_name="incident.severity", scope_key=workspace_id,
                idempotency_key=idempotency_key, payload=payload)
            if replay is not None:
                return replay
            row = db.execute(
                """SELECT revision,data FROM incidents WHERE id=? AND workspace_id=?
                   AND application_id=? AND environment_id=?""",
                (incident_id, workspace_id, application_id, environment_id),
            ).fetchone()
            if row is None:
                raise ProductError("INCIDENT_NOT_FOUND", "事故不属于该应用和环境或不存在", 404)
            if int(row["revision"]) != expected_revision:
                raise ProductError("STALE_INCIDENT_REVISION", "事故已变化，请刷新后调整严重度", 409)
            current = json.loads(row["data"])
            if IncidentState(current["state"]) not in active_states:
                raise ProductError("INCIDENT_NOT_EDITABLE", "仅未结案事故可以调整严重度", 409)
            if current["severity"] == severity:
                raise ProductError("INCIDENT_SEVERITY_UNCHANGED", "严重度没有变化", 409)
            updated = dict(current)
            updated.update({"revision": expected_revision + 1, "updated_at": timestamp, "severity": severity})
            try:
                record = Incident.model_validate_json(json.dumps(updated)).model_dump(mode="json")
            except ValueError as exc:
                raise ProductError("INVALID_INCIDENT", "事故严重度更新不符合产品契约", 422) from exc
            db.execute(
                """UPDATE incidents SET revision=?,data=?,updated_at=? WHERE id=? AND revision=?""",
                (record["revision"], _encode(record), timestamp, incident_id, expected_revision),
            )
            self._workspace_event(
                db, workspace_id=workspace_id, application_id=application_id,
                resource_type="incident", resource_id=incident_id, event_type="incident.severity_changed",
                record=record, event_metadata={"from_severity": current["severity"],
                    "to_severity": severity, "reason": reason},
            )
            self._save_command_receipt(db, command_name="incident.severity", scope_key=workspace_id,
                idempotency_key=idempotency_key, payload=payload, response=record)
        return record

    def merge_incidents(
        self,
        workspace_id: str,
        application_id: str,
        environment_id: str,
        incident_id: str,
        target_incident_id: str,
        reason: str,
        *,
        expected_incident_revision: int,
        expected_target_revision: int,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Move all signals from one active Incident to another as one audited command."""
        reason = reason.strip()
        if not reason or len(reason) > 1000:
            raise ProductError("INVALID_INCIDENT_MERGE", "合并原因须为 1–1000 个字符", 422)
        if incident_id == target_incident_id:
            raise ProductError("INVALID_INCIDENT_MERGE", "不能将事故合并到自身", 422)
        payload = {
            "workspace_id": workspace_id, "application_id": application_id,
            "environment_id": environment_id, "incident_id": incident_id,
            "target_incident_id": target_incident_id,
            "expected_incident_revision": expected_incident_revision,
            "expected_target_revision": expected_target_revision, "reason": reason,
        }
        timestamp = _now()
        active_states = {IncidentState.OPEN, IncidentState.INVESTIGATING, IncidentState.NEEDS_INPUT}
        with self.tx() as db:
            self._require_application(db, workspace_id, application_id)
            replay = self._replay_command(db, command_name="incident.merge", scope_key=workspace_id,
                idempotency_key=idempotency_key, payload=payload)
            if replay is not None:
                return replay
            source_row = db.execute(
                """SELECT revision,data FROM incidents WHERE id=? AND workspace_id=?
                   AND application_id=? AND environment_id=?""",
                (incident_id, workspace_id, application_id, environment_id),
            ).fetchone()
            target_row = db.execute(
                """SELECT revision,data FROM incidents WHERE id=? AND workspace_id=?
                   AND application_id=? AND environment_id=?""",
                (target_incident_id, workspace_id, application_id, environment_id),
            ).fetchone()
            if source_row is None or target_row is None:
                raise ProductError("INCIDENT_NOT_FOUND", "待合并事故不属于该应用和环境或不存在", 404)
            if int(source_row["revision"]) != expected_incident_revision:
                raise ProductError("STALE_INCIDENT_REVISION", "待合并事故已变化，请刷新后重试", 409)
            if int(target_row["revision"]) != expected_target_revision:
                raise ProductError("STALE_TARGET_INCIDENT_REVISION", "目标事故已变化，请刷新后重试", 409)
            source = json.loads(source_row["data"])
            target = json.loads(target_row["data"])
            if IncidentState(source["state"]) not in active_states or IncidentState(target["state"]) not in active_states:
                raise ProductError("INCIDENT_NOT_MERGEABLE", "仅未结案事故可以合并", 409)
            active_run = db.execute(
                """SELECT 1 FROM investigation_runs WHERE workspace_id=? AND application_id=?
                   AND environment_id=? AND incident_id IN (?,?) AND state IN ('queued','running') LIMIT 1""",
                (workspace_id, application_id, environment_id, incident_id, target_incident_id),
            ).fetchone()
            if active_run:
                raise ProductError("ACTIVE_INVESTIGATION_EXISTS", "事故存在进行中的调查，结束调查后才能合并", 409)
            source_signal_ids = list(source.get("signal_ids") or [])
            target_signal_ids = list(target.get("signal_ids") or [])
            if not source_signal_ids or len(set(source_signal_ids + target_signal_ids)) != len(source_signal_ids + target_signal_ids):
                raise ProductError("INCIDENT_SIGNAL_INTEGRITY", "事故信号关系不完整或重复，拒绝合并", 409)
            linked_source_ids = [row[0] for row in db.execute(
                """SELECT signal_id FROM incident_signals WHERE workspace_id=? AND application_id=?
                   AND environment_id=? AND incident_id=? ORDER BY linked_at,signal_id""",
                (workspace_id, application_id, environment_id, incident_id),
            ).fetchall()]
            linked_target_ids = [row[0] for row in db.execute(
                """SELECT signal_id FROM incident_signals WHERE workspace_id=? AND application_id=?
                   AND environment_id=? AND incident_id=? ORDER BY linked_at,signal_id""",
                (workspace_id, application_id, environment_id, target_incident_id),
            ).fetchall()]
            if set(linked_source_ids) != set(source_signal_ids) or set(linked_target_ids) != set(target_signal_ids):
                raise ProductError("INCIDENT_SIGNAL_INTEGRITY", "事故记录与信号关系表不一致，拒绝合并", 409)

            target_updated = dict(target)
            target_updated.update({
                "revision": expected_target_revision + 1,
                "updated_at": timestamp,
                "signal_ids": [*target_signal_ids, *source_signal_ids],
            })
            source_updated = dict(source)
            source_updated.update({
                "revision": expected_incident_revision + 1,
                "updated_at": timestamp,
                "signal_ids": [],
                "state": IncidentState.MERGED.value,
                "outcome": "merged",
                "merged_into_id": target_incident_id,
                "assignee_id": None,
            })
            try:
                target_validated = Incident.model_validate_json(json.dumps(target_updated)).model_dump(mode="json")
                source_validated = Incident.model_validate_json(json.dumps(source_updated)).model_dump(mode="json")
            except ValueError as exc:
                raise ProductError("INVALID_INCIDENT", "合并后的事故不符合产品契约", 422) from exc
            db.execute(
                """UPDATE incidents SET revision=?,data=?,updated_at=? WHERE id=? AND revision=?""",
                (target_validated["revision"], _encode(target_validated), timestamp,
                 target_incident_id, expected_target_revision),
            )
            db.execute(
                """UPDATE incidents SET revision=?,data=?,updated_at=? WHERE id=? AND revision=?""",
                (source_validated["revision"], _encode(source_validated), timestamp,
                 incident_id, expected_incident_revision),
            )
            db.execute(
                """UPDATE incident_signals SET incident_id=? WHERE workspace_id=? AND application_id=?
                   AND environment_id=? AND incident_id=?""",
                (target_incident_id, workspace_id, application_id, environment_id, incident_id),
            )
            self._workspace_event(
                db, workspace_id=workspace_id, application_id=application_id,
                resource_type="incident", resource_id=incident_id, event_type="incident.merged",
                record=source_validated,
                event_metadata={"target_incident_id": target_incident_id,
                    "moved_signal_ids": source_signal_ids, "reason": reason},
            )
            self._workspace_event(
                db, workspace_id=workspace_id, application_id=application_id,
                resource_type="incident", resource_id=target_incident_id, event_type="incident.signals_merged",
                record=target_validated,
                event_metadata={"source_incident_id": incident_id,
                    "moved_signal_ids": source_signal_ids, "reason": reason},
            )
            response = {"source": source_validated, "target": target_validated}
            self._save_command_receipt(db, command_name="incident.merge", scope_key=workspace_id,
                idempotency_key=idempotency_key, payload=payload, response=response)
        return response

    def split_incident(
        self,
        workspace_id: str,
        application_id: str,
        environment_id: str,
        incident_id: str,
        title: str,
        severity: str,
        signals: list[dict[str, Any]],
        reason: str,
        *,
        expected_incident_revision: int,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Move selected linked signals to a new Incident while retaining at least one on the source."""
        title = title.strip()
        reason = reason.strip()
        if not title or len(title) > 500 or not reason or len(reason) > 1000:
            raise ProductError("INVALID_INCIDENT_SPLIT", "拆分标题须为 1–500 字，原因须为 1–1000 字", 422)
        if not signals or len(signals) > 99:
            raise ProductError("INVALID_INCIDENT_SPLIT", "一次拆分须选择 1–99 条信号", 422)
        signal_ids = [str(item.get("signal_id", "")) for item in signals]
        if any(not item for item in signal_ids) or len(set(signal_ids)) != len(signal_ids):
            raise ProductError("INVALID_INCIDENT_SIGNALS", "拆分信号 ID 必须唯一且有效", 422)
        payload = {
            "workspace_id": workspace_id, "application_id": application_id,
            "environment_id": environment_id, "incident_id": incident_id,
            "expected_incident_revision": expected_incident_revision,
            "title": title, "severity": severity, "signals": signals, "reason": reason,
        }
        child_id = _uid("inc")
        timestamp = _now()
        active_states = {IncidentState.OPEN, IncidentState.INVESTIGATING, IncidentState.NEEDS_INPUT}
        with self.tx() as db:
            self._require_application(db, workspace_id, application_id)
            replay = self._replay_command(db, command_name="incident.split", scope_key=workspace_id,
                idempotency_key=idempotency_key, payload=payload)
            if replay is not None:
                return replay
            row = db.execute(
                """SELECT revision,data FROM incidents WHERE id=? AND workspace_id=?
                   AND application_id=? AND environment_id=?""",
                (incident_id, workspace_id, application_id, environment_id),
            ).fetchone()
            if row is None:
                raise ProductError("INCIDENT_NOT_FOUND", "事故不属于该应用和环境或不存在", 404)
            if int(row["revision"]) != expected_incident_revision:
                raise ProductError("STALE_INCIDENT_REVISION", "事故已变化，请刷新后再拆分", 409)
            incident = json.loads(row["data"])
            if IncidentState(incident["state"]) not in active_states:
                raise ProductError("INCIDENT_NOT_SPLITTABLE", "仅未结案事故可以拆分", 409)
            active_run = db.execute(
                """SELECT 1 FROM investigation_runs WHERE workspace_id=? AND application_id=?
                   AND environment_id=? AND incident_id=? AND state IN ('queued','running') LIMIT 1""",
                (workspace_id, application_id, environment_id, incident_id),
            ).fetchone()
            if active_run:
                raise ProductError("ACTIVE_INVESTIGATION_EXISTS", "事故存在进行中的调查，结束调查后才能拆分", 409)
            existing_ids = list(incident.get("signal_ids") or [])
            if len(signal_ids) >= len(existing_ids) or not set(signal_ids).issubset(existing_ids):
                raise ProductError("INVALID_INCIDENT_SPLIT_SIGNALS", "拆分后原事故必须保留至少一条自身信号", 409)
            linked_ids = {row[0] for row in db.execute(
                """SELECT signal_id FROM incident_signals WHERE workspace_id=? AND application_id=?
                   AND environment_id=? AND incident_id=?""",
                (workspace_id, application_id, environment_id, incident_id),
            ).fetchall()}
            if linked_ids != set(existing_ids):
                raise ProductError("INCIDENT_SIGNAL_INTEGRITY", "事故记录与信号关系表不一致，拒绝拆分", 409)
            for reference in signals:
                signal_row = db.execute(
                    """SELECT revision,data FROM signals WHERE id=? AND workspace_id=?
                       AND application_id=? AND environment_id=?""",
                    (reference["signal_id"], workspace_id, application_id, environment_id),
                ).fetchone()
                if signal_row is None:
                    raise ProductError("SIGNAL_NOT_FOUND", "一个或多个信号不属于此应用和环境", 404)
                if int(signal_row["revision"]) != reference["expected_revision"]:
                    raise ProductError("STALE_SIGNAL_REVISION", "信号已变化，请刷新后再拆分", 409)
                signal_record = json.loads(signal_row["data"])
                if signal_record.get("status") != "clustered":
                    raise ProductError("SIGNAL_NOT_CLUSTERED", "仅事故中的已归并信号可拆分", 409)

            remaining_ids = [item for item in existing_ids if item not in set(signal_ids)]
            parent_updated = dict(incident)
            parent_updated.update({"revision": expected_incident_revision + 1,
                "updated_at": timestamp, "signal_ids": remaining_ids})
            try:
                parent_validated = Incident.model_validate_json(json.dumps(parent_updated)).model_dump(mode="json")
                child_validated = Incident(
                    id=child_id, workspace_id=workspace_id, application_id=application_id,
                    environment_id=environment_id, revision=1, created_at=timestamp, updated_at=timestamp,
                    title=title, severity=severity, signal_ids=signal_ids, state=IncidentState.OPEN, outcome=None,
                ).model_dump(mode="json")
            except ValueError as exc:
                raise ProductError("INVALID_INCIDENT", "拆分后的事故不符合产品契约", 422) from exc
            db.execute(
                """UPDATE incidents SET revision=?,data=?,updated_at=? WHERE id=? AND revision=?""",
                (parent_validated["revision"], _encode(parent_validated), timestamp,
                 incident_id, expected_incident_revision),
            )
            db.execute(
                """INSERT INTO incidents(id,workspace_id,application_id,environment_id,revision,data,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (child_id, workspace_id, application_id, environment_id, 1,
                 _encode(child_validated), timestamp, timestamp),
            )
            for signal_id in signal_ids:
                db.execute(
                    """UPDATE incident_signals SET incident_id=? WHERE workspace_id=? AND application_id=?
                       AND environment_id=? AND incident_id=? AND signal_id=?""",
                    (child_id, workspace_id, application_id, environment_id, incident_id, signal_id),
                )
            self._workspace_event(
                db, workspace_id=workspace_id, application_id=application_id,
                resource_type="incident", resource_id=incident_id, event_type="incident.signals_split",
                record=parent_validated,
                event_metadata={"created_incident_id": child_id, "moved_signal_ids": signal_ids,
                    "reason": reason},
            )
            self._workspace_event(
                db, workspace_id=workspace_id, application_id=application_id,
                resource_type="incident", resource_id=child_id, event_type="incident.created_by_split",
                record=child_validated,
                event_metadata={"source_incident_id": incident_id, "moved_signal_ids": signal_ids,
                    "reason": reason},
            )
            response = {"source": parent_validated, "created": child_validated}
            self._save_command_receipt(db, command_name="incident.split", scope_key=workspace_id,
                idempotency_key=idempotency_key, payload=payload, response=response)
        return response

    @staticmethod
    def _default_task_graph_specs(mode: str, evidence_records: list[dict[str, Any]]) -> list[TaskSpec]:
        """Build the first bounded Section9 task plan from frozen source bindings.

        This deterministic plan is Section9-owned orchestration. It is not an
        EvoMap task graph or proof of independent model execution.
        """
        grouped: dict[str, list[str]] = {}
        for evidence in evidence_records:
            grouped.setdefault(str(evidence["source_binding_id"]), []).append(str(evidence["id"]))
        if not grouped:
            grouped["no-evidence"] = []
        all_evidence_ids = [str(item["id"]) for item in evidence_records]
        if mode == "single":
            return [
                TaskSpec(
                    task_key="investigate", role="investigator", capability="evidence.investigate",
                    title="使用完整冻结证据进行单路调查", dependencies=[],
                    input_evidence_ids=all_evidence_ids,
                ),
                TaskSpec(
                    task_key="synthesize", role="synthesizer", capability="conclusion.synthesize",
                    title="汇总单路调查结论", dependencies=["investigate"],
                    input_evidence_ids=all_evidence_ids,
                ),
            ]
        groups = [grouped[key] for key in sorted(grouped)]
        if len(groups) > 12:
            merged: list[list[str]] = [[] for _ in range(12)]
            for index, evidence_ids in enumerate(groups):
                merged[index % 12].extend(evidence_ids)
            groups = merged

        investigators = [
            TaskSpec(
                task_key=f"investigate-{index + 1}", role="investigator",
                capability="evidence.investigate", title=f"独立调查来源组 {index + 1}",
                dependencies=[], input_evidence_ids=evidence_ids,
            )
            for index, evidence_ids in enumerate(groups)
        ]
        investigator_keys = [item.task_key for item in investigators]
        seed = TaskSpec(
            task_key="challenge-seed", role="challenger_seed",
            capability="counterexample.prepare", title="先列独立约束与反例问题",
            dependencies=[], input_evidence_ids=all_evidence_ids,
        )
        review = TaskSpec(
            task_key="challenge-review", role="challenger_review",
            capability="counterexample.review", title="对照调查结论复核反例",
            dependencies=[*investigator_keys, seed.task_key],
            input_evidence_ids=all_evidence_ids,
        )
        synth = TaskSpec(
            task_key="synthesize", role="synthesizer", capability="conclusion.synthesize",
            title="汇总调查与独立复核", dependencies=[*investigator_keys, review.task_key],
            input_evidence_ids=all_evidence_ids,
        )
        return [*investigators, seed, review, synth]

    def _insert_investigation_task_graph(
        self,
        db: sqlite3.Connection,
        *,
        workspace_id: str,
        application_id: str,
        environment_id: str,
        incident_id: str,
        run_id: str,
        mode: str,
        evidence_records: list[dict[str, Any]],
        timestamp: str,
    ) -> dict[str, Any]:
        task_graph_input = TaskGraphCreate.model_validate({
            "environment_id": environment_id,
            "expected_run_revision": 1,
            "mode": mode,
            "tasks": [item.model_dump(mode="json") for item in self._default_task_graph_specs(mode, evidence_records)],
        })
        evidence_ids = {str(item["id"]) for item in evidence_records}
        requested_evidence_ids = {
            evidence_id for item in task_graph_input.tasks for evidence_id in item.input_evidence_ids
        }
        if not requested_evidence_ids.issubset(evidence_ids):
            raise ProductError("EVIDENCE_NOT_FOUND", "任务图只能引用冻结在该调查中的证据", 404)

        graph_id = _uid("taskgraph")
        task_ids = {item.task_key: _uid("task") for item in task_graph_input.tasks}
        tasks: list[Task] = []
        for spec in task_graph_input.tasks:
            task = Task(
                id=task_ids[spec.task_key], workspace_id=workspace_id, revision=1,
                created_at=timestamp, updated_at=timestamp, application_id=application_id,
                environment_id=environment_id, incident_id=incident_id, run_id=run_id,
                task_key=spec.task_key, role=spec.role, capability=spec.capability,
                title=spec.title, dependencies=[task_ids[key] for key in spec.dependencies],
                input_evidence_ids=spec.input_evidence_ids, epoch=0, holder_id=None,
                lease_deadline=None, state=TaskState.OPEN, result=None, failure_reason=None,
                attempt_history=[],
            )
            tasks.append(task)
        graph_payload = {
            "mode": task_graph_input.mode,
            "tasks": [item.model_dump(mode="json") for item in task_graph_input.tasks],
        }
        graph_sha = hashlib.sha256(_encode(graph_payload).encode("utf-8")).hexdigest()
        graph = TaskGraph(
            id=graph_id, workspace_id=workspace_id, revision=1,
            created_at=timestamp, updated_at=timestamp, application_id=application_id,
            environment_id=environment_id, incident_id=incident_id, run_id=run_id,
            mode=mode, graph_sha256=graph_sha, state="ready", tasks=tasks,
        )
        graph_record = graph.model_dump(mode="json")
        db.execute(
            """INSERT INTO investigation_task_graphs(
                id,workspace_id,application_id,environment_id,incident_id,run_id,revision,state,
                graph_sha256,data,created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (graph_id, workspace_id, application_id, environment_id, incident_id, run_id, 1,
             graph_record["state"], graph_sha, _encode(graph_record), timestamp, timestamp),
        )
        for task in tasks:
            task_record = task.model_dump(mode="json")
            db.execute(
                """INSERT INTO investigation_tasks(
                    id,workspace_id,application_id,environment_id,incident_id,run_id,graph_id,task_key,
                    revision,state,epoch,holder_id,lease_deadline,data,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (task.id, workspace_id, application_id, environment_id, incident_id, run_id, graph_id,
                 task.task_key, task.revision, task.state.value, task.epoch, None, None,
                 _encode(task_record), timestamp, timestamp),
            )
        self._workspace_event(
            db, workspace_id=workspace_id, application_id=application_id,
            resource_type="investigation_task_graph", resource_id=graph_id,
            event_type="investigation_task_graph.created", record=graph_record,
            event_metadata={"mode": mode, "task_count": len(tasks), "planner": "section9-deterministic-v1"},
        )
        return graph_record

    def create_manual_investigation_run(
        self,
        workspace_id: str,
        application_id: str,
        environment_id: str,
        incident_id: str,
        *,
        expected_incident_revision: int,
        idempotency_key: str,
    ) -> dict[str, Any]:
        return self.create_investigation_run(
            workspace_id, application_id, environment_id, incident_id,
            expected_incident_revision=expected_incident_revision,
            idempotency_key=idempotency_key, execution_mode="manual",
        )

    def create_investigation_run(
        self,
        workspace_id: str,
        application_id: str,
        environment_id: str,
        incident_id: str,
        *,
        expected_incident_revision: int,
        idempotency_key: str,
        execution_mode: str = "manual",
    ) -> dict[str, Any]:
        if execution_mode not in {"manual", "single", "swarm"}:
            raise ProductError("INVALID_INVESTIGATION_MODE", "调查模式无效", 422)
        payload = {
            "workspace_id": workspace_id, "application_id": application_id,
            "environment_id": environment_id, "incident_id": incident_id,
            "expected_incident_revision": expected_incident_revision,
            "execution_mode": execution_mode,
        }
        command_name = f"investigation_run.create_{execution_mode}"
        run_id = _uid("run")
        timestamp = _now()
        with self.tx() as db:
            self._require_application(db, workspace_id, application_id)
            replay = self._replay_command(db, command_name=command_name,
                scope_key=workspace_id, idempotency_key=idempotency_key, payload=payload)
            if replay is not None:
                return replay
            incident_row = db.execute(
                """SELECT revision,data FROM incidents WHERE id=? AND workspace_id=?
                   AND application_id=? AND environment_id=?""",
                (incident_id, workspace_id, application_id, environment_id),
            ).fetchone()
            if incident_row is None:
                raise ProductError("INCIDENT_NOT_FOUND", "事故不属于该应用和环境或不存在", 404)
            if int(incident_row["revision"]) != expected_incident_revision:
                raise ProductError("STALE_INCIDENT_REVISION", "事故已变化，请刷新后再开始调查", 409)
            incident = json.loads(incident_row["data"])
            incident_state = IncidentState(incident["state"])
            if incident_state not in {IncidentState.OPEN, IncidentState.INVESTIGATING, IncidentState.NEEDS_INPUT}:
                raise ProductError("INCIDENT_NOT_INVESTIGATABLE", "只有未结案事故可以启动调查", 409)
            active = db.execute(
                """SELECT 1 FROM investigation_runs WHERE workspace_id=? AND application_id=?
                   AND environment_id=? AND incident_id=? AND state IN ('queued','running') LIMIT 1""",
                (workspace_id, application_id, environment_id, incident_id),
            ).fetchone()
            if active:
                raise ProductError("ACTIVE_INVESTIGATION_EXISTS", "该事故已有进行中的调查运行", 409)
            workspace_row = db.execute(
                "SELECT policy_revision,data FROM workspaces WHERE id=?", (workspace_id,),
            ).fetchone()
            if workspace_row is None:
                raise ProductError("WORKSPACE_NOT_FOUND", "工作区不存在", 404)
            workspace_data = json.loads(workspace_row["data"])
            budget = workspace_data.get("budget", {})
            signal_rows = db.execute(
                """SELECT s.data FROM incident_signals link JOIN signals s
                   ON s.id=link.signal_id AND s.workspace_id=link.workspace_id
                   AND s.application_id=link.application_id AND s.environment_id=link.environment_id
                   WHERE link.workspace_id=? AND link.application_id=? AND link.environment_id=?
                     AND link.incident_id=? ORDER BY link.linked_at,link.signal_id""",
                (workspace_id, application_id, environment_id, incident_id),
            ).fetchall()
            signal_records = [json.loads(row["data"]) for row in signal_rows]
            snapshot = redact_secrets({
                "schema_version": "investigation-input-v1",
                "captured_at": timestamp,
                "incident": incident,
                "signals": signal_records,
            })
            snapshot_sha = hashlib.sha256(_encode(snapshot).encode("utf-8")).hexdigest()
            try:
                run = InvestigationRun(
                    id=run_id, workspace_id=workspace_id, application_id=application_id,
                    environment_id=environment_id, incident_id=incident_id, revision=1,
                    created_at=timestamp, updated_at=timestamp,
                    input_snapshot_sha256=snapshot_sha,
                    policy_revision=int(workspace_row["policy_revision"]),
                    token_limit=int(budget.get("token_limit") if budget.get("token_limit") is not None
                                    else budget.get("validation_reserve_tokens") or 0),
                    validation_reserve_tokens=int(budget.get("validation_reserve_tokens") or 0),
                    execution_mode=execution_mode, state=InvestigationRunState.RUNNING,
                    result_type=None, started_at=timestamp, completed_at=None,
                )
            except ValueError as exc:
                raise ProductError("INVALID_INVESTIGATION_RUN", "调查运行不符合产品契约", 422) from exc
            run_record = run.model_dump(mode="json")
            db.execute(
                """INSERT INTO investigation_runs(
                    id,workspace_id,application_id,environment_id,incident_id,revision,state,data,
                    snapshot_json,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (run_id, workspace_id, application_id, environment_id, incident_id, 1,
                 run_record["state"], _encode(run_record), _encode(snapshot), timestamp, timestamp),
            )
            evidence_records: list[dict[str, Any]] = []
            for signal in signal_records:
                evidence_id = _uid("ev")
                evidence = Evidence(
                    id=evidence_id, workspace_id=workspace_id, application_id=application_id,
                    environment_id=environment_id, incident_id=incident_id, run_id=run_id,
                    revision=1, created_at=timestamp, updated_at=timestamp,
                    source_binding_id=signal["source_binding_id"],
                    source_version=signal.get("source_version") or f"revision:{signal['revision']}",
                    origin=f"section9-signal://{signal['id']}",
                    source_occurred_at=signal.get("occurred_at"),
                    captured_at=signal["observed_at"],
                    content_sha256=hashlib.sha256(_encode(signal).encode("utf-8")).hexdigest(),
                    classification=EvidenceClass.INTERNAL,
                    raw_blob_ref=None, sanitized_blob_ref=None,
                    # A sampled signal does not prove that the source time window was fully covered.
                    coverage_complete=False,
                )
                evidence_record = evidence.model_dump(mode="json")
                db.execute(
                    """INSERT INTO investigation_evidence(
                        id,workspace_id,application_id,environment_id,incident_id,run_id,revision,data,created_at
                    ) VALUES(?,?,?,?,?,?,?,?,?)""",
                    (evidence_id, workspace_id, application_id, environment_id, incident_id, run_id,
                     1, _encode(evidence_record), timestamp),
                )
                evidence_records.append(evidence_record)
                self._workspace_event(db, workspace_id=workspace_id, application_id=application_id,
                    resource_type="evidence", resource_id=evidence_id, event_type="evidence.captured",
                    record=evidence_record, event_metadata={"source_signal_id": signal["id"], "mode": f"{execution_mode}_snapshot"})

            incident_updated = dict(incident)
            if incident_state != IncidentState.INVESTIGATING:
                incident_updated.update({
                    "revision": expected_incident_revision + 1,
                    "updated_at": timestamp,
                    "state": IncidentState.INVESTIGATING.value,
                    "outcome": None,
                })
                incident_updated = Incident.model_validate_json(json.dumps(incident_updated)).model_dump(mode="json")
                db.execute(
                    """UPDATE incidents SET revision=?,data=?,updated_at=? WHERE id=? AND revision=?""",
                    (incident_updated["revision"], _encode(incident_updated), timestamp,
                     incident_id, expected_incident_revision),
                )
                self._workspace_event(db, workspace_id=workspace_id, application_id=application_id,
                    resource_type="incident", resource_id=incident_id, event_type="incident.state_changed",
                    record=incident_updated, event_metadata={"from_state": incident_state.value,
                        "to_state": IncidentState.INVESTIGATING.value, "reason": "manual_investigation_started"})
            task_graph_record = None
            if execution_mode in {"single", "swarm"}:
                task_graph_record = self._insert_investigation_task_graph(
                    db, workspace_id=workspace_id, application_id=application_id,
                    environment_id=environment_id, incident_id=incident_id, run_id=run_id,
                    mode=execution_mode, evidence_records=evidence_records, timestamp=timestamp,
                )
            self._workspace_event(db, workspace_id=workspace_id, application_id=application_id,
                resource_type="investigation_run", resource_id=run_id, event_type="investigation_run.started",
                record=run_record, event_metadata={"execution_mode": execution_mode, "snapshot_sha256": snapshot_sha})
            response = {"run": run_record, "input_snapshot": snapshot, "evidence": evidence_records}
            if task_graph_record is not None:
                response["task_graph"] = task_graph_record
            self._save_command_receipt(db, command_name=command_name,
                scope_key=workspace_id, idempotency_key=idempotency_key, payload=payload, response=response)
        return response

    def list_investigation_runs(
        self, workspace_id: str, application_id: str, environment_id: str,
        *, incident_id: str | None = None, limit: int = 200,
    ) -> list[dict[str, Any]]:
        if not environment_id or not 1 <= limit <= 1000:
            raise ProductError("INVALID_INVESTIGATION_PAGE", "调查运行分页参数无效", 422)
        with self.tx() as db:
            self._require_application(db, workspace_id, application_id)
            if incident_id is not None:
                if db.execute(
                    "SELECT 1 FROM incidents WHERE id=? AND workspace_id=? AND application_id=? AND environment_id=?",
                    (incident_id, workspace_id, application_id, environment_id),
                ).fetchone() is None:
                    raise ProductError("INCIDENT_NOT_FOUND", "事故不属于该应用和环境或不存在", 404)
                rows = db.execute(
                    """SELECT data FROM investigation_runs WHERE workspace_id=? AND application_id=?
                       AND environment_id=? AND incident_id=? ORDER BY created_at DESC,id DESC LIMIT ?""",
                    (workspace_id, application_id, environment_id, incident_id, limit),
                ).fetchall()
            else:
                rows = db.execute(
                    """SELECT data FROM investigation_runs WHERE workspace_id=? AND application_id=?
                       AND environment_id=? ORDER BY created_at DESC,id DESC LIMIT ?""",
                    (workspace_id, application_id, environment_id, limit),
                ).fetchall()
        return [json.loads(row["data"]) for row in rows]

    def get_investigation_run_detail(
        self, workspace_id: str, application_id: str, environment_id: str, run_id: str,
    ) -> dict[str, Any] | None:
        with self.tx() as db:
            run_row = db.execute(
                """SELECT data,snapshot_json,incident_id FROM investigation_runs WHERE id=?
                   AND workspace_id=? AND application_id=? AND environment_id=?""",
                (run_id, workspace_id, application_id, environment_id),
            ).fetchone()
            if run_row is None:
                return None
            scope = (workspace_id, application_id, environment_id, run_row["incident_id"], run_id)
            evidence = db.execute(
                """SELECT data FROM investigation_evidence WHERE workspace_id=? AND application_id=?
                   AND environment_id=? AND incident_id=? AND run_id=? ORDER BY created_at,id LIMIT 1000""",
                scope,
            ).fetchall()
            hypotheses = db.execute(
                """SELECT data FROM investigation_hypotheses WHERE workspace_id=? AND application_id=?
                   AND environment_id=? AND incident_id=? AND run_id=? ORDER BY created_at,id LIMIT 1000""",
                scope,
            ).fetchall()
            graph_row = db.execute(
                """SELECT * FROM investigation_task_graphs WHERE workspace_id=? AND application_id=?
                   AND environment_id=? AND run_id=?""",
                (workspace_id, application_id, environment_id, run_id),
            ).fetchone()
            task_graph = self._task_graph_detail_in_tx(db, graph_row) if graph_row is not None else None
            usage_rows = db.execute(
                """SELECT request_id,task_id,task_epoch,provider,model,prompt_sha256,reserved_tokens,
                          actual_tokens,state,provider_called,error_code,created_at,updated_at
                   FROM investigation_model_usage WHERE workspace_id=? AND application_id=?
                     AND environment_id=? AND run_id=? ORDER BY created_at,request_id""",
                (workspace_id, application_id, environment_id, run_id),
            ).fetchall()
        result = {
            "run": json.loads(run_row["data"]),
            "input_snapshot": json.loads(run_row["snapshot_json"]),
            "evidence": [json.loads(row["data"]) for row in evidence],
            "hypotheses": [json.loads(row["data"]) for row in hypotheses],
            "model_usage": [dict(row) for row in usage_rows],
        }
        if task_graph is not None:
            result["task_graph"] = task_graph
        return result

    @staticmethod
    def _require_task_model_authority_in_tx(
        db: sqlite3.Connection, *, workspace_id: str, application_id: str,
        environment_id: str, run_id: str, task_id: str, worker_id: str, epoch: int,
    ) -> tuple[sqlite3.Row, Task, dict[str, Any]]:
        row = db.execute(
            """SELECT t.data AS task_data,r.data AS run_data,r.state AS run_state,
                      w.policy_revision,w.data AS workspace_data
               FROM investigation_tasks t
               JOIN investigation_runs r ON r.id=t.run_id AND r.workspace_id=t.workspace_id
                 AND r.application_id=t.application_id AND r.environment_id=t.environment_id
                 AND r.incident_id=t.incident_id
               JOIN workspaces w ON w.id=t.workspace_id
               WHERE t.id=? AND t.workspace_id=? AND t.application_id=?
                 AND t.environment_id=? AND t.run_id=?""",
            (task_id, workspace_id, application_id, environment_id, run_id),
        ).fetchone()
        if row is None:
            raise ProductError("INVESTIGATION_TASK_NOT_FOUND", "任务不属于该调查或不存在", 404)
        if row["run_state"] != InvestigationRunState.RUNNING.value:
            raise ProductError("INVESTIGATION_RUN_TERMINAL", "终态调查不能发起模型请求", 409)
        run = json.loads(row["run_data"])
        if int(row["policy_revision"]) != int(run["policy_revision"]):
            raise ProductError("POLICY_REVISION_CHANGED", "工作区预算策略已变化，请基于新策略重新启动调查", 409)
        task = Task.model_validate_json(row["task_data"])
        if task.state not in {TaskState.CLAIMED, TaskState.RUNNING} or task.holder_id != worker_id or task.epoch != epoch:
            raise ProductError("TASK_LEASE_FENCED", "Worker 不持有此任务的当前租约", 409)
        if (not task.lease_deadline or
                datetime.fromisoformat(task.lease_deadline.replace("Z", "+00:00")) <= datetime.now(timezone.utc)):
            raise ProductError("TASK_LEASE_EXPIRED", "任务租约已过期，拒绝模型请求", 409)
        return row, task, json.loads(row["workspace_data"])

    @staticmethod
    def _investigation_model_usage_total_in_tx(db: sqlite3.Connection, *, run_id: str) -> int:
        row = db.execute(
            """SELECT COALESCE(SUM(CASE
                       WHEN state='settled' THEN COALESCE(actual_tokens,0)
                       ELSE reserved_tokens END),0) AS used
               FROM investigation_model_usage WHERE run_id=?
                 AND state IN ('reserved','dispatch_committed','settled','outcome_unknown')""",
            (run_id,),
        ).fetchone()
        return int(row["used"] or 0)

    def reserve_investigation_model_usage(
        self, workspace_id: str, application_id: str, environment_id: str, run_id: str,
        task_id: str, worker_id: str, epoch: int, *, request_id: str, provider: str,
        model: str, prompt_sha256: str, reserved_tokens: int,
    ) -> dict[str, Any]:
        if (not request_id or len(request_id) > 200 or not provider or len(provider) > 200
                or not model or len(model) > 200 or len(prompt_sha256) != 64
                or any(char not in "0123456789abcdef" for char in prompt_sha256.lower())
                or type(reserved_tokens) is not int or reserved_tokens < 1):
            raise ProductError("INVALID_MODEL_RESERVATION", "模型预算预留参数无效", 422)
        timestamp = _now()
        with self.tx() as db:
            self._require_application(db, workspace_id, application_id)
            _, task, workspace = self._require_task_model_authority_in_tx(
                db, workspace_id=workspace_id, application_id=application_id,
                environment_id=environment_id, run_id=run_id, task_id=task_id,
                worker_id=worker_id, epoch=epoch,
            )
            if db.execute("SELECT 1 FROM investigation_model_usage WHERE task_id=? AND task_epoch=?",
                          (task_id, epoch)).fetchone():
                raise ProductError("TASK_MODEL_REQUEST_EXISTS", "此任务尝试已登记模型请求，不会自动重复调用", 409)
            run_row = db.execute("SELECT data FROM investigation_runs WHERE id=?", (run_id,)).fetchone()
            run = json.loads(run_row["data"])
            budget = workspace.get("budget", {})
            available = int(run["token_limit"]) - int(run["validation_reserve_tokens"])
            used = self._investigation_model_usage_total_in_tx(db, run_id=run_id)
            if used + reserved_tokens > available:
                raise ProductError("TOKEN_BUDGET_EXHAUSTED", "调查运行剩余模型预算不足，尚未发出供应商请求", 409)
            concurrency = int(budget.get("max_concurrency", 1))
            in_flight = db.execute(
                """SELECT COUNT(*) FROM investigation_model_usage WHERE workspace_id=?
                   AND state IN ('reserved','dispatch_committed')""", (workspace_id,),
            ).fetchone()[0]
            if int(in_flight) >= concurrency:
                raise ProductError("MODEL_CONCURRENCY_LIMIT", "工作区模型并发额度已满", 409)
            try:
                db.execute(
                    """INSERT INTO investigation_model_usage(
                       request_id,workspace_id,application_id,environment_id,incident_id,run_id,task_id,
                       task_epoch,provider,model,prompt_sha256,reserved_tokens,actual_tokens,state,
                       provider_called,error_code,created_at,updated_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,NULL,'reserved',0,NULL,?,?)""",
                    (request_id, workspace_id, application_id, environment_id, task.incident_id,
                     run_id, task_id, epoch, provider, model, prompt_sha256, reserved_tokens,
                     timestamp, timestamp),
                )
            except sqlite3.IntegrityError as exc:
                raise ProductError("TASK_MODEL_REQUEST_EXISTS", "此任务尝试已登记模型请求，不会自动重复调用", 409) from exc
            record = {"revision": 1, "request_id": request_id, "task_id": task_id, "task_epoch": epoch,
                      "provider": provider, "model": model, "reserved_tokens": reserved_tokens,
                      "actual_tokens": None, "state": "reserved", "provider_called": False,
                      "error_code": None, "created_at": timestamp, "updated_at": timestamp}
            self._workspace_event(db, workspace_id=workspace_id, application_id=application_id,
                resource_type="model_usage", resource_id=request_id,
                event_type="investigation.model_budget_reserved", record=record,
                event_metadata={"run_id": run_id, "task_id": task_id, "task_epoch": epoch,
                    "prompt_sha256": prompt_sha256, "reserved_tokens": reserved_tokens})
        return record

    def authorize_investigation_model_dispatch(
        self, workspace_id: str, application_id: str, environment_id: str, run_id: str,
        task_id: str, worker_id: str, epoch: int, *, request_id: str,
    ) -> dict[str, Any]:
        timestamp = _now()
        with self.tx() as db:
            self._require_application(db, workspace_id, application_id)
            _, task, _ = self._require_task_model_authority_in_tx(
                db, workspace_id=workspace_id, application_id=application_id,
                environment_id=environment_id, run_id=run_id, task_id=task_id,
                worker_id=worker_id, epoch=epoch,
            )
            row = db.execute(
                """SELECT reserved_tokens,state FROM investigation_model_usage WHERE request_id=?
                   AND workspace_id=? AND application_id=? AND environment_id=? AND run_id=? AND task_id=?
                     AND task_epoch=?""",
                (request_id, workspace_id, application_id, environment_id, run_id, task_id, epoch),
            ).fetchone()
            if row is None or row["state"] != "reserved":
                raise ProductError("MODEL_RESERVATION_INVALID", "模型预算预留不存在或已使用", 409)
            run_row = db.execute("SELECT data FROM investigation_runs WHERE id=?", (run_id,)).fetchone()
            run = json.loads(run_row["data"])
            if self._investigation_model_usage_total_in_tx(db, run_id=run_id) > int(run["token_limit"]) - int(run["validation_reserve_tokens"]):
                raise ProductError("TOKEN_BUDGET_EXHAUSTED", "策略复核后模型预算不足，尚未发出供应商请求", 409)
            db.execute("UPDATE investigation_model_usage SET state='dispatch_committed',updated_at=? WHERE request_id=? AND state='reserved'",
                       (timestamp, request_id))
            record = {"revision": 1, "request_id": request_id, "task_id": task.id, "task_epoch": epoch,
                      "reserved_tokens": int(row["reserved_tokens"]), "state": "dispatch_committed",
                      "provider_called": True, "updated_at": timestamp}
            self._workspace_event(db, workspace_id=workspace_id, application_id=application_id,
                resource_type="model_usage", resource_id=request_id,
                event_type="investigation.model_dispatch_authorized", record=record,
                event_metadata={"run_id": run_id, "task_id": task_id, "task_epoch": epoch})
        return record

    def settle_investigation_model_usage(
        self, workspace_id: str, application_id: str, *, request_id: str,
        actual_tokens: int | None, provider_called: bool, error_code: str | None = None,
    ) -> dict[str, Any]:
        if (actual_tokens is not None and (type(actual_tokens) is not int or actual_tokens < 0)
                or type(provider_called) is not bool):
            raise ProductError("INVALID_MODEL_SETTLEMENT", "模型用量结算参数无效", 422)
        if error_code is not None:
            error_code = "".join(char for char in str(error_code) if char.isalnum() or char in "_.-")[:80] or "PROVIDER_ERROR"
        timestamp = _now()
        with self.tx() as db:
            row = db.execute(
                """SELECT * FROM investigation_model_usage WHERE request_id=? AND workspace_id=?
                   AND application_id=?""", (request_id, workspace_id, application_id),
            ).fetchone()
            if row is None:
                raise ProductError("MODEL_USAGE_NOT_FOUND", "模型用量记录不存在", 404)
            if row["state"] in {"settled", "outcome_unknown"}:
                return {"request_id": request_id, "state": row["state"],
                        "actual_tokens": row["actual_tokens"], "provider_called": bool(row["provider_called"]),
                        "error_code": row["error_code"]}
            if not provider_called and row["state"] != "reserved":
                raise ProductError("MODEL_SETTLEMENT_CONFLICT", "已准入供应商的调用不能结算为未调用", 409)
            if provider_called and row["state"] != "dispatch_committed":
                raise ProductError("MODEL_SETTLEMENT_CONFLICT", "供应商调用尚未获准", 409)
            state = "outcome_unknown" if provider_called and actual_tokens is None else "settled"
            settled_tokens = actual_tokens if provider_called else 0
            db.execute(
                """UPDATE investigation_model_usage SET actual_tokens=?,state=?,provider_called=?,error_code=?,updated_at=?
                   WHERE request_id=?""",
                (settled_tokens, state, int(provider_called), error_code, timestamp, request_id),
            )
            record = {"revision": 1, "request_id": request_id, "task_id": row["task_id"],
                      "task_epoch": int(row["task_epoch"]), "reserved_tokens": int(row["reserved_tokens"]),
                      "actual_tokens": settled_tokens, "state": state, "provider_called": provider_called,
                      "error_code": error_code, "updated_at": timestamp}
            self._workspace_event(db, workspace_id=workspace_id, application_id=application_id,
                resource_type="model_usage", resource_id=request_id,
                event_type="investigation.model_usage_settled" if state == "settled" else "investigation.model_usage_unknown",
                record=record,
                event_metadata={"run_id": row["run_id"], "task_id": row["task_id"],
                    "task_epoch": int(row["task_epoch"]), "reserved_tokens": int(row["reserved_tokens"]),
                    "actual_tokens": settled_tokens, "provider_called": provider_called,
                    "unknown_consumption_keeps_full_reservation": state == "outcome_unknown"})
        return record

    @staticmethod
    def _task_graph_detail_in_tx(db: sqlite3.Connection, graph_row: sqlite3.Row) -> dict[str, Any]:
        graph = json.loads(graph_row["data"])
        tasks = db.execute(
            """SELECT data FROM investigation_tasks WHERE graph_id=?
               ORDER BY created_at,id""",
            (graph_row["id"],),
        ).fetchall()
        graph.update({
            "revision": int(graph_row["revision"]),
            "updated_at": graph_row["updated_at"],
            "state": graph_row["state"],
            "graph_sha256": graph_row["graph_sha256"],
            "tasks": [json.loads(row["data"]) for row in tasks],
        })
        return TaskGraph.model_validate_json(json.dumps(graph)).model_dump(mode="json")

    _INVESTIGATION_WORKER_CAPABILITIES = frozenset({
        "evidence.investigate", "counterexample.prepare", "counterexample.review",
        "conclusion.synthesize",
    })

    def create_investigation_worker(
        self, workspace_id: str, application_id: str, environment_id: str,
        worker_id: str, capabilities: list[str],
    ) -> dict[str, Any]:
        worker_id = worker_id.strip()
        capabilities = sorted(set(capabilities))
        if (not worker_id or len(worker_id) > 200 or not environment_id
                or len(environment_id) > 200 or not capabilities
                or set(capabilities) - self._INVESTIGATION_WORKER_CAPABILITIES):
            raise ProductError("INVALID_INVESTIGATION_WORKER", "Worker 身份或能力范围无效", 422)
        token = "s9w_" + secrets.token_urlsafe(32)
        token_sha256 = hashlib.sha256(token.encode("utf-8")).hexdigest()
        timestamp = _now()
        with self.tx() as db:
            self._require_application(db, workspace_id, application_id)
            row = db.execute(
                """SELECT revision,state FROM investigation_workers WHERE workspace_id=?
                   AND application_id=? AND environment_id=? AND worker_id=?""",
                (workspace_id, application_id, environment_id, worker_id),
            ).fetchone()
            if row is not None and row["state"] == "active":
                raise ProductError("INVESTIGATION_WORKER_EXISTS", "同范围内已存在活动 Worker", 409)
            revision = int(row["revision"]) + 1 if row is not None else 1
            record = {
                "revision": revision, "worker_id": worker_id,
                "workspace_id": workspace_id, "application_id": application_id,
                "environment_id": environment_id, "capabilities": capabilities,
                "state": "active", "created_at": timestamp, "updated_at": timestamp,
            }
            if row is None:
                db.execute(
                    """INSERT INTO investigation_workers(
                       workspace_id,application_id,environment_id,worker_id,token_sha256,
                       revision,state,data,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (workspace_id, application_id, environment_id, worker_id, token_sha256,
                     revision, "active", _encode(record), timestamp, timestamp),
                )
            else:
                db.execute(
                    """UPDATE investigation_workers SET token_sha256=?,revision=?,state='active',
                       data=?,updated_at=? WHERE workspace_id=? AND application_id=?
                       AND environment_id=? AND worker_id=? AND state='revoked'""",
                    (token_sha256, revision, _encode(record), timestamp,
                     workspace_id, application_id, environment_id, worker_id),
                )
            self._workspace_event(db, workspace_id=workspace_id, application_id=application_id,
                resource_type="investigation_worker", resource_id=worker_id,
                event_type="investigation_worker.created" if revision == 1 else "investigation_worker.credential_rotated",
                record=record, event_metadata={"capabilities": capabilities})
        # This bearer secret is returned exactly once. Only its SHA-256 digest is stored.
        return {"worker": record, "token": token}

    def list_investigation_workers(
        self, workspace_id: str, application_id: str, environment_id: str,
    ) -> list[dict[str, Any]]:
        with self.tx() as db:
            self._require_application(db, workspace_id, application_id)
            rows = db.execute(
                """SELECT data FROM investigation_workers WHERE workspace_id=?
                   AND application_id=? AND environment_id=? ORDER BY created_at,worker_id""",
                (workspace_id, application_id, environment_id),
            ).fetchall()
        return [json.loads(row["data"]) for row in rows]

    def revoke_investigation_worker(
        self, workspace_id: str, application_id: str, environment_id: str, worker_id: str,
    ) -> dict[str, Any]:
        timestamp = _now()
        with self.tx() as db:
            self._require_application(db, workspace_id, application_id)
            row = db.execute(
                """SELECT data FROM investigation_workers WHERE workspace_id=?
                   AND application_id=? AND environment_id=? AND worker_id=?""",
                (workspace_id, application_id, environment_id, worker_id),
            ).fetchone()
            if row is None:
                raise ProductError("INVESTIGATION_WORKER_NOT_FOUND", "Worker 不存在", 404)
            record = json.loads(row["data"])
            if record["state"] == "active":
                record.update({"revision": int(record["revision"]) + 1,
                               "state": "revoked", "updated_at": timestamp})
                db.execute(
                    """UPDATE investigation_workers SET revision=?,state='revoked',data=?,updated_at=?
                       WHERE workspace_id=? AND application_id=? AND environment_id=? AND worker_id=?""",
                    (record["revision"], _encode(record), timestamp,
                     workspace_id, application_id, environment_id, worker_id),
                )
                self._workspace_event(db, workspace_id=workspace_id, application_id=application_id,
                    resource_type="investigation_worker", resource_id=worker_id,
                    event_type="investigation_worker.revoked", record=record)
        return record

    def authenticate_investigation_worker(
        self, token: str, workspace_id: str, application_id: str, environment_id: str,
    ) -> dict[str, Any]:
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        with self.tx() as db:
            row = db.execute(
                """SELECT workspace_id,application_id,environment_id,state,data
                   FROM investigation_workers WHERE token_sha256=?""", (digest,),
            ).fetchone()
        if row is None or row["state"] != "active":
            raise ProductError("WORKER_CREDENTIAL_INVALID", "Worker 凭据无效或已撤销", 401)
        if (row["workspace_id"], row["application_id"], row["environment_id"]) != (
                workspace_id, application_id, environment_id):
            raise ProductError("WORKER_SCOPE_MISMATCH", "Worker 凭据不属于请求的工作区、应用或环境", 403)
        return json.loads(row["data"])

    def recover_expired_investigation_task_leases(
        self, workspace_id: str, application_id: str, environment_id: str, run_id: str,
    ) -> dict[str, int]:
        """Recover only expired task leases; never resend an uncertain provider request."""
        with self.tx() as db:
            self._require_application(db, workspace_id, application_id)
            graph_row = db.execute(
                """SELECT g.* FROM investigation_task_graphs g JOIN investigation_runs r
                   ON r.id=g.run_id AND r.workspace_id=g.workspace_id
                     AND r.application_id=g.application_id AND r.environment_id=g.environment_id
                     AND r.incident_id=g.incident_id
                   WHERE g.workspace_id=? AND g.application_id=? AND g.environment_id=? AND g.run_id=?
                     AND r.state=?""",
                (workspace_id, application_id, environment_id, run_id, InvestigationRunState.RUNNING.value),
            ).fetchone()
            if graph_row is None:
                return {"reopened": 0, "stopped_for_provider_outcome": 0}
            rows = db.execute(
                """SELECT data,revision FROM investigation_tasks WHERE graph_id=?
                   AND state IN (?,?) ORDER BY created_at,id""",
                (graph_row["id"], TaskState.CLAIMED.value, TaskState.RUNNING.value),
            ).fetchall()
            now = datetime.now(timezone.utc)
            reopened = stopped = 0
            for row in rows:
                task = Task.model_validate_json(row["data"])
                deadline = (datetime.fromisoformat(task.lease_deadline.replace("Z", "+00:00"))
                            if task.lease_deadline else now)
                if deadline > now:
                    continue
                usage = db.execute(
                    """SELECT * FROM investigation_model_usage WHERE task_id=? AND task_epoch=?
                       AND workspace_id=? AND application_id=? AND environment_id=? AND run_id=?""",
                    (task.id, task.epoch, workspace_id, application_id, environment_id, run_id),
                ).fetchone()
                safe_to_reopen = usage is None or (
                    usage["state"] in {"reserved", "settled"} and not usage["provider_called"]
                )
                if usage is not None and usage["state"] == "reserved":
                    # Dispatch was never committed. Close this reservation as a known zero-use attempt.
                    db.execute(
                        """UPDATE investigation_model_usage SET actual_tokens=0,state='settled',provider_called=0,
                           error_code='PROCESS_INTERRUPTED_BEFORE_DISPATCH',updated_at=? WHERE request_id=? AND state='reserved'""",
                        (_now(), usage["request_id"]),
                    )
                    usage_record = {"revision": 1, "request_id": usage["request_id"],
                        "task_id": task.id, "task_epoch": task.epoch,
                        "reserved_tokens": int(usage["reserved_tokens"]), "actual_tokens": 0,
                        "state": "settled", "provider_called": False,
                        "error_code": "PROCESS_INTERRUPTED_BEFORE_DISPATCH", "updated_at": _now()}
                    self._workspace_event(db, workspace_id=workspace_id, application_id=application_id,
                        resource_type="model_usage", resource_id=usage["request_id"],
                        event_type="investigation.model_usage_settled", record=usage_record,
                        event_metadata={"run_id": run_id, "task_id": task.id,
                            "task_epoch": task.epoch, "provider_called": False})
                elif usage is not None and usage["state"] == "dispatch_committed":
                    # The provider may have accepted the request. Keep the full reservation as unknown.
                    db.execute(
                        """UPDATE investigation_model_usage SET state='outcome_unknown',provider_called=1,
                           error_code='PROCESS_INTERRUPTED_AFTER_DISPATCH',updated_at=?
                           WHERE request_id=? AND state='dispatch_committed'""",
                        (_now(), usage["request_id"]),
                    )
                    usage_record = {"revision": 1, "request_id": usage["request_id"],
                        "task_id": task.id, "task_epoch": task.epoch,
                        "reserved_tokens": int(usage["reserved_tokens"]), "actual_tokens": None,
                        "state": "outcome_unknown", "provider_called": True,
                        "error_code": "PROCESS_INTERRUPTED_AFTER_DISPATCH", "updated_at": _now()}
                    self._workspace_event(db, workspace_id=workspace_id, application_id=application_id,
                        resource_type="model_usage", resource_id=usage["request_id"],
                        event_type="investigation.model_usage_unknown", record=usage_record,
                        event_metadata={"run_id": run_id, "task_id": task.id,
                            "task_epoch": task.epoch, "reserved_tokens": int(usage["reserved_tokens"]),
                            "unknown_consumption_keeps_full_reservation": True})

                if safe_to_reopen:
                    reason = "worker lease expired before provider dispatch; safe to resume"
                    next_state = TaskState.OPEN
                    failure_reason = None
                    reopened += 1
                else:
                    if usage is not None and usage["state"] == "settled" and usage["actual_tokens"] is not None:
                        reason = ("provider usage was settled before process interruption, but the task result was "
                                  "not persisted; explicit retry will make another provider request")
                    else:
                        reason = ("provider outcome is unknown after process interruption; full reservation is "
                                  "retained and automatic retry is stopped")
                    next_state = TaskState.FAILED
                    failure_reason = reason
                    stopped += 1
                timestamp = _now()
                history = list(task.attempt_history)
                if not history or history[-1].epoch != task.epoch:
                    history.append(TaskAttemptFailure(epoch=task.epoch,
                        holder_id=task.holder_id or "unknown", reason=reason, failed_at=timestamp))
                updated = task.model_copy(update={
                    "revision": int(row["revision"]) + 1, "updated_at": timestamp,
                    "state": next_state, "holder_id": None, "lease_deadline": None,
                    "result": None, "failure_reason": failure_reason, "attempt_history": history,
                })
                record = updated.model_dump(mode="json")
                db.execute(
                    """UPDATE investigation_tasks SET revision=?,state=?,holder_id=NULL,lease_deadline=NULL,
                       data=?,updated_at=? WHERE id=? AND revision=?""",
                    (record["revision"], record["state"], _encode(record), timestamp, task.id, row["revision"]),
                )
                self._workspace_event(db, workspace_id=workspace_id, application_id=application_id,
                    resource_type="investigation_task", resource_id=task.id,
                    event_type="investigation_task.recovered" if safe_to_reopen else "investigation_task.interrupted_unknown",
                    record=record, event_metadata={"previous_epoch": task.epoch,
                        "provider_request_state": usage["state"] if usage is not None else "not_started",
                        "automatic_provider_retry": False})
            self._refresh_task_graph_state_in_tx(db, graph_row,
                workspace_id=workspace_id, application_id=application_id)
        return {"reopened": reopened, "stopped_for_provider_outcome": stopped}

    @staticmethod
    def _refresh_task_graph_state_in_tx(
        db: sqlite3.Connection, graph_row: sqlite3.Row, *, workspace_id: str, application_id: str,
    ) -> dict[str, Any]:
        states = [row["state"] for row in db.execute(
            "SELECT state FROM investigation_tasks WHERE graph_id=?", (graph_row["id"],),
        ).fetchall()]
        if states and all(state == TaskState.SUCCEEDED.value for state in states):
            new_state = "complete"
        elif TaskState.FAILED.value in states or TaskState.BLOCKED.value in states:
            new_state = "blocked"
        elif any(state in {TaskState.CLAIMED.value, TaskState.RUNNING.value, TaskState.SUCCEEDED.value}
                 for state in states):
            new_state = "running"
        else:
            new_state = "ready"
        if new_state != graph_row["state"]:
            current = json.loads(graph_row["data"])
            timestamp = _now()
            current.update({"revision": int(graph_row["revision"]) + 1,
                            "state": new_state, "updated_at": timestamp})
            db.execute(
                """UPDATE investigation_task_graphs SET revision=?,state=?,data=?,updated_at=?
                   WHERE id=? AND revision=?""",
                (current["revision"], new_state, _encode(current), timestamp,
                 graph_row["id"], graph_row["revision"]),
            )
            refreshed = dict(graph_row)
            refreshed.update({"revision": current["revision"], "state": new_state,
                              "data": _encode(current), "updated_at": timestamp})
            graph_record = ProductRegistry._task_graph_detail_in_tx(db, refreshed)
            ProductRegistry._workspace_event(
                db, workspace_id=workspace_id, application_id=application_id,
                resource_type="investigation_task_graph", resource_id=graph_row["id"],
                event_type="investigation_task_graph.state_changed", record=graph_record,
                event_metadata={"from_state": graph_row["state"], "to_state": new_state},
            )
            return graph_record
        return ProductRegistry._task_graph_detail_in_tx(db, graph_row)

    def claim_investigation_task(
        self, workspace_id: str, application_id: str, environment_id: str, run_id: str,
        task_id: str, worker_id: str, capabilities: list[str], *, expected_revision: int,
        lease_seconds: int, idempotency_key: str,
    ) -> dict[str, Any]:
        worker_id = worker_id.strip()
        capabilities = sorted(set(capabilities))
        if not worker_id or len(worker_id) > 200 or not 10 <= lease_seconds <= 300:
            raise ProductError("INVALID_TASK_CLAIM", "Worker 身份或租约时长无效", 422)
        payload = {"workspace_id": workspace_id, "application_id": application_id,
            "environment_id": environment_id, "run_id": run_id, "task_id": task_id,
            "worker_id": worker_id, "capabilities": capabilities,
            "expected_revision": expected_revision, "lease_seconds": lease_seconds}
        with self.tx() as db:
            self._require_application(db, workspace_id, application_id)
            replay = self._replay_command(db, command_name="investigation_task.claim",
                scope_key=workspace_id, idempotency_key=idempotency_key, payload=payload)
            if replay is not None:
                return replay
            row = db.execute(
                """SELECT t.data,t.revision,t.state,t.epoch,t.lease_deadline,t.graph_id,r.state AS run_state
                   FROM investigation_tasks t JOIN investigation_runs r
                     ON r.id=t.run_id AND r.workspace_id=t.workspace_id
                    AND r.application_id=t.application_id AND r.environment_id=t.environment_id
                    AND r.incident_id=t.incident_id
                   WHERE t.id=? AND t.workspace_id=? AND t.application_id=?
                     AND t.environment_id=? AND t.run_id=?""",
                (task_id, workspace_id, application_id, environment_id, run_id),
            ).fetchone()
            if row is None:
                raise ProductError("INVESTIGATION_TASK_NOT_FOUND", "任务不属于该调查或不存在", 404)
            if int(row["revision"]) != expected_revision:
                raise ProductError("STALE_TASK_REVISION", "任务已变化，请刷新后重试", 409)
            if row["run_state"] != InvestigationRunState.RUNNING.value:
                raise ProductError("INVESTIGATION_RUN_TERMINAL", "终态调查中的任务不可领取", 409)
            task = Task.model_validate_json(row["data"])
            if task.capability not in capabilities:
                raise ProductError("TASK_CAPABILITY_REQUIRED", "Worker 未声明该任务所需能力", 403)
            now = datetime.now(timezone.utc)
            history = list(task.attempt_history)
            if task.state == TaskState.CLAIMED:
                deadline = datetime.fromisoformat(task.lease_deadline.replace("Z", "+00:00")) if task.lease_deadline else now
                if deadline > now:
                    raise ProductError("TASK_LEASE_ACTIVE", "任务租约仍由其他 Worker 持有", 409)
                if task.holder_id and task.epoch > 0:
                    history.append(TaskAttemptFailure(epoch=task.epoch, holder_id=task.holder_id,
                        reason="worker lease expired before completion", failed_at=_now()))
            elif task.state != TaskState.OPEN:
                raise ProductError("TASK_NOT_CLAIMABLE", "任务当前状态不可领取", 409)
            if task.dependencies:
                marks = ",".join("?" for _ in task.dependencies)
                rows = db.execute(
                    f"SELECT id,state FROM investigation_tasks WHERE id IN ({marks}) AND graph_id=?",
                    (*task.dependencies, row["graph_id"]),
                ).fetchall()
                if len(rows) != len(task.dependencies) or any(item["state"] != TaskState.SUCCEEDED.value for item in rows):
                    raise ProductError("TASK_DEPENDENCIES_PENDING", "前置任务尚未全部成功", 409)
            timestamp = _now()
            updated = task.model_copy(update={
                "revision": expected_revision + 1, "updated_at": timestamp,
                "epoch": task.epoch + 1, "holder_id": worker_id,
                "lease_deadline": (datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)).isoformat(timespec="milliseconds"),
                "state": TaskState.CLAIMED, "failure_reason": None,
                "attempt_history": history,
            })
            record = updated.model_dump(mode="json")
            db.execute(
                """UPDATE investigation_tasks SET revision=?,state=?,epoch=?,holder_id=?,lease_deadline=?,
                   data=?,updated_at=? WHERE id=? AND revision=?""",
                (record["revision"], record["state"], record["epoch"], worker_id,
                 record["lease_deadline"], _encode(record), timestamp, task_id, expected_revision),
            )
            self._workspace_event(db, workspace_id=workspace_id, application_id=application_id,
                resource_type="investigation_task", resource_id=task_id, event_type="investigation_task.claimed",
                record=record, event_metadata={"worker_id": worker_id, "epoch": record["epoch"],
                    "capability": task.capability, "lease_seconds": lease_seconds})
            graph_row = db.execute("SELECT * FROM investigation_task_graphs WHERE id=?", (row["graph_id"],)).fetchone()
            self._refresh_task_graph_state_in_tx(db, graph_row, workspace_id=workspace_id,
                application_id=application_id)
            self._save_command_receipt(db, command_name="investigation_task.claim", scope_key=workspace_id,
                idempotency_key=idempotency_key, payload=payload, response=record)
        return record

    def get_investigation_task_context(
        self, workspace_id: str, application_id: str, environment_id: str, run_id: str,
        task_id: str, worker_id: str, epoch: int,
    ) -> dict[str, Any] | None:
        with self.tx() as db:
            row = db.execute(
                """SELECT t.data,t.graph_id,r.snapshot_json FROM investigation_tasks t
                   JOIN investigation_runs r ON r.id=t.run_id AND r.workspace_id=t.workspace_id
                    AND r.application_id=t.application_id AND r.environment_id=t.environment_id
                    AND r.incident_id=t.incident_id
                   WHERE t.id=? AND t.workspace_id=? AND t.application_id=?
                     AND t.environment_id=? AND t.run_id=?""",
                (task_id, workspace_id, application_id, environment_id, run_id),
            ).fetchone()
            if row is None:
                return None
            task = Task.model_validate_json(row["data"])
            if task.state not in {TaskState.CLAIMED, TaskState.RUNNING} or task.holder_id != worker_id or task.epoch != epoch:
                raise ProductError("TASK_LEASE_FENCED", "Worker 不持有此任务的当前租约", 409)
            if not task.lease_deadline or datetime.fromisoformat(task.lease_deadline.replace("Z", "+00:00")) <= datetime.now(timezone.utc):
                raise ProductError("TASK_LEASE_EXPIRED", "任务租约已过期，请重新领取", 409)
            evidence: list[dict[str, Any]] = []
            snapshot = json.loads(row["snapshot_json"])
            signals_by_id = {str(item["id"]): item for item in snapshot.get("signals", []) if item.get("id")}
            if task.input_evidence_ids:
                marks = ",".join("?" for _ in task.input_evidence_ids)
                rows = db.execute(
                    f"""SELECT id,data FROM investigation_evidence WHERE id IN ({marks})
                         AND workspace_id=? AND application_id=? AND environment_id=? AND run_id=?""",
                    (*task.input_evidence_ids, workspace_id, application_id, environment_id, run_id),
                ).fetchall()
                by_id = {item["id"]: json.loads(item["data"]) for item in rows}
                if set(by_id) != set(task.input_evidence_ids):
                    raise ProductError("EVIDENCE_NOT_FOUND", "任务输入证据不完整", 404)
                for evidence_id in task.input_evidence_ids:
                    item = by_id[evidence_id]
                    signal_id = str(item.get("origin", "")).rsplit("/", 1)[-1]
                    item["source_record"] = signals_by_id.get(signal_id)
                    evidence.append(item)
            dependency_results = []
            if task.dependencies:
                marks = ",".join("?" for _ in task.dependencies)
                rows = db.execute(
                    f"SELECT id,data,state FROM investigation_tasks WHERE id IN ({marks}) AND graph_id=?",
                    (*task.dependencies, row["graph_id"]),
                ).fetchall()
                for item in rows:
                    dependency = Task.model_validate_json(item["data"])
                    if item["state"] != TaskState.SUCCEEDED.value or dependency.result is None:
                        raise ProductError("TASK_DEPENDENCIES_PENDING", "前置任务尚未提供成功结果", 409)
                    dependency_results.append({"task_id": dependency.id, "task_key": dependency.task_key,
                        "role": dependency.role, "result": dependency.result.model_dump(mode="json")})
                dependency_results.sort(key=lambda item: item["task_key"])
            scoped_signals = [item["source_record"] for item in evidence
                if isinstance(item.get("source_record"), dict)]
            incident_context = dict(snapshot.get("incident") or {})
            incident_context["signal_ids"] = [str(item["id"]) for item in scoped_signals if item.get("id")]
            return {
                "task": task.model_dump(mode="json"),
                "input_snapshot": {
                    "schema_version": snapshot.get("schema_version"),
                    "captured_at": snapshot.get("captured_at"),
                    "incident": incident_context,
                    "signals": scoped_signals,
                },
                "input_evidence": evidence,
                "dependency_results": dependency_results,
            }

    def renew_investigation_task_lease(
        self, workspace_id: str, application_id: str, environment_id: str, run_id: str,
        task_id: str, worker_id: str, epoch: int, *, expected_revision: int,
        lease_seconds: int, idempotency_key: str,
    ) -> dict[str, Any]:
        if not worker_id.strip() or not 10 <= lease_seconds <= 300:
            raise ProductError("INVALID_TASK_LEASE", "Worker 身份或租约时长无效", 422)
        payload = {"workspace_id": workspace_id, "application_id": application_id,
            "environment_id": environment_id, "run_id": run_id, "task_id": task_id,
            "worker_id": worker_id, "epoch": epoch, "expected_revision": expected_revision,
            "lease_seconds": lease_seconds}
        with self.tx() as db:
            self._require_application(db, workspace_id, application_id)
            replay = self._replay_command(db, command_name="investigation_task.renew",
                scope_key=workspace_id, idempotency_key=idempotency_key, payload=payload)
            if replay is not None:
                return replay
            row = db.execute(
                """SELECT t.data,t.revision,t.graph_id,r.state AS run_state FROM investigation_tasks t
                   JOIN investigation_runs r ON r.id=t.run_id AND r.workspace_id=t.workspace_id
                    AND r.application_id=t.application_id AND r.environment_id=t.environment_id
                    AND r.incident_id=t.incident_id
                   WHERE t.id=? AND t.workspace_id=? AND t.application_id=?
                     AND t.environment_id=? AND t.run_id=?""",
                (task_id, workspace_id, application_id, environment_id, run_id),
            ).fetchone()
            if row is None:
                raise ProductError("INVESTIGATION_TASK_NOT_FOUND", "任务不属于该调查或不存在", 404)
            if int(row["revision"]) != expected_revision:
                raise ProductError("STALE_TASK_REVISION", "任务已变化，请刷新后重试", 409)
            if row["run_state"] != InvestigationRunState.RUNNING.value:
                raise ProductError("INVESTIGATION_RUN_TERMINAL", "终态调查中的租约不可续期", 409)
            task = Task.model_validate_json(row["data"])
            if task.state not in {TaskState.CLAIMED, TaskState.RUNNING} or task.holder_id != worker_id or task.epoch != epoch:
                raise ProductError("TASK_LEASE_FENCED", "Worker 不持有此任务的当前租约", 409)
            now = datetime.now(timezone.utc)
            if not task.lease_deadline or datetime.fromisoformat(task.lease_deadline.replace("Z", "+00:00")) <= now:
                raise ProductError("TASK_LEASE_EXPIRED", "已过期租约不可续期，需重新领取", 409)
            timestamp = _now()
            updated = task.model_copy(update={
                "revision": expected_revision + 1, "updated_at": timestamp,
                "lease_deadline": (now + timedelta(seconds=lease_seconds)).isoformat(timespec="milliseconds"),
            })
            record = updated.model_dump(mode="json")
            db.execute(
                """UPDATE investigation_tasks SET revision=?,lease_deadline=?,data=?,updated_at=?
                   WHERE id=? AND revision=?""",
                (record["revision"], record["lease_deadline"], _encode(record), timestamp, task_id, expected_revision),
            )
            self._workspace_event(db, workspace_id=workspace_id, application_id=application_id,
                resource_type="investigation_task", resource_id=task_id,
                event_type="investigation_task.lease_renewed", record=record,
                event_metadata={"worker_id": worker_id, "epoch": epoch, "lease_seconds": lease_seconds})
            self._save_command_receipt(db, command_name="investigation_task.renew", scope_key=workspace_id,
                idempotency_key=idempotency_key, payload=payload, response=record)
        return record

    def release_investigation_task_claim(
        self, workspace_id: str, application_id: str, environment_id: str, run_id: str,
        task_id: str, worker_id: str, epoch: int, *, expected_revision: int, reason: str,
    ) -> dict[str, Any]:
        """Return a pre-dispatch task to the open queue without recording a failed attempt."""
        with self.tx() as db:
            row = db.execute(
                """SELECT t.data,t.revision,t.graph_id,r.state AS run_state FROM investigation_tasks t
                   JOIN investigation_runs r ON r.id=t.run_id AND r.workspace_id=t.workspace_id
                    AND r.application_id=t.application_id AND r.environment_id=t.environment_id
                    AND r.incident_id=t.incident_id
                   WHERE t.id=? AND t.workspace_id=? AND t.application_id=?
                     AND t.environment_id=? AND t.run_id=?""",
                (task_id, workspace_id, application_id, environment_id, run_id),
            ).fetchone()
            if row is None:
                raise ProductError("INVESTIGATION_TASK_NOT_FOUND", "任务不属于该调查或不存在", 404)
            if int(row["revision"]) != expected_revision or row["run_state"] != InvestigationRunState.RUNNING.value:
                raise ProductError("TASK_REVISION_OR_RUN_CHANGED", "任务或调查已变化，不能释放认领", 409)
            task = Task.model_validate_json(row["data"])
            if task.state not in {TaskState.CLAIMED, TaskState.RUNNING} or task.holder_id != worker_id or task.epoch != epoch:
                raise ProductError("TASK_LEASE_FENCED", "Worker 不持有此任务的当前租约", 409)
            if (not task.lease_deadline or
                    datetime.fromisoformat(task.lease_deadline.replace("Z", "+00:00")) <= datetime.now(timezone.utc)):
                raise ProductError("TASK_LEASE_EXPIRED", "已过期租约不能释放", 409)
            timestamp = _now()
            updated = task.model_copy(update={
                "revision": expected_revision + 1, "updated_at": timestamp,
                "state": TaskState.OPEN, "holder_id": None, "lease_deadline": None,
                "failure_reason": None,
            })
            record = updated.model_dump(mode="json")
            db.execute(
                """UPDATE investigation_tasks SET revision=?,state=?,holder_id=NULL,lease_deadline=NULL,
                   data=?,updated_at=? WHERE id=? AND revision=?""",
                (record["revision"], TaskState.OPEN.value, _encode(record), timestamp, task_id, expected_revision),
            )
            self._workspace_event(db, workspace_id=workspace_id, application_id=application_id,
                resource_type="investigation_task", resource_id=task_id,
                event_type="investigation_task.claim_released", record=record,
                event_metadata={"worker_id": worker_id, "epoch": epoch,
                    "reason_code": "".join(ch for ch in reason if ch.isalnum() or ch in "_.-")[:80]})
            graph_row = db.execute("SELECT * FROM investigation_task_graphs WHERE id=?", (row["graph_id"],)).fetchone()
            graph = self._refresh_task_graph_state_in_tx(db, graph_row,
                workspace_id=workspace_id, application_id=application_id)
        return {"task": record, "task_graph": graph}

    def finish_investigation_task(
        self, workspace_id: str, application_id: str, environment_id: str, run_id: str,
        task_id: str, worker_id: str, epoch: int, result: dict[str, Any] | None,
        failure_reason: str | None, *, expected_revision: int, idempotency_key: str,
    ) -> dict[str, Any]:
        if (result is None) == (failure_reason is None):
            raise ProductError("INVALID_TASK_RESULT", "必须且只能提交成功产物或失败原因", 422)
        if failure_reason is not None:
            failure_reason = failure_reason.strip()
            if not failure_reason or len(failure_reason) > 1000:
                raise ProductError("INVALID_TASK_RESULT", "失败原因无效", 422)
        payload = {"workspace_id": workspace_id, "application_id": application_id,
            "environment_id": environment_id, "run_id": run_id, "task_id": task_id,
            "worker_id": worker_id, "epoch": epoch, "expected_revision": expected_revision,
            "result": result, "failure_reason": failure_reason}
        with self.tx() as db:
            self._require_application(db, workspace_id, application_id)
            replay = self._replay_command(db, command_name="investigation_task.finish",
                scope_key=workspace_id, idempotency_key=idempotency_key, payload=payload)
            if replay is not None:
                return replay
            row = db.execute(
                """SELECT t.data,t.revision,t.graph_id,r.state AS run_state FROM investigation_tasks t
                   JOIN investigation_runs r ON r.id=t.run_id AND r.workspace_id=t.workspace_id
                    AND r.application_id=t.application_id AND r.environment_id=t.environment_id
                    AND r.incident_id=t.incident_id
                   WHERE t.id=? AND t.workspace_id=? AND t.application_id=?
                     AND t.environment_id=? AND t.run_id=?""",
                (task_id, workspace_id, application_id, environment_id, run_id),
            ).fetchone()
            if row is None:
                raise ProductError("INVESTIGATION_TASK_NOT_FOUND", "任务不属于该调查或不存在", 404)
            if int(row["revision"]) != expected_revision:
                raise ProductError("STALE_TASK_REVISION", "任务已变化，请刷新后重试", 409)
            if row["run_state"] != InvestigationRunState.RUNNING.value:
                raise ProductError("INVESTIGATION_RUN_TERMINAL", "终态调查中的任务不可完成", 409)
            task = Task.model_validate_json(row["data"])
            if task.state not in {TaskState.CLAIMED, TaskState.RUNNING} or task.holder_id != worker_id or task.epoch != epoch:
                raise ProductError("TASK_LEASE_FENCED", "Worker 不持有此任务的当前租约", 409)
            if not task.lease_deadline or datetime.fromisoformat(task.lease_deadline.replace("Z", "+00:00")) <= datetime.now(timezone.utc):
                raise ProductError("TASK_LEASE_EXPIRED", "任务租约已过期，不接受迟到的结果", 409)
            if result is not None:
                try:
                    result_model = TaskResult.model_validate(result)
                except ValueError as exc:
                    raise ProductError("INVALID_TASK_RESULT", "任务产物不符合结构化契约", 422) from exc
                self._validate_task_result_for_role(task.role, result_model)
                references = set(result_model.evidence_ids)
                for hypothesis in result_model.hypotheses:
                    references.update(hypothesis.support_evidence_ids)
                    references.update(hypothesis.counterevidence_ids)
                for question in result_model.challenge_questions:
                    references.update(question.evidence_ids)
                allowed_references = set(task.input_evidence_ids)
                if task.dependencies:
                    dep_marks = ",".join("?" for _ in task.dependencies)
                    dep_rows = db.execute(
                        f"SELECT data,state FROM investigation_tasks WHERE id IN ({dep_marks}) AND graph_id=?",
                        (*task.dependencies, row["graph_id"]),
                    ).fetchall()
                    if len(dep_rows) != len(task.dependencies) or any(item["state"] != TaskState.SUCCEEDED.value for item in dep_rows):
                        raise ProductError("TASK_DEPENDENCIES_PENDING", "完成任务前，前置任务必须全部成功", 409)
                    for dependency_row in dep_rows:
                        dependency = Task.model_validate_json(dependency_row["data"])
                        if dependency.result is not None:
                            allowed_references.update(dependency.result.evidence_ids)
                            for hypothesis in dependency.result.hypotheses:
                                allowed_references.update(hypothesis.support_evidence_ids)
                                allowed_references.update(hypothesis.counterevidence_ids)
                            for question in dependency.result.challenge_questions:
                                allowed_references.update(question.evidence_ids)
                if not references.issubset(allowed_references):
                    raise ProductError("TASK_EVIDENCE_SCOPE", "任务产物引用了当前任务上下文之外的证据", 403)
                if references:
                    marks = ",".join("?" for _ in references)
                    found = db.execute(
                        f"""SELECT id FROM investigation_evidence WHERE id IN ({marks})
                             AND workspace_id=? AND application_id=? AND environment_id=?
                             AND incident_id=? AND run_id=?""",
                        (*sorted(references), workspace_id, application_id, environment_id,
                         task.incident_id, run_id),
                    ).fetchall()
                    if {item["id"] for item in found} != references:
                        raise ProductError("EVIDENCE_NOT_FOUND", "任务产物只能引用同一调查运行中的证据", 404)
            else:
                result_model = None
            timestamp = _now()
            updated = task.model_copy(update={
                "revision": expected_revision + 1, "updated_at": timestamp,
                "state": TaskState.FAILED if failure_reason is not None else TaskState.SUCCEEDED,
                "result": result_model, "failure_reason": failure_reason,
                "lease_deadline": None,
            })
            record = updated.model_dump(mode="json")
            db.execute(
                """UPDATE investigation_tasks SET revision=?,state=?,lease_deadline=?,data=?,updated_at=?
                   WHERE id=? AND revision=?""",
                (record["revision"], record["state"], None, _encode(record), timestamp, task_id, expected_revision),
            )
            if result_model is not None:
                for finding in result_model.hypotheses:
                    hypothesis_id = _uid("hyp")
                    hypothesis = Hypothesis(
                        id=hypothesis_id, workspace_id=workspace_id, application_id=application_id,
                        environment_id=environment_id, incident_id=task.incident_id, run_id=run_id,
                        revision=1, created_at=timestamp, updated_at=timestamp,
                        statement=finding.statement, confidence=finding.confidence,
                        support_evidence_ids=finding.support_evidence_ids,
                        counterevidence_ids=finding.counterevidence_ids,
                        state=HypothesisState.PROPOSED, source_task_id=task.id, source_task_epoch=epoch,
                    ).model_dump(mode="json")
                    db.execute(
                        """INSERT INTO investigation_hypotheses(
                            id,workspace_id,application_id,environment_id,incident_id,run_id,revision,data,created_at,updated_at
                        ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                        (hypothesis_id, workspace_id, application_id, environment_id,
                         task.incident_id, run_id, 1, _encode(hypothesis), timestamp, timestamp),
                    )
                    self._workspace_event(db, workspace_id=workspace_id, application_id=application_id,
                        resource_type="hypothesis", resource_id=hypothesis_id,
                        event_type="hypothesis.proposed_by_task", record=hypothesis,
                        event_metadata={"task_id": task.id, "task_epoch": epoch, "task_role": task.role})
            self._workspace_event(db, workspace_id=workspace_id, application_id=application_id,
                resource_type="investigation_task", resource_id=task_id,
                event_type="investigation_task.failed" if failure_reason is not None else "investigation_task.succeeded",
                record=record, event_metadata={"worker_id": worker_id, "epoch": epoch,
                    "failure_reason": failure_reason})
            graph_row = db.execute("SELECT * FROM investigation_task_graphs WHERE id=?", (row["graph_id"],)).fetchone()
            graph_record = self._refresh_task_graph_state_in_tx(db, graph_row,
                workspace_id=workspace_id, application_id=application_id)
            response = {"task": record, "task_graph": graph_record}
            self._save_command_receipt(db, command_name="investigation_task.finish", scope_key=workspace_id,
                idempotency_key=idempotency_key, payload=payload, response=response)
        return response

    @staticmethod
    def _validate_task_result_for_role(role: str, result: TaskResult) -> None:
        if role == "investigator":
            valid = bool(result.hypotheses) and not result.challenge_questions and result.review_verdict is None and result.conclusion is None
        elif role == "challenger_seed":
            valid = bool(result.challenge_questions) and not result.hypotheses and result.review_verdict is None and result.conclusion is None
        elif role == "challenger_review":
            valid = result.review_verdict is not None and not result.challenge_questions and result.conclusion is None
            if result.review_verdict == "challenges":
                cited = bool(result.evidence_ids) or any(
                    hypothesis.support_evidence_ids or hypothesis.counterevidence_ids
                    for hypothesis in result.hypotheses
                )
                valid = valid and cited
        else:
            valid = result.conclusion is not None and not result.hypotheses and not result.challenge_questions and result.review_verdict is None
        if not valid:
            raise ProductError("TASK_RESULT_ROLE_MISMATCH", "任务产物类型与任务角色不匹配", 422)

    def retry_investigation_task(
        self, workspace_id: str, application_id: str, environment_id: str, run_id: str,
        task_id: str, reason: str, *, expected_revision: int, idempotency_key: str,
    ) -> dict[str, Any]:
        reason = reason.strip()
        if not reason or len(reason) > 1000:
            raise ProductError("INVALID_TASK_RETRY", "重试理由无效", 422)
        payload = {"workspace_id": workspace_id, "application_id": application_id,
            "environment_id": environment_id, "run_id": run_id, "task_id": task_id,
            "reason": reason, "expected_revision": expected_revision}
        with self.tx() as db:
            self._require_application(db, workspace_id, application_id)
            replay = self._replay_command(db, command_name="investigation_task.retry",
                scope_key=workspace_id, idempotency_key=idempotency_key, payload=payload)
            if replay is not None:
                return replay
            row = db.execute(
                """SELECT t.data,t.revision,t.graph_id,r.state AS run_state FROM investigation_tasks t
                   JOIN investigation_runs r ON r.id=t.run_id AND r.workspace_id=t.workspace_id
                    AND r.application_id=t.application_id AND r.environment_id=t.environment_id
                    AND r.incident_id=t.incident_id
                   WHERE t.id=? AND t.workspace_id=? AND t.application_id=?
                     AND t.environment_id=? AND t.run_id=?""",
                (task_id, workspace_id, application_id, environment_id, run_id),
            ).fetchone()
            if row is None:
                raise ProductError("INVESTIGATION_TASK_NOT_FOUND", "任务不属于该调查或不存在", 404)
            if int(row["revision"]) != expected_revision:
                raise ProductError("STALE_TASK_REVISION", "任务已变化，请刷新后重试", 409)
            if row["run_state"] != InvestigationRunState.RUNNING.value:
                raise ProductError("INVESTIGATION_RUN_TERMINAL", "终态调查中的任务不可重试", 409)
            task = Task.model_validate_json(row["data"])
            if task.state != TaskState.FAILED:
                raise ProductError("TASK_NOT_FAILED", "只有失败任务可以显式重试", 409)
            timestamp = _now()
            history = list(task.attempt_history)
            if not history or history[-1].epoch != task.epoch:
                history.append(TaskAttemptFailure(epoch=task.epoch, holder_id=task.holder_id or "unknown",
                    reason=task.failure_reason or reason, failed_at=timestamp))
            updated = task.model_copy(update={
                "revision": expected_revision + 1, "updated_at": timestamp,
                "state": TaskState.OPEN, "holder_id": None, "lease_deadline": None,
                "result": None, "failure_reason": None, "attempt_history": history,
            })
            record = updated.model_dump(mode="json")
            db.execute(
                """UPDATE investigation_tasks SET revision=?,state=?,holder_id=NULL,lease_deadline=NULL,
                   data=?,updated_at=? WHERE id=? AND revision=?""",
                (record["revision"], record["state"], _encode(record), timestamp, task_id, expected_revision),
            )
            self._workspace_event(db, workspace_id=workspace_id, application_id=application_id,
                resource_type="investigation_task", resource_id=task_id,
                event_type="investigation_task.retried", record=record,
                event_metadata={"reason": reason, "previous_epoch": task.epoch})
            graph_row = db.execute("SELECT * FROM investigation_task_graphs WHERE id=?", (row["graph_id"],)).fetchone()
            graph_record = self._refresh_task_graph_state_in_tx(db, graph_row,
                workspace_id=workspace_id, application_id=application_id)
            response = {"task": record, "task_graph": graph_record}
            self._save_command_receipt(db, command_name="investigation_task.retry", scope_key=workspace_id,
                idempotency_key=idempotency_key, payload=payload, response=response)
        return response

    def create_hypothesis(
        self, workspace_id: str, application_id: str, environment_id: str, run_id: str,
        statement: str, confidence: float, support_evidence_ids: list[str],
        counterevidence_ids: list[str], *, expected_run_revision: int, idempotency_key: str,
    ) -> dict[str, Any]:
        statement = statement.strip()
        if not statement or len(statement) > 4000 or not 0 <= confidence <= 1:
            raise ProductError("INVALID_HYPOTHESIS", "假设内容或置信度无效", 422)
        all_refs = support_evidence_ids + counterevidence_ids
        if len(set(all_refs)) != len(all_refs):
            raise ProductError("INVALID_HYPOTHESIS_EVIDENCE", "支持证据与反证 ID 必须唯一且不重复", 422)
        payload = {
            "workspace_id": workspace_id, "application_id": application_id,
            "environment_id": environment_id, "run_id": run_id,
            "expected_run_revision": expected_run_revision, "statement": statement,
            "confidence": confidence, "support_evidence_ids": support_evidence_ids,
            "counterevidence_ids": counterevidence_ids,
        }
        hypothesis_id = _uid("hyp")
        timestamp = _now()
        with self.tx() as db:
            self._require_application(db, workspace_id, application_id)
            replay = self._replay_command(db, command_name="hypothesis.create", scope_key=workspace_id,
                idempotency_key=idempotency_key, payload=payload)
            if replay is not None:
                return replay
            run_row = db.execute(
                """SELECT revision,state,incident_id FROM investigation_runs WHERE id=? AND workspace_id=?
                   AND application_id=? AND environment_id=?""",
                (run_id, workspace_id, application_id, environment_id),
            ).fetchone()
            if run_row is None:
                raise ProductError("INVESTIGATION_RUN_NOT_FOUND", "调查运行不属于该应用和环境或不存在", 404)
            if int(run_row["revision"]) != expected_run_revision:
                raise ProductError("STALE_INVESTIGATION_REVISION", "调查运行已变化，请刷新后重试", 409)
            if run_row["state"] != InvestigationRunState.RUNNING.value:
                raise ProductError("INVESTIGATION_RUN_TERMINAL", "终态调查运行不可新增假设", 409)
            if all_refs:
                placeholders = ",".join("?" for _ in all_refs)
                rows = db.execute(
                    f"""SELECT id FROM investigation_evidence WHERE id IN ({placeholders})
                        AND workspace_id=? AND application_id=? AND environment_id=?
                        AND incident_id=? AND run_id=?""",
                    (*all_refs, workspace_id, application_id, environment_id, run_row["incident_id"], run_id),
                ).fetchall()
                if {row["id"] for row in rows} != set(all_refs):
                    raise ProductError("EVIDENCE_NOT_FOUND", "假设只能引用同一调查运行中的证据", 404)
            try:
                hypothesis = Hypothesis(
                    id=hypothesis_id, workspace_id=workspace_id, application_id=application_id,
                    environment_id=environment_id, incident_id=run_row["incident_id"], run_id=run_id,
                    revision=1, created_at=timestamp, updated_at=timestamp,
                    statement=statement, confidence=confidence,
                    support_evidence_ids=support_evidence_ids,
                    counterevidence_ids=counterevidence_ids,
                    state=HypothesisState.PROPOSED,
                )
            except ValueError as exc:
                raise ProductError("INVALID_HYPOTHESIS", "假设不符合产品契约", 422) from exc
            record = hypothesis.model_dump(mode="json")
            db.execute(
                """INSERT INTO investigation_hypotheses(
                    id,workspace_id,application_id,environment_id,incident_id,run_id,revision,data,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (hypothesis_id, workspace_id, application_id, environment_id, run_row["incident_id"], run_id,
                 1, _encode(record), timestamp, timestamp),
            )
            self._workspace_event(db, workspace_id=workspace_id, application_id=application_id,
                resource_type="hypothesis", resource_id=hypothesis_id, event_type="hypothesis.proposed",
                record=record, event_metadata={"run_id": run_id})
            self._save_command_receipt(db, command_name="hypothesis.create", scope_key=workspace_id,
                idempotency_key=idempotency_key, payload=payload, response=record)
        return record

    def decide_hypothesis(
        self, workspace_id: str, application_id: str, environment_id: str, run_id: str,
        hypothesis_id: str, target_state: HypothesisState, confidence: float,
        support_evidence_ids: list[str], counterevidence_ids: list[str], reason: str, *,
        expected_revision: int, idempotency_key: str,
    ) -> dict[str, Any]:
        reason = reason.strip()
        all_refs = support_evidence_ids + counterevidence_ids
        if not reason or len(reason) > 1000 or not 0 <= confidence <= 1:
            raise ProductError("INVALID_HYPOTHESIS_DECISION", "判断原因或置信度无效", 422)
        if target_state == HypothesisState.PROPOSED or len(set(all_refs)) != len(all_refs):
            raise ProductError("INVALID_HYPOTHESIS_DECISION", "判断状态和证据引用无效", 422)
        if target_state == HypothesisState.SUPPORTED and not support_evidence_ids:
            raise ProductError("SUPPORTING_EVIDENCE_REQUIRED", "支持假设必须引用至少一条支持证据", 422)
        if target_state == HypothesisState.REFUTED and not counterevidence_ids:
            raise ProductError("COUNTEREVIDENCE_REQUIRED", "反驳假设必须引用至少一条反证", 422)
        payload = {
            "workspace_id": workspace_id, "application_id": application_id,
            "environment_id": environment_id, "run_id": run_id, "hypothesis_id": hypothesis_id,
            "expected_revision": expected_revision, "target_state": target_state.value,
            "confidence": confidence, "support_evidence_ids": support_evidence_ids,
            "counterevidence_ids": counterevidence_ids, "reason": reason,
        }
        with self.tx() as db:
            self._require_application(db, workspace_id, application_id)
            replay = self._replay_command(db, command_name="hypothesis.decide", scope_key=workspace_id,
                idempotency_key=idempotency_key, payload=payload)
            if replay is not None:
                return replay
            run_row = db.execute(
                """SELECT state,incident_id FROM investigation_runs WHERE id=? AND workspace_id=?
                   AND application_id=? AND environment_id=?""",
                (run_id, workspace_id, application_id, environment_id),
            ).fetchone()
            if run_row is None:
                raise ProductError("INVESTIGATION_RUN_NOT_FOUND", "调查运行不属于该应用和环境或不存在", 404)
            if run_row["state"] != InvestigationRunState.RUNNING.value:
                raise ProductError("INVESTIGATION_RUN_TERMINAL", "终态调查运行不可更改假设判断", 409)
            row = db.execute(
                """SELECT revision,data FROM investigation_hypotheses WHERE id=? AND workspace_id=?
                   AND application_id=? AND environment_id=? AND incident_id=? AND run_id=?""",
                (hypothesis_id, workspace_id, application_id, environment_id, run_row["incident_id"], run_id),
            ).fetchone()
            if row is None:
                raise ProductError("HYPOTHESIS_NOT_FOUND", "假设不属于该调查运行或不存在", 404)
            if int(row["revision"]) != expected_revision:
                raise ProductError("STALE_HYPOTHESIS_REVISION", "假设已变化，请刷新后重试", 409)
            if all_refs:
                placeholders = ",".join("?" for _ in all_refs)
                rows = db.execute(
                    f"""SELECT id FROM investigation_evidence WHERE id IN ({placeholders})
                        AND workspace_id=? AND application_id=? AND environment_id=?
                        AND incident_id=? AND run_id=?""",
                    (*all_refs, workspace_id, application_id, environment_id, run_row["incident_id"], run_id),
                ).fetchall()
                if {item["id"] for item in rows} != set(all_refs):
                    raise ProductError("EVIDENCE_NOT_FOUND", "判断只能引用同一调查运行中的证据", 404)
            current = json.loads(row["data"])
            timestamp = _now()
            updated = dict(current)
            updated.update({
                "revision": expected_revision + 1, "updated_at": timestamp,
                "state": target_state.value, "confidence": confidence,
                "support_evidence_ids": support_evidence_ids,
                "counterevidence_ids": counterevidence_ids,
            })
            record = Hypothesis.model_validate_json(json.dumps(updated)).model_dump(mode="json")
            db.execute(
                """UPDATE investigation_hypotheses SET revision=?,data=?,updated_at=?
                   WHERE id=? AND revision=?""",
                (record["revision"], _encode(record), timestamp, hypothesis_id, expected_revision),
            )
            self._workspace_event(db, workspace_id=workspace_id, application_id=application_id,
                resource_type="hypothesis", resource_id=hypothesis_id, event_type="hypothesis.decided",
                record=record, event_metadata={"reason": reason, "to_state": target_state.value})
            self._save_command_receipt(db, command_name="hypothesis.decide", scope_key=workspace_id,
                idempotency_key=idempotency_key, payload=payload, response=record)
        return record

    def complete_investigation_task_graph(self, workspace_id, application_id, environment_id, run_id):
        """Finish model analysis without asserting a verified root cause or recovery."""
        with self.tx() as db:
            row = db.execute('SELECT data FROM investigation_runs WHERE id=? AND workspace_id=? AND application_id=? AND environment_id=?',
                             (run_id, workspace_id, application_id, environment_id)).fetchone()
            if row is None:
                raise ProductError('INVESTIGATION_RUN_NOT_FOUND', '调查不存在', 404)
            record = json.loads(row['data'])
            if record['state'] != 'running':
                return record
            graph = db.execute('SELECT * FROM investigation_task_graphs WHERE run_id=?', (run_id,)).fetchone()
            if graph is None or graph['state'] != 'complete':
                raise ProductError('TASK_GRAPH_INCOMPLETE', '调查任务尚未全部完成', 409)
            tasks = self._task_graph_detail_in_tx(db, graph)['tasks']
            final = next(t['result'] for t in tasks if t['role'] == 'synthesizer')
            state, result_type = {'root_cause_candidate': ('succeeded', 'proposal_ready'),
                'needs_data': ('inconclusive', 'needs_data'), 'abstain': ('abstained', 'none')}[final['conclusion']]
            record.update(state=state, result_type=result_type, revision=record['revision'] + 1,
                          updated_at=_now(), completed_at=_now())
            record = InvestigationRun.model_validate_json(json.dumps(record)).model_dump(mode='json')
            db.execute('UPDATE investigation_runs SET revision=?,state=?,data=?,updated_at=? WHERE id=?',
                       (record['revision'], state, _encode(record), record['updated_at'], run_id))
            self._workspace_event(db, workspace_id=workspace_id, application_id=application_id,
                resource_type='investigation_run', resource_id=run_id,
                event_type='investigation_run.analysis_completed', record=record,
                event_metadata={'conclusion': final['conclusion'], 'independent_recovery_verified': False})
        return record

    def finish_manual_investigation_run(
        self, workspace_id: str, application_id: str, environment_id: str, run_id: str,
        result_type: str, reason: str, *, expected_revision: int, idempotency_key: str,
    ) -> dict[str, Any]:
        reason = reason.strip()
        state_for_result = {
            "root_cause_identified": InvestigationRunState.SUCCEEDED,
            "needs_data": InvestigationRunState.INCONCLUSIVE,
            "none": InvestigationRunState.ABSTAINED,
        }
        if result_type not in state_for_result or not reason or len(reason) > 1000:
            raise ProductError("INVALID_INVESTIGATION_RESULT", "调查结论或说明无效", 422)
        payload = {
            "workspace_id": workspace_id, "application_id": application_id,
            "environment_id": environment_id, "run_id": run_id,
            "expected_revision": expected_revision, "result_type": result_type, "reason": reason,
        }
        with self.tx() as db:
            self._require_application(db, workspace_id, application_id)
            replay = self._replay_command(db, command_name="investigation_run.finish_manual",
                scope_key=workspace_id, idempotency_key=idempotency_key, payload=payload)
            if replay is not None:
                return replay
            row = db.execute(
                """SELECT revision,data,incident_id FROM investigation_runs WHERE id=? AND workspace_id=?
                   AND application_id=? AND environment_id=?""",
                (run_id, workspace_id, application_id, environment_id),
            ).fetchone()
            if row is None:
                raise ProductError("INVESTIGATION_RUN_NOT_FOUND", "调查运行不属于该应用和环境或不存在", 404)
            if int(row["revision"]) != expected_revision:
                raise ProductError("STALE_INVESTIGATION_REVISION", "调查运行已变化，请刷新后再结束", 409)
            if row["data"] is None:
                raise ProductError("INVESTIGATION_RUN_NOT_FOUND", "调查运行不存在", 404)
            if json.loads(row["data"])["state"] != InvestigationRunState.RUNNING.value:
                raise ProductError("INVESTIGATION_RUN_TERMINAL", "调查运行已经结束；继续工作请新建运行", 409)
            if result_type == "root_cause_identified" and db.execute(
                """SELECT 1 FROM investigation_hypotheses WHERE workspace_id=? AND application_id=?
                   AND environment_id=? AND incident_id=? AND run_id=?
                   AND json_extract(data,'$.state')='supported' LIMIT 1""",
                (workspace_id, application_id, environment_id, row["incident_id"], run_id),
            ).fetchone() is None:
                raise ProductError("SUPPORTED_HYPOTHESIS_REQUIRED", "记录根因前，至少须有一条人工标记为支持的假设", 409)
            current = json.loads(row["data"])
            timestamp = _now()
            updated = dict(current)
            updated.update({
                "revision": expected_revision + 1, "updated_at": timestamp,
                "state": state_for_result[result_type].value,
                "result_type": result_type, "completed_at": timestamp,
            })
            record = InvestigationRun.model_validate_json(json.dumps(updated)).model_dump(mode="json")
            db.execute(
                """UPDATE investigation_runs SET revision=?,state=?,data=?,updated_at=?
                   WHERE id=? AND revision=?""",
                (record["revision"], record["state"], _encode(record), timestamp, run_id, expected_revision),
            )
            incident_row = db.execute(
                """SELECT revision,data FROM incidents WHERE id=? AND workspace_id=? AND application_id=?
                   AND environment_id=?""",
                (row["incident_id"], workspace_id, application_id, environment_id),
            ).fetchone()
            incident_data = json.loads(incident_row["data"])
            if result_type == "needs_data" and incident_data["state"] != IncidentState.NEEDS_INPUT.value:
                incident_updated = dict(incident_data)
                incident_updated.update({
                    "revision": int(incident_row["revision"]) + 1, "updated_at": timestamp,
                    "state": IncidentState.NEEDS_INPUT.value, "outcome": None,
                })
                incident_updated = Incident.model_validate_json(json.dumps(incident_updated)).model_dump(mode="json")
                db.execute("UPDATE incidents SET revision=?,data=?,updated_at=? WHERE id=? AND revision=?",
                    (incident_updated["revision"], _encode(incident_updated), timestamp,
                     row["incident_id"], incident_row["revision"]))
                self._workspace_event(db, workspace_id=workspace_id, application_id=application_id,
                    resource_type="incident", resource_id=row["incident_id"],
                    event_type="incident.state_changed", record=incident_updated,
                    event_metadata={"from_state": incident_data["state"],
                        "to_state": IncidentState.NEEDS_INPUT.value, "reason": reason})
            self._workspace_event(db, workspace_id=workspace_id, application_id=application_id,
                resource_type="investigation_run", resource_id=run_id, event_type="investigation_run.finished",
                record=record, event_metadata={"result_type": result_type, "reason": reason,
                    "execution_mode": "manual"})
            self._save_command_receipt(db, command_name="investigation_run.finish_manual",
                scope_key=workspace_id, idempotency_key=idempotency_key, payload=payload, response=record)
        return record

    def list_incidents(
        self,
        workspace_id: str,
        application_id: str,
        environment_id: str,
        *,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        if not environment_id or not 1 <= limit <= 1000:
            raise ProductError("INVALID_INCIDENT_PAGE", "事故分页参数无效", 422)
        with self.tx() as db:
            self._require_application(db, workspace_id, application_id)
            rows = db.execute(
                """SELECT data FROM incidents WHERE workspace_id=? AND application_id=? AND environment_id=?
                   ORDER BY created_at DESC,id DESC LIMIT ?""",
                (workspace_id, application_id, environment_id, limit),
            ).fetchall()
        return [json.loads(row["data"]) for row in rows]

    def get_incident(
        self, workspace_id: str, application_id: str, environment_id: str, incident_id: str,
    ) -> dict[str, Any] | None:
        with self.tx() as db:
            row = db.execute(
                """SELECT data FROM incidents WHERE id=? AND workspace_id=? AND application_id=?
                   AND environment_id=?""",
                (incident_id, workspace_id, application_id, environment_id),
            ).fetchone()
        return json.loads(row["data"]) if row else None

    def workspace_events(
        self,
        workspace_id: str,
        *,
        after: int = 0,
        limit: int = 200,
        application_id: str | None = None,
    ) -> list[dict[str, Any]]:
        if after < 0 or limit < 1 or limit > 1000:
            raise ProductError("INVALID_EVENT_PAGE", "事件分页参数无效", 422)
        with self.tx() as db:
            self._require_workspace(db, workspace_id)
            if application_id is None:
                rows = db.execute(
                    """SELECT data FROM workspace_events WHERE workspace_id=? AND sequence>?
                       ORDER BY sequence LIMIT ?""",
                    (workspace_id, after, limit),
                ).fetchall()
            else:
                self._require_application(db, workspace_id, application_id)
                rows = db.execute(
                    """SELECT data FROM workspace_events WHERE workspace_id=? AND application_id=?
                       AND sequence>? ORDER BY sequence LIMIT ?""",
                    (workspace_id, application_id, after, limit),
                ).fetchall()
        return [json.loads(row["data"]) for row in rows]
