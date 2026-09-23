from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient

from s9.product.credentials import ProductCredentialBroker
from s9.product.registry import ProductError, ProductRegistry
from s9.product.signal_monitor import SignalSyncScheduler
from s9.product.v1_api import product_error_response, router, validation_error_response


def _setup(tmp_path):
    registry = ProductRegistry(tmp_path / "product.sqlite")
    workspace = registry.create_workspace("Workspace", idempotency_key="workspace")
    application = registry.create_application(workspace["id"], "App", idempotency_key="application")
    connection = registry.create_connection(
        workspace["id"], application["id"], "Langfuse", idempotency_key="connection",
        credential_ref="env://S9_OBSERVED_LANGFUSE",
    )
    binding = registry.create_scope_binding(
        workspace["id"], application["id"], "production", connection["id"],
        "langfuse_project", "project-1", idempotency_key="binding",
    )
    return registry, workspace, application, connection, binding


def _confirm(registry, workspace, application, connection, binding):
    return registry.record_connection_check(
        workspace["id"], application["id"], connection["id"], binding["id"],
        expected_connection_revision=1, expected_binding_revision=1,
        idempotency_key="verify-binding", request_parameters={"environment_id": "production"},
        check={"checked_at": "2026-09-23T00:00:00Z", "outcome": "empty",
               "connection_status": "connected", "binding_status": "confirmed",
               "http_status": 200, "observed_count": 0, "watermark": None,
               "coverage": {"complete": True}, "scope_confirmed": True,
               "detail": "verified test binding"},
    )


def _enabled_monitor(registry, workspace, application, binding):
    return registry.configure_signal_sync_monitor(
        workspace["id"], application["id"], "production", binding["id"],
        enabled=True, interval_seconds=60, expected_revision=0, idempotency_key="monitor-enable",
    )


def test_monitor_requires_verified_scope_and_is_revision_checked(tmp_path):
    registry, workspace, application, connection, binding = _setup(tmp_path)
    with pytest.raises(ProductError, match="验证"):
        _enabled_monitor(registry, workspace, application, binding)

    checked = _confirm(registry, workspace, application, connection, binding)
    monitor = _enabled_monitor(registry, workspace, application, checked["scope_binding"])
    assert monitor["enabled"] is True
    assert monitor["revision"] == 1
    assert monitor["status"] == "starting"
    assert monitor["last_successful_watermark"] == monitor["enabled_at"]
    assert registry.configure_signal_sync_monitor(
        workspace["id"], application["id"], "production", binding["id"],
        enabled=True, interval_seconds=60, expected_revision=0, idempotency_key="monitor-enable",
    ) == monitor
    with pytest.raises(ProductError, match="已变化"):
        registry.configure_signal_sync_monitor(
            workspace["id"], application["id"], "production", binding["id"],
            enabled=False, interval_seconds=60, expected_revision=0, idempotency_key="pause-stale",
        )
    paused = registry.configure_signal_sync_monitor(
        workspace["id"], application["id"], "production", binding["id"],
        enabled=False, interval_seconds=60, expected_revision=1, idempotency_key="pause-monitor",
    )
    assert paused["enabled"] is False
    assert paused["status"] == "paused"
    assert registry.claim_due_signal_sync_monitors("worker") == []


def test_restarted_monitor_caps_catchup_window_and_records_gap(tmp_path):
    registry, workspace, application, connection, binding = _setup(tmp_path)
    checked = _confirm(registry, workspace, application, connection, binding)
    monitor = _enabled_monitor(registry, workspace, application, checked["scope_binding"])
    enabled_at = datetime.fromisoformat(monitor["enabled_at"])
    now = enabled_at + timedelta(days=5)
    timestamp = now.isoformat(timespec="milliseconds").replace("+00:00", "Z")

    claimed = registry.claim_due_signal_sync_monitors("restart-worker", now=timestamp)
    assert len(claimed) == 1
    window = claimed[0]["current_window"]
    gap = claimed[0]["coverage_gap"]
    window_from = datetime.fromisoformat(window["from"])
    window_to = datetime.fromisoformat(window["to"])
    assert window_to - window_from == timedelta(hours=24)
    assert datetime.fromisoformat(gap["from"]) == enabled_at
    assert datetime.fromisoformat(gap["to"]) == window_from


