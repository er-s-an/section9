from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient

from s9.product.registry import ProductError, ProductRegistry
from s9.product.credentials import ProductCredentialBroker
from s9.product.v1_api import product_error_response, router, validation_error_response


def _client(tmp_path):
    app = FastAPI()
    app.include_router(router)
    app.state.core = SimpleNamespace(product=SimpleNamespace(registry=ProductRegistry(tmp_path / "product.sqlite")))

    app.add_exception_handler(ProductError, product_error_response)
    app.add_exception_handler(RequestValidationError, validation_error_response)
    return TestClient(app)


def test_v1_setup_flow_creates_scoped_records_and_replays_idempotent_commands(tmp_path):
    client = _client(tmp_path)
    workspace_response = client.post("/api/v1/workspaces", headers={"Idempotency-Key": "workspace-create"},
        json={"name": "Demo", "budget": {"token_limit": 2000, "validation_reserve_tokens": 250,
        "max_concurrency": 2}, "deployment_mode": "local"})
    assert workspace_response.status_code == 201
    workspace = workspace_response.json()
    assert workspace["budget"]["token_limit"] == 2000
    assert workspace["budget"]["validation_reserve_tokens"] == 250
    assert client.post("/api/v1/workspaces", headers={"Idempotency-Key": "workspace-create"},
        json={"name": "Demo", "budget": {"token_limit": 2000, "validation_reserve_tokens": 250,
        "max_concurrency": 2}, "deployment_mode": "local"}).json() == workspace
    conflict = client.post("/api/v1/workspaces", headers={"Idempotency-Key": "workspace-create"},
        json={"name": "Different", "deployment_mode": "local"})
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"

    workspace_id = workspace["id"]
    application_response = client.post(f"/api/v1/workspaces/{workspace_id}/applications",
        headers={"Idempotency-Key": "application-create"}, json={"name": "Checkout"})
    assert application_response.status_code == 201
    application = application_response.json()
    assert application["workspace_id"] == workspace_id

    connection_response = client.post(
        f"/api/v1/workspaces/{workspace_id}/applications/{application['id']}/connections",
        headers={"Idempotency-Key": "connection-create"},
        json={"provider": "Langfuse", "endpoint": "https://langfuse.example/api",
        "credential_ref": "keychain://section9/langfuse", "capabilities": ["traces.read"]},
    )
    assert connection_response.status_code == 201
    connection = connection_response.json()
    assert connection["status"] == "pending"
    assert connection["checked_at"] is None

    binding_response = client.post(
        f"/api/v1/workspaces/{workspace_id}/applications/{application['id']}/scope-bindings",
        headers={"Idempotency-Key": "binding-create"},
        json={"environment_id": "production", "connection_id": connection["id"],
        "resource_type": "langfuse_project", "external_resource_id": "project-demo",
        "read_scopes": ["traces.read"]},
    )
    assert binding_response.status_code == 201
    binding = binding_response.json()
    assert binding["status"] == "pending"

    listed = client.get(f"/api/v1/workspaces/{workspace_id}/applications/{application['id']}/scope-bindings",
        params={"environment_id": "production"})
    assert listed.json()["items"] == [binding]
    events = client.get(f"/api/v1/workspaces/{workspace_id}/events", params={"limit": 2}).json()
    assert [event["event_type"] for event in events["items"]] == ["workspace.created", "application.created"]
    assert events["next_after"] == int(events["items"][-1]["sequence"])


def test_v1_api_scope_mismatch_and_missing_idempotency_fail_closed(tmp_path):
    client = _client(tmp_path)
    a = client.post("/api/v1/workspaces", headers={"Idempotency-Key": "w-a"}, json={"name": "A"}).json()
    b = client.post("/api/v1/workspaces", headers={"Idempotency-Key": "w-b"}, json={"name": "B"}).json()
    app_a = client.post(f"/api/v1/workspaces/{a['id']}/applications",
        headers={"Idempotency-Key": "app-a"}, json={"name": "App A"}).json()

    cross_scope = client.get(f"/api/v1/workspaces/{b['id']}/applications/{app_a['id']}")
    assert cross_scope.status_code == 404
    assert cross_scope.json()["error"]["code"] == "APPLICATION_NOT_FOUND"
    assert cross_scope.json()["error"]["retryable"] is False
    assert cross_scope.json()["error"]["request_id"]
    missing_key = client.post(f"/api/v1/workspaces/{a['id']}/applications", json={"name": "No key"})
    assert missing_key.status_code == 422
    assert missing_key.json()["error"]["code"] == "INVALID_REQUEST"
    assert missing_key.json()["error"]["details"]["fields"]
    assert len(client.get("/api/v1/workspaces").json()["items"]) == 2


