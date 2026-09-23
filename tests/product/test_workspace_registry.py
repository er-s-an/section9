import json
import sqlite3

import pytest

from s9.product.registry import ProductError, ProductRegistry


def test_workspace_migration_is_additive_and_preserves_legacy_project_records(tmp_path):
    database = tmp_path / "product.sqlite"
    registry = ProductRegistry(database)
    legacy = registry.put("project", "project-a", {"name": "Legacy"},
        project_id="project-a", environment_id="production")

    workspace = registry.create_workspace("Team One", workspace_id="workspace-a", idempotency_key="ws-create-1")
    assert workspace["id"] == workspace["workspace_id"] == "workspace-a"
    assert registry.create_workspace("Team One", workspace_id="workspace-a",
        idempotency_key="ws-create-1") == workspace
    assert len(registry.list_workspaces()) == 1
    with pytest.raises(ProductError) as replay_conflict:
        registry.create_workspace("Different name", workspace_id="workspace-a", idempotency_key="ws-create-1")
    assert (replay_conflict.value.code, replay_conflict.value.status) == ("IDEMPOTENCY_CONFLICT", 409)

    reopened = ProductRegistry(database)
    assert reopened.get("project", "project-a", project_id="project-a",
        environment_id="production") == legacy
    assert reopened.get_workspace("workspace-a") == workspace
    assert reopened.get_workspace("workspace-a")["budget"]["token_limit"] is None
    assert reopened.get_workspace("workspace-a")["budget"]["validation_reserve_tokens"] == 0
    with reopened.tx() as db:
        assert db.execute("SELECT MAX(version) FROM product_schema_migrations").fetchone()[0] == 8
        assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []


def test_workspace_application_connection_and_binding_commit_with_scoped_events(tmp_path):
    registry = ProductRegistry(tmp_path / "product.sqlite")
    workspace = registry.create_workspace("Demo", workspace_id="workspace-demo", idempotency_key="ws-1")
    application = registry.create_application(workspace["id"], "Checkout", idempotency_key="app-1")
    connection = registry.create_connection(
        workspace["id"], application["id"], "Langfuse", endpoint="https://langfuse.example/api",
        idempotency_key="conn-1", credential_ref="keychain://section9/connection-demo",
        capabilities=["traces.read"],
    )
    binding = registry.create_scope_binding(
        workspace["id"], application["id"], "production", connection["id"],
        "langfuse_project", "project-demo", idempotency_key="binding-1", read_scopes=["traces.read"],
    )

    assert connection["status"] == "pending"
    assert connection["checked_at"] is None
    assert binding["status"] == "pending"
    assert registry.get_application(workspace["id"], application["id"]) == application
    assert registry.get_connection(workspace["id"], application["id"], connection["id"]) == connection
    assert registry.get_scope_binding(workspace["id"], application["id"], "production", binding["id"]) == binding
    assert registry.list_applications(workspace["id"]) == [application]
    assert registry.list_connections(workspace["id"], application["id"]) == [connection]
    assert registry.list_scope_bindings(workspace["id"], application["id"], "production") == [binding]

    events = registry.workspace_events(workspace["id"], limit=10)
    assert [event["event_type"] for event in events] == [
        "workspace.created", "application.created", "connection.created", "scope_binding.created",
    ]
    assert [event["sequence"] for event in events] == sorted(event["sequence"] for event in events)
    assert "keychain://section9/connection-demo" not in json.dumps(events)
    assert registry.workspace_events(workspace["id"], application_id=application["id"])[0]["event_type"] == "application.created"


def test_workspace_storage_fails_closed_on_cross_scope_reads_and_writes(tmp_path):
    registry = ProductRegistry(tmp_path / "product.sqlite")
    workspace_a = registry.create_workspace("A", workspace_id="workspace-a", idempotency_key="ws-a")
    workspace_b = registry.create_workspace("B", workspace_id="workspace-b", idempotency_key="ws-b")
    application_a = registry.create_application(workspace_a["id"], "App A", idempotency_key="app-a")
    connection_a = registry.create_connection(workspace_a["id"], application_a["id"], "GitHub",
        idempotency_key="conn-a")
    binding_a = registry.create_scope_binding(workspace_a["id"], application_a["id"], "prod",
        connection_a["id"], "github_repository", "org/repo", idempotency_key="binding-a")

    assert registry.get_application(workspace_b["id"], application_a["id"]) is None
    assert registry.get_connection(workspace_b["id"], application_a["id"], connection_a["id"]) is None
    assert registry.get_scope_binding(workspace_b["id"], application_a["id"], "prod", binding_a["id"]) is None
    with pytest.raises(ProductError) as app_error:
        registry.create_connection(workspace_b["id"], application_a["id"], "GitHub", idempotency_key="bad-conn")
    assert (app_error.value.code, app_error.value.status) == ("APPLICATION_NOT_FOUND", 404)
    with pytest.raises(ProductError) as binding_error:
        registry.create_scope_binding(workspace_b["id"], application_a["id"], "prod",
            connection_a["id"], "github_repository", "org/repo", idempotency_key="bad-binding")
    assert (binding_error.value.code, binding_error.value.status) == ("APPLICATION_NOT_FOUND", 404)


def test_scope_binding_is_unique_within_application_environment_and_external_resource(tmp_path):
    registry = ProductRegistry(tmp_path / "product.sqlite")
    workspace = registry.create_workspace("Demo", idempotency_key="ws")
    application = registry.create_application(workspace["id"], "App", idempotency_key="app")
    connection = registry.create_connection(workspace["id"], application["id"], "GitHub", idempotency_key="conn")
    registry.create_scope_binding(workspace["id"], application["id"], "prod", connection["id"],
        "github_repository", "org/repo", idempotency_key="binding-1")
    with pytest.raises(ProductError) as caught:
        registry.create_scope_binding(workspace["id"], application["id"], "prod", connection["id"],
            "github_repository", "org/repo", idempotency_key="binding-2")
    assert (caught.value.code, caught.value.status) == ("SCOPE_BINDING_EXISTS", 409)


def test_workspace_event_pagination_rejects_invalid_limits(tmp_path):
    registry = ProductRegistry(tmp_path / "product.sqlite")
    workspace = registry.create_workspace("Demo", idempotency_key="ws")
    for kwargs in ({"after": -1}, {"limit": 0}, {"limit": 1001}):
        with pytest.raises(ProductError) as caught:
            registry.workspace_events(workspace["id"], **kwargs)
        assert caught.value.code == "INVALID_EVENT_PAGE"


def test_workspace_policy_counts_survive_redaction_while_credentials_do_not():
    from s9.product.bundle import redact_secrets

    assert redact_secrets({"token_limit": 2500, "validation_reserve_tokens": 300,
        "access_token": "opaque-sensitive-value"}) == {
            "token_limit": 2500,
            "validation_reserve_tokens": 300,
            "access_token": "[REDACTED]",
        }


def test_workspace_migration_version_newer_than_code_fails_closed(tmp_path):
    database = tmp_path / "product.sqlite"
    ProductRegistry(database)
    db = sqlite3.connect(database)
    db.execute("INSERT INTO product_schema_migrations(version, applied_at) VALUES(9, 'future')")
    db.commit()
    db.close()

    with pytest.raises(RuntimeError, match="newer than this application"):
        ProductRegistry(database)
