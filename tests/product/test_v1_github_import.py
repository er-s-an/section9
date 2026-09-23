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
    app.state.product_credentials = ProductCredentialBroker({"S9_OBSERVED_GITHUB_TOKEN": "ghp-fixture-only"})
    app.add_exception_handler(ProductError, product_error_response)
    app.add_exception_handler(RequestValidationError, validation_error_response)
    workspace = registry.create_workspace("W", idempotency_key="w")
    application = registry.create_application(workspace["id"], "App", idempotency_key="a")
    connection = registry.create_connection(workspace["id"], application["id"], "GitHub",
        idempotency_key="c", credential_ref="env://S9_OBSERVED_GITHUB")
    binding = registry.create_scope_binding(workspace["id"], application["id"], "prod", connection["id"],
        "github_repository", "acme/service", idempotency_key="b")
    checked = registry.record_connection_check(workspace["id"], application["id"], connection["id"],
        binding["id"], expected_connection_revision=1, expected_binding_revision=1,
        idempotency_key="verify", request_parameters={"environment_id": "prod"}, check={
            "checked_at": "2026-09-23T09:00:00Z", "outcome": "data",
            "connection_status": "connected", "binding_status": "confirmed", "http_status": 200,
            "observed_count": 3, "watermark": None, "coverage": {"complete": True},
            "scope_confirmed": True, "detail": "fixture scope confirmed",
        })
    app.state.ids = (workspace["id"], application["id"],
                     checked["connection"]["id"], checked["scope_binding"]["id"])
    return TestClient(app)


def _url(client):
    workspace, application, connection, binding = client.app.state.ids
    return (f"/api/v1/workspaces/{workspace}/applications/{application}/connections/{connection}"
            f"/scope-bindings/{binding}/signals/import/github")


def _body(**overrides):
    return {
        "environment_id": "prod",
        "from_updated_at": "2026-09-22T00:00:00Z",
        "to_updated_at": "2026-09-23T00:00:00Z",
        "page_size": 2,
        "max_pages": 1,
        **overrides,
    }


def test_github_issue_and_pr_import_resumes_and_redacts_user_content(tmp_path, monkeypatch):
    import s9.product.v1_api as api_module

    client = _client(tmp_path)
    calls = []

    async def read_page(connection, binding, credentials, *, updated_since, page, per_page):
        calls.append(page)
        assert credentials.token == "ghp-fixture-only"
        assert binding["external_resource_id"] == "acme/service"
        assert updated_since == "2026-09-22T00:00:00Z"
        assert per_page == 2
        if page == 1:
            return {"http_status": 200, "availability": "data", "has_next": True, "rows": [
                {"number": 7, "updated_at": "2026-09-22T01:00:00Z", "title": "private issue title", "body": "secret report"},
                {"number": 8, "updated_at": "2026-09-22T02:00:00Z", "pull_request": {"url": "private"}, "title": "private PR title"},
            ]}
        assert page == 2
        return {"http_status": 200, "availability": "data", "has_next": False, "rows": [
            {"number": 9, "updated_at": "2026-09-22T03:00:00Z", "title": "also private"},
        ]}

    monkeypatch.setattr(api_module, "read_github_issues", read_page)
    first = client.post(_url(client), headers={"Idempotency-Key": "github-import-page-1"}, json=_body())
    assert first.status_code == 201
    page_one = first.json()
    assert page_one["created_count"] == 2
    assert page_one["coverage"]["complete"] is False
    assert page_one["coverage"]["next_cursor"] == "page:2"
    assert page_one["coverage"]["consistency"] == "github_updated_at_ascending_best_effort"
    assert page_one["items"][0]["signal_type"] == "github_issue"
    assert page_one["items"][1]["signal_type"] == "github_pull_request"
    assert "private issue title" not in first.text
    assert "private PR title" not in first.text
    assert "secret report" not in first.text

    second_body = _body(cursor="page:2")
    second = client.post(_url(client), headers={"Idempotency-Key": "github-import-page-2"}, json=second_body)
    assert second.status_code == 201
    page_two = second.json()
    assert page_two["created_count"] == 1
    assert page_two["coverage"]["complete"] is True
    assert page_two["coverage"]["next_cursor"] is None
    assert page_two["checkpoint"]["revision"] == 2

    replay = client.post(_url(client), headers={"Idempotency-Key": "github-import-page-2"}, json=second_body)
    assert replay.json() == page_two
    assert calls == [1, 2]
    workspace, application, _, binding = client.app.state.ids
    checkpoint = client.get(
        f"/api/v1/workspaces/{workspace}/applications/{application}/scope-bindings/{binding}/signals/import-state",
        params={"environment_id": "prod", "from_start_time": "2026-09-22T00:00:00Z",
                "to_start_time": "2026-09-23T00:00:00Z"},
    ).json()["checkpoint"]
    assert checkpoint["revision"] == 2
    assert checkpoint["complete"] is True
    signals = client.get(
        f"/api/v1/workspaces/{workspace}/applications/{application}/signals",
        params={"environment_id": "prod"},
    ).json()["items"]
    assert len(signals) == 3
    assert all(item["status"] == "new" for item in signals)


def test_github_import_rejects_unverified_scope_and_unbounded_window(tmp_path):
    client = _client(tmp_path)
    path = _url(client)
    invalid_window = client.post(path, headers={"Idempotency-Key": "bad-window"}, json=_body(
        from_updated_at="2026-01-01T00:00:00Z", to_updated_at="2026-03-01T00:00:00Z",
    ))
    assert invalid_window.status_code == 422
    assert invalid_window.json()["error"]["code"] == "INVALID_REQUEST"

    workspace, application, _, binding = client.app.state.ids
    with client.app.state.core.product.registry.tx() as db:
        import json
        record = json.loads(db.execute("SELECT data FROM scope_bindings WHERE id=?", (binding,)).fetchone()[0])
        record["status"] = "pending"
        db.execute("UPDATE scope_bindings SET data=? WHERE id=?", (json.dumps(record), binding))
    unverified = client.post(path, headers={"Idempotency-Key": "unverified"}, json=_body())
    assert unverified.status_code == 409
    assert unverified.json()["error"]["code"] == "SOURCE_SCOPE_UNVERIFIED"