def _create_langfuse_binding(client):
    workspace = client.post("/api/v1/workspaces", headers={"Idempotency-Key": "ws"},
        json={"name": "Observed App"}).json()
    application = client.post(f"/api/v1/workspaces/{workspace['id']}/applications",
        headers={"Idempotency-Key": "app"}, json={"name": "App"}).json()
    connection = client.post(f"/api/v1/workspaces/{workspace['id']}/applications/{application['id']}/connections",
        headers={"Idempotency-Key": "conn"}, json={"provider": "Langfuse",
        "credential_ref": "env://S9_OBSERVED_LANGFUSE"}).json()
    binding = client.post(f"/api/v1/workspaces/{workspace['id']}/applications/{application['id']}/scope-bindings",
        headers={"Idempotency-Key": "binding"}, json={"environment_id": "prod",
        "connection_id": connection["id"], "resource_type": "langfuse_project",
        "external_resource_id": "project-1", "read_scopes": ["traces.read"]}).json()
    return workspace, application, connection, binding


def test_langfuse_connection_check_records_empty_data_as_connected_and_confirms_scope(tmp_path, monkeypatch):
    import s9.product.v1_api as api_module

    client = _client(tmp_path)
    client.app.state.product_credentials = ProductCredentialBroker({
        "S9_OBSERVED_LANGFUSE_PUBLIC_KEY": "public-test",
        "S9_OBSERVED_LANGFUSE_SECRET_KEY": "secret-test",
        "S9_OBSERVED_LANGFUSE_PROJECT_ID": "project-1",
    })
    workspace, application, connection, binding = _create_langfuse_binding(client)
    calls = []

    async def fake_check(*args, **kwargs):
        calls.append((args, kwargs))
        return {"checked_at": "2026-09-23T00:00:00Z", "outcome": "empty",
            "connection_status": "connected", "binding_status": "confirmed", "http_status": 200,
            "observed_count": 0, "watermark": None,
            "coverage": {"from_start_time": "2026-09-22T23:00:00Z", "to_start_time": "2026-09-23T00:00:00Z", "complete": True},
            "scope_confirmed": True, "detail": "连接已认证，但所选时间窗内没有观察记录"}

    monkeypatch.setattr(api_module, "check_langfuse_binding", fake_check)
    url = (f"/api/v1/workspaces/{workspace['id']}/applications/{application['id']}"
           f"/connections/{connection['id']}/scope-bindings/{binding['id']}/verify")
    body = {"environment_id": "prod", "expected_connection_revision": 1, "expected_binding_revision": 1}
    headers = {"Idempotency-Key": "langfuse-check-1"}
    checked = client.post(url, headers=headers, json=body)
    assert checked.status_code == 200
    result = checked.json()
    assert result["connection"]["status"] == "connected"
    assert result["scope_binding"]["status"] == "confirmed"
    assert result["check"]["outcome"] == "empty"
    assert result["check"]["observed_count"] == 0
    assert "secret-test" not in checked.text

    replay = client.post(url, headers=headers, json=body)
    assert replay.status_code == 200
    assert replay.json() == result
    assert len(calls) == 1
    listed = client.get(f"/api/v1/workspaces/{workspace['id']}/applications/{application['id']}/connections").json()
    assert listed["items"][0]["latest_check"]["outcome"] == "empty"
    events = client.get(f"/api/v1/workspaces/{workspace['id']}/events").json()["items"]
    assert events[-1]["event_type"] == "connection.check_completed"


def test_langfuse_check_missing_credentials_fails_before_provider_and_keeps_pending(tmp_path):
    client = _client(tmp_path)
    client.app.state.product_credentials = ProductCredentialBroker({})
    workspace, application, connection, binding = _create_langfuse_binding(client)
    url = (f"/api/v1/workspaces/{workspace['id']}/applications/{application['id']}"
           f"/connections/{connection['id']}/scope-bindings/{binding['id']}/verify")
    response = client.post(url, headers={"Idempotency-Key": "missing-credential"},
        json={"environment_id": "prod", "expected_connection_revision": 1, "expected_binding_revision": 1})
    assert response.status_code == 424
    assert response.json()["error"]["code"] == "CREDENTIAL_NOT_CONFIGURED"
    assert "secret" not in response.text.lower()
    current = client.get(f"/api/v1/workspaces/{workspace['id']}/applications/{application['id']}/connections").json()["items"][0]
    assert current["status"] == "pending"
    assert current["checked_at"] is None