@pytest.mark.asyncio
async def test_scheduler_imports_verified_source_and_persists_auditable_run(tmp_path, monkeypatch):
    from s9.product import v1_api

    registry, workspace, application, connection, binding = _setup(tmp_path)
    checked = _confirm(registry, workspace, application, connection, binding)
    monitor = _enabled_monitor(registry, workspace, application, checked["scope_binding"])
    now = datetime.now(timezone.utc) + timedelta(seconds=61)
    page_cursors = []

    async def read_page(_connection, _binding, credentials, *, from_start_time, to_start_time, cursor, limit):
        assert credentials.project_id == "project-1"
        assert from_start_time == monitor["enabled_at"]
        assert datetime.fromisoformat(to_start_time.replace("Z", "+00:00")).tzinfo is not None
        assert limit == 100
        page_cursors.append(cursor)
        return {"http_status": 200, "availability": "data", "rows": [
            {"id": "observation-1", "timestamp": to_start_time, "type": "SPAN", "project_id": "project-1"},
        ], "coverage": {"complete": True, "next_cursor": None}}

    monkeypatch.setattr(v1_api, "read_langfuse_observations", read_page)
    broker = ProductCredentialBroker({
        "S9_OBSERVED_LANGFUSE_PUBLIC_KEY": "public-test",
        "S9_OBSERVED_LANGFUSE_SECRET_KEY": "secret-test",
        "S9_OBSERVED_LANGFUSE_PROJECT_ID": "project-1",
    })
    scheduler = SignalSyncScheduler(registry, broker, worker_id="test-worker", clock=lambda: now)
    assert await scheduler.run_once() == 1

    current = registry.list_signal_sync_monitors(workspace["id"], application["id"], "production")[0]
    signals = registry.list_signals(workspace["id"], application["id"], "production")
    runs = registry.list_signal_sync_runs(workspace["id"], application["id"], monitor["id"])
    events = registry.workspace_events(workspace["id"], after=0, limit=100)
    assert current["status"] == "healthy"
    assert current["enabled"] is True
    assert current["current_window"] is None
    assert current["last_run"]["created_count"] == 1
    assert current["last_run"]["auto_linked_count"] == 0
    assert current["last_run"]["correlation_ambiguous_count"] == 0
    assert len(signals) == 1 and signals[0]["source_kind"] == "poll"
    assert page_cursors == [None]
    assert len(runs) == 1 and runs[0]["state"] == "healthy"
    assert any(event["event_type"] == "signal.sync.healthy" for event in events)
    assert "secret-test" not in str(current)
    assert "observation-1" not in str(events)


@pytest.mark.asyncio
async def test_scheduler_backoffs_on_rate_limit_and_blocks_missing_credentials(tmp_path, monkeypatch):
    from s9.product import v1_api

    registry, workspace, application, connection, binding = _setup(tmp_path)
    checked = _confirm(registry, workspace, application, connection, binding)
    monitor = _enabled_monitor(registry, workspace, application, checked["scope_binding"])
    now = datetime.now(timezone.utc) + timedelta(seconds=61)

    async def rate_limited(*_args, **_kwargs):
        return {"http_status": 429, "availability": "rate_limited", "rows": [], "coverage": {"complete": False}}

    monkeypatch.setattr(v1_api, "read_langfuse_observations", rate_limited)
    credentials = ProductCredentialBroker({
        "S9_OBSERVED_LANGFUSE_PUBLIC_KEY": "public-test",
        "S9_OBSERVED_LANGFUSE_SECRET_KEY": "secret-test",
        "S9_OBSERVED_LANGFUSE_PROJECT_ID": "project-1",
    })
    scheduler = SignalSyncScheduler(registry, credentials, worker_id="rate-worker", clock=lambda: now)
    assert await scheduler.run_once() == 1
    current = registry.list_signal_sync_monitors(workspace["id"], application["id"], "production")[0]
    assert current["enabled"] is True
    assert current["status"] == "degraded"
    assert current["last_run"]["error_code"] == "SOURCE_RATE_LIMITED"
    assert current["current_window"]["from"] == monitor["enabled_at"]
    assert datetime.fromisoformat(current["next_run_at"].replace("Z", "+00:00")) > now

    paused = registry.configure_signal_sync_monitor(
        workspace["id"], application["id"], "production", binding["id"],
        enabled=False, interval_seconds=60, expected_revision=1, idempotency_key="stop-rate-monitor",
    )
    assert paused["enabled"] is False
    resumed = registry.configure_signal_sync_monitor(
        workspace["id"], application["id"], "production", binding["id"],
        enabled=True, interval_seconds=60, expected_revision=2, idempotency_key="resume-no-credentials",
    )
    missing = SignalSyncScheduler(registry, ProductCredentialBroker({}), worker_id="missing-worker", clock=lambda: now + timedelta(seconds=61))
    assert await missing.run_once() == 1
    current = registry.list_signal_sync_monitors(workspace["id"], application["id"], "production")[0]
    assert resumed["id"] == current["id"]
    assert current["enabled"] is False
    assert current["status"] == "blocked"
    assert current["last_run"]["error_code"] == "CREDENTIAL_NOT_CONFIGURED"


def test_monitor_api_exposes_configuration_and_run_history(tmp_path):
    registry, workspace, application, connection, binding = _setup(tmp_path)
    app = FastAPI()
    app.include_router(router)
    app.state.core = SimpleNamespace(product=SimpleNamespace(registry=registry))
    app.add_exception_handler(ProductError, product_error_response)
    app.add_exception_handler(RequestValidationError, validation_error_response)
    client = TestClient(app)
    _confirm(registry, workspace, application, connection, binding)
    path = (f"/api/v1/workspaces/{workspace['id']}/applications/{application['id']}"
            f"/scope-bindings/{binding['id']}/signal-monitor")
    response = client.put(path, headers={"Idempotency-Key": "api-monitor"}, json={
        "environment_id": "production", "enabled": True,
        "interval_seconds": 60, "expected_revision": 0,
    })
    assert response.status_code == 200
    monitor = response.json()
    assert monitor["enabled"] is True
    root = f"/api/v1/workspaces/{workspace['id']}/applications/{application['id']}"
    assert client.get(root + "/signal-monitors", params={"environment_id": "production"}).json()["items"] == [monitor]
    assert client.get(root + f"/signal-monitors/{monitor['id']}/runs",
                      params={"environment_id": "production"}).json()["items"] == []
    invalid = client.put(path, headers={"Idempotency-Key": "too-fast"}, json={
        "environment_id": "production", "enabled": True,
        "interval_seconds": 10, "expected_revision": monitor["revision"],
    })
    assert invalid.status_code == 422
