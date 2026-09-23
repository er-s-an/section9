from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient

from s9.product.credentials import ProductCredentialBroker
from s9.product.registry import ProductError, ProductRegistry
from s9.product.v1_api import product_error_response, router, validation_error_response


def _client(tmp_path):
    app = FastAPI()
    app.include_router(router)
    registry = ProductRegistry(tmp_path / "product.sqlite")
    app.state.core = SimpleNamespace(product=SimpleNamespace(registry=registry))
    app.state.product_credentials = ProductCredentialBroker({
        "S9_OBSERVED_LANGFUSE_PUBLIC_KEY": "public-fixture",
        "S9_OBSERVED_LANGFUSE_SECRET_KEY": "secret-fixture",
        "S9_OBSERVED_LANGFUSE_PROJECT_ID": "project-import",
    })
    app.add_exception_handler(ProductError, product_error_response)
    app.add_exception_handler(RequestValidationError, validation_error_response)
    workspace = registry.create_workspace("W", idempotency_key="w")
    application = registry.create_application(workspace["id"], "App", idempotency_key="a")
    connection = registry.create_connection(workspace["id"], application["id"], "Langfuse",
        idempotency_key="c", credential_ref="env://S9_OBSERVED_LANGFUSE")
    binding = registry.create_scope_binding(workspace["id"], application["id"], "prod", connection["id"],
        "langfuse_project", "project-import", idempotency_key="b")
    checked = registry.record_connection_check(workspace["id"], application["id"], connection["id"],
        binding["id"], expected_connection_revision=1, expected_binding_revision=1,
        idempotency_key="verify", request_parameters={"environment_id": "prod"}, check={
            "checked_at": "2026-09-23T09:00:00Z", "outcome": "empty",
            "connection_status": "connected", "binding_status": "confirmed", "http_status": 200,
            "observed_count": 0, "watermark": None, "coverage": {"complete": True},
            "scope_confirmed": True, "detail": "fixture scope confirmed",
        })
    app.state.ids = (workspace["id"], application["id"],
                     checked["connection"]["id"], checked["scope_binding"]["id"])
    return TestClient(app)


def _url(client):
    workspace, application, connection, binding = client.app.state.ids
    return (f"/api/v1/workspaces/{workspace}/applications/{application}/connections/{connection}"
            f"/scope-bindings/{binding}/signals/import")


def _body(**overrides):
    return {
        "environment_id": "prod",
        "from_start_time": "2026-09-22T00:00:00Z",
        "to_start_time": "2026-09-23T00:00:00Z",
        "page_size": 2,
        "max_pages": 1,
        **overrides,
    }


def test_langfuse_import_resumes_cursor_and_keeps_observation_text_private(tmp_path, monkeypatch):
    import s9.product.v1_api as api_module

    client = _client(tmp_path)
    cursors = []

    async def read_page(connection, binding, credentials, *, from_start_time, to_start_time, cursor, limit):
        cursors.append(cursor)
        assert credentials.project_id == binding["external_resource_id"] == "project-import"
        assert (from_start_time, to_start_time) == ("2026-09-22T00:00:00Z", "2026-09-23T00:00:00Z")
        if cursor is None:
            rows = [
                {"id": "obs-1", "timestamp": "2026-09-22T01:00:00Z", "type": "SPAN", "name": "private trace title", "project_id": "project-import"},
                {"id": "obs-2", "timestamp": "2026-09-22T02:00:00Z", "type": "GENERATION", "name": "private prompt text", "project_id": "project-import"},
            ]
            return {"http_status": 200, "availability": "data", "rows": rows,
                    "coverage": {"complete": False, "next_cursor": "cursor-next"}}
        assert cursor == "cursor-next"
        return {"http_status": 200, "availability": "data", "rows": [
            {"id": "obs-3", "timestamp": "2026-09-22T03:00:00Z", "type": "SPAN", "project_id": "project-import"},
        ], "coverage": {"complete": True, "next_cursor": None}}

    monkeypatch.setattr(api_module, "read_langfuse_observations", read_page)
    first = client.post(_url(client), headers={"Idempotency-Key": "import-page-1"}, json=_body())
    assert first.status_code == 201
    page_one = first.json()
    assert page_one["created_count"] == 2
    assert page_one["coverage"]["complete"] is False
    assert page_one["coverage"]["next_cursor"] == "cursor-next"
    assert page_one["coverage"]["continuation_required"] is True
    assert page_one["checkpoint"]["revision"] == 1
    assert page_one["checkpoint"]["complete"] is False
    assert "private trace title" not in first.text
    assert "private prompt text" not in first.text
    workspace, application, _, _ = client.app.state.ids
    listed_checkpoint = client.get(
        f"/api/v1/workspaces/{workspace}/applications/{application}/signal-import-checkpoints",
        params={"environment_id": "prod"},
    ).json()["items"][0]
    assert listed_checkpoint["next_cursor"] == "cursor-next"
    assert listed_checkpoint["complete"] is False

    next_body = _body(cursor="cursor-next")
    second = client.post(_url(client), headers={"Idempotency-Key": "import-page-2"}, json=next_body)
    assert second.status_code == 201
    page_two = second.json()
    assert page_two["created_count"] == 1
    assert page_two["coverage"]["complete"] is True
    assert page_two["coverage"]["next_cursor"] is None
    assert page_two["checkpoint"]["revision"] == 2
    assert page_two["checkpoint"]["complete"] is True
    assert cursors == [None, "cursor-next"]
    workspace, application, _, binding = client.app.state.ids
    checkpoint = client.get(
        f"/api/v1/workspaces/{workspace}/applications/{application}/scope-bindings/{binding}/signals/import-state",
        params={"environment_id": "prod", "from_start_time": "2026-09-22T00:00:00Z",
                "to_start_time": "2026-09-23T00:00:00Z"},
    ).json()["checkpoint"]
    assert checkpoint["revision"] == 2
    assert checkpoint["complete"] is True
    assert checkpoint["next_cursor"] is None
    listed_complete = client.get(
        f"/api/v1/workspaces/{workspace}/applications/{application}/signal-import-checkpoints",
        params={"environment_id": "prod"},
    ).json()["items"][0]
    assert listed_complete["complete"] is True

    retry = client.post(_url(client), headers={"Idempotency-Key": "import-page-2"}, json=next_body)
    assert retry.json() == page_two
    conflict = client.post(_url(client), headers={"Idempotency-Key": "import-page-2"},
        json=_body(cursor="cursor-next", page_size=1))
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    assert cursors == [None, "cursor-next"]
    signals = client.get(
        f"/api/v1/workspaces/{client.app.state.ids[0]}/applications/{client.app.state.ids[1]}/signals",
        params={"environment_id": "prod"},
    ).json()["items"]
    assert len(signals) == 3
    assert all(item["status"] == "new" for item in signals)
    assert all("private" not in item["summary"] for item in signals)