def test_langfuse_timeout_is_recorded_as_degraded_without_payload_or_secret(tmp_path, monkeypatch):
    import httpx
    import s9.product.v1_api as api_module

    client = _client(tmp_path)
    client.app.state.product_credentials = ProductCredentialBroker({
        "S9_OBSERVED_LANGFUSE_PUBLIC_KEY": "public-test",
        "S9_OBSERVED_LANGFUSE_SECRET_KEY": "secret-test",
        "S9_OBSERVED_LANGFUSE_PROJECT_ID": "project-1",
    })
    workspace, application, connection, binding = _create_langfuse_binding(client)

    async def timeout(*args, **kwargs):
        raise httpx.ReadTimeout("request contained a secret-test value")

    monkeypatch.setattr(api_module, "check_langfuse_binding", timeout)
    url = (f"/api/v1/workspaces/{workspace['id']}/applications/{application['id']}"
           f"/connections/{connection['id']}/scope-bindings/{binding['id']}/verify")
    response = client.post(url, headers={"Idempotency-Key": "langfuse-timeout"}, json={
        "environment_id": "prod", "expected_connection_revision": 1, "expected_binding_revision": 1,
    })
    assert response.status_code == 200
    assert response.json()["connection"]["status"] == "degraded"
    assert response.json()["scope_binding"]["status"] == "pending"
    assert "secret-test" not in response.text
    assert "request contained" not in response.text


def test_github_repository_check_uses_scoped_token_and_records_exact_source_commit(tmp_path, monkeypatch):
    import s9.product.v1_api as api_module

    client = _client(tmp_path)
    client.app.state.product_credentials = ProductCredentialBroker({"S9_OBSERVED_GITHUB_TOKEN": "github-secret"})
    workspace = client.post("/api/v1/workspaces", headers={"Idempotency-Key": "gh-ws"},
        json={"name": "Repo workspace"}).json()
    application = client.post(f"/api/v1/workspaces/{workspace['id']}/applications",
        headers={"Idempotency-Key": "gh-app"}, json={"name": "Repo app"}).json()
    connection = client.post(f"/api/v1/workspaces/{workspace['id']}/applications/{application['id']}/connections",
        headers={"Idempotency-Key": "gh-conn"}, json={"provider": "GitHub",
        "credential_ref": "env://S9_OBSERVED_GITHUB"}).json()
    binding = client.post(f"/api/v1/workspaces/{workspace['id']}/applications/{application['id']}/scope-bindings",
        headers={"Idempotency-Key": "gh-bind"}, json={"environment_id": "prod",
        "connection_id": connection["id"], "resource_type": "github_repository",
        "external_resource_id": "acme/service", "read_scopes": ["contents.read"]}).json()
    captured = []

    async def fake_check(conn, scope, credential):
        captured.append((conn, scope, credential))
        return {"checked_at": "2026-09-23T00:00:00Z", "outcome": "data",
            "connection_status": "connected", "binding_status": "confirmed", "http_status": 200,
            "observed_count": 4, "watermark": None, "source_version": "a" * 40,
            "coverage": {"complete": True}, "scope_confirmed": True,
            "detail": "GitHub 仓库已读取并绑定到默认分支提交"}

    monkeypatch.setattr(api_module, "check_github_binding", fake_check)
    url = (f"/api/v1/workspaces/{workspace['id']}/applications/{application['id']}"
           f"/connections/{connection['id']}/scope-bindings/{binding['id']}/verify")
    response = client.post(url, headers={"Idempotency-Key": "gh-check"}, json={
        "environment_id": "prod", "expected_connection_revision": 1, "expected_binding_revision": 1,
    })
    assert response.status_code == 200
    assert response.json()["connection"]["status"] == "connected"
    assert response.json()["scope_binding"]["status"] == "confirmed"
    assert response.json()["check"]["source_version"] == "a" * 40
    assert captured[0][2].token == "github-secret"
    assert "github-secret" not in response.text