def test_langfuse_import_detects_repeated_cursor_and_keeps_partial_coverage(tmp_path, monkeypatch):
    import s9.product.v1_api as api_module

    client = _client(tmp_path)
    calls = []

    async def repeated_cursor(*args, cursor, **kwargs):
        calls.append(cursor)
        return {"http_status": 200, "availability": "data", "rows": [],
                "coverage": {"complete": False, "next_cursor": "loop"}}

    monkeypatch.setattr(api_module, "read_langfuse_observations", repeated_cursor)
    response = client.post(_url(client), headers={"Idempotency-Key": "loop"}, json=_body(max_pages=5))
    assert response.status_code == 201
    result = response.json()
    assert result["coverage"]["complete"] is False
    assert result["coverage"]["error"] == "cursor_repeated"
    assert result["coverage"]["next_cursor"] is None
    assert calls == [None, "loop"]


def test_langfuse_import_reports_missing_local_credentials_without_provider_call(tmp_path, monkeypatch):
    from s9.product import v1_api as api_module

    client = _client(tmp_path)
    client.app.state.product_credentials = ProductCredentialBroker({})
    called = False

    async def forbidden_provider_call(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("provider must not be called without credentials")

    monkeypatch.setattr(api_module, "read_langfuse_observations", forbidden_provider_call)
    response = client.post(_url(client), headers={"Idempotency-Key": "missing-local-credential"}, json=_body())
    assert response.status_code == 424
    assert response.json()["error"]["code"] == "CREDENTIAL_NOT_CONFIGURED"
    assert called is False
    assert "secret" not in response.text.lower()


def test_langfuse_import_requires_confirmed_scope_and_bounded_window(tmp_path):
    client = _client(tmp_path)
    path = _url(client)
    invalid_window = client.post(path, headers={"Idempotency-Key": "bad-window"},
        json=_body(from_start_time="2026-01-01T00:00:00Z", to_start_time="2026-03-01T00:00:00Z"))
    assert invalid_window.status_code == 422
    assert invalid_window.json()["error"]["code"] == "INVALID_REQUEST"

    workspace, application, connection, binding = client.app.state.ids
    with client.app.state.core.product.registry.tx() as db:
        data = db.execute("SELECT data FROM scope_bindings WHERE id=?", (binding,)).fetchone()[0]
        import json
        record = json.loads(data)
        record["status"] = "pending"
        db.execute("UPDATE scope_bindings SET data=? WHERE id=?", (json.dumps(record), binding))
    unverified = client.post(path, headers={"Idempotency-Key": "unverified"}, json=_body())
    assert unverified.status_code == 409
    assert unverified.json()["error"]["code"] == "SOURCE_SCOPE_UNVERIFIED"
