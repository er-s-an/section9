from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient

from s9.product.registry import ProductError, ProductRegistry
from s9.product.v1_api import product_error_response, router, validation_error_response


def _client(tmp_path):
    app = FastAPI()
    app.include_router(router)
    app.state.core = SimpleNamespace(product=SimpleNamespace(registry=ProductRegistry(tmp_path / "product.sqlite")))
    app.add_exception_handler(ProductError, product_error_response)
    app.add_exception_handler(RequestValidationError, validation_error_response)
    return TestClient(app)


def test_api_ingests_deduplicated_signal_and_creates_incident(tmp_path):
    client = _client(tmp_path)
    workspace = client.post("/api/v1/workspaces", headers={"Idempotency-Key": "w"}, json={"name": "W"}).json()
    app_url = f"/api/v1/workspaces/{workspace['id']}/applications"
    application = client.post(app_url, headers={"Idempotency-Key": "a"}, json={"name": "App"}).json()
    root = f"{app_url}/{application['id']}"
    connection = client.post(f"{root}/connections", headers={"Idempotency-Key": "c"},
        json={"provider": "Manual intake"}).json()
    binding = client.post(f"{root}/scope-bindings", headers={"Idempotency-Key": "b"}, json={
        "environment_id": "prod", "connection_id": connection["id"],
        "resource_type": "manual_source", "external_resource_id": "operator-reports",
    }).json()
    batch = {
        "environment_id": "prod", "source_binding_id": binding["id"],
        "items": [{
            "source_id": "report-1", "source_version": "v1", "deduplication_key": "report-1",
            "signal_type": "manual_observation", "source_kind": "manual",
            "occurred_at": "2026-09-23T09:00:00Z", "observed_at": "2026-09-23T10:00:00Z",
            "summary": "Operator reported a failed request",
        }],
    }
    signal_url = f"{root}/signals"
    response = client.post(signal_url, headers={"Idempotency-Key": "signal-batch"}, json=batch)
    assert response.status_code == 201
    first = response.json()
    assert first["created_count"] == 1
    assert first["items"][0]["status"] == "new"
    assert client.post(signal_url, headers={"Idempotency-Key": "signal-batch"}, json=batch).json() == first
    duplicate = client.post(signal_url, headers={"Idempotency-Key": "signal-batch-retry"}, json=batch).json()
    assert duplicate["created_count"] == 0
    assert duplicate["duplicate_count"] == 1
    assert duplicate["items"][0]["id"] == first["items"][0]["id"]

    incident_body = {
        "environment_id": "prod", "title": "Investigate the failed request", "severity": "high",
        "signals": [{"signal_id": first["items"][0]["id"], "expected_revision": 1}],
    }
    incident_url = f"{root}/incidents"
    created = client.post(incident_url, headers={"Idempotency-Key": "incident-create"}, json=incident_body)
    assert created.status_code == 201
    incident = created.json()
    assert incident["state"] == "open"
    assert incident["signal_ids"] == [first["items"][0]["id"]]
    assert client.post(incident_url, headers={"Idempotency-Key": "incident-create"}, json=incident_body).json() == incident
    followup = {**batch, "items": [{**batch["items"][0], "source_id": "report-2", "deduplication_key": "report-2"}]}
    followup_result = client.post(signal_url, headers={"Idempotency-Key": "signal-followup"}, json=followup)
    assert followup_result.status_code == 201
    followup_signal = followup_result.json()["items"][0]
    attach_url = f"{root}/incidents/{incident['id']}/signals"
    attach_body = {
        "environment_id": "prod", "expected_incident_revision": incident["revision"],
        "reason": "后续报告指向同一失败范围",
        "signals": [{"signal_id": followup_signal["id"], "expected_revision": followup_signal["revision"]}],
    }
    attached = client.post(attach_url, headers={"Idempotency-Key": "incident-attach-followup"}, json=attach_body)
    assert attached.status_code == 200, attached.text
    incident = attached.json()
    assert incident["revision"] == 2
    assert incident["signal_ids"] == [first["items"][0]["id"], followup_signal["id"]]
    assert client.post(attach_url, headers={"Idempotency-Key": "incident-attach-followup"}, json=attach_body).json() == incident
    listed_signals = client.get(f"{root}/signals", params={"environment_id": "prod"}).json()["items"]
    listed_incidents = client.get(f"{root}/incidents", params={"environment_id": "prod"}).json()["items"]
    assert {item["id"]: item["status"] for item in listed_signals} == {
        first["items"][0]["id"]: "clustered", followup_signal["id"]: "clustered",
    }
    assert listed_incidents == [incident]

    wrong_scope = client.get(f"{root}/incidents/{incident['id']}", params={"environment_id": "staging"})
    assert wrong_scope.status_code == 404
    assert wrong_scope.json()["error"]["code"] == "INCIDENT_NOT_FOUND"

    transition_url = f"{root}/incidents/{incident['id']}/transitions"
    investigating_body = {
        "environment_id": "prod", "expected_revision": 2,
        "target_state": "investigating", "reason": "人工开始调查",
    }
    investigating = client.post(transition_url, headers={"Idempotency-Key": "incident-investigate"},
        json=investigating_body)
    assert investigating.status_code == 200, investigating.text
    assert investigating.json()["state"] == "investigating"
    assert investigating.json()["revision"] == 3
    assert client.post(transition_url, headers={"Idempotency-Key": "incident-investigate"},
        json=investigating_body).json() == investigating.json()

    stale = client.post(transition_url, headers={"Idempotency-Key": "incident-stale"}, json={
        **investigating_body, "target_state": "needs_input", "reason": "等待更多资料",
    })
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "STALE_INCIDENT_REVISION"

    needs_input = client.post(transition_url, headers={"Idempotency-Key": "incident-needs-input"}, json={
        "environment_id": "prod", "expected_revision": 3,
        "target_state": "needs_input", "reason": "等待服务负责人提供部署记录",
    })
    assert needs_input.status_code == 200
    reopened = client.post(transition_url, headers={"Idempotency-Key": "incident-reopen"}, json={
        "environment_id": "prod", "expected_revision": 4,
        "target_state": "open", "reason": "已收到部署记录，继续调查",
    })
    assert reopened.status_code == 200

    falsely_resolved = client.post(transition_url, headers={"Idempotency-Key": "incident-resolve"}, json={
        "environment_id": "prod", "expected_revision": 5,
        "target_state": "resolved", "reason": "请求关闭",
    })
    assert falsely_resolved.status_code == 409
    assert falsely_resolved.json()["error"]["code"] == "INDEPENDENT_VALIDATION_REQUIRED"
    dismissed = client.post(transition_url, headers={"Idempotency-Key": "incident-dismiss"}, json={
        "environment_id": "prod", "expected_revision": 5,
        "target_state": "dismissed", "reason": "经人工复核，该信号不是异常",
    })
    assert dismissed.status_code == 200
    assert dismissed.json()["state"] == "dismissed"
    assert dismissed.json()["outcome"] == "dismissed"
    events = client.get(f"/api/v1/workspaces/{workspace['id']}/events").json()["items"]
    assert [item["event_type"] for item in events].count("incident.state_changed") == 4


def test_signal_api_rejects_missing_idempotency_and_invalid_status(tmp_path):
    client = _client(tmp_path)
    response = client.post(
        "/api/v1/workspaces/wsp-missing/applications/app-missing/signals",
        json={"environment_id": "prod", "source_binding_id": "bind", "items": []},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_REQUEST"


def test_confirmed_poll_api_import_auto_links_only_exact_repeat_source(tmp_path):
    client = _client(tmp_path)
    workspace = client.post("/api/v1/workspaces", headers={"Idempotency-Key": "w"}, json={"name": "W"}).json()
    apps_url = f"/api/v1/workspaces/{workspace['id']}/applications"
    application = client.post(apps_url, headers={"Idempotency-Key": "a"}, json={"name": "App"}).json()
    root = f"{apps_url}/{application['id']}"
    connection = client.post(f"{root}/connections", headers={"Idempotency-Key": "c"}, json={
        "provider": "Langfuse", "credential_ref": "env://S9_OBSERVED_LANGFUSE",
    }).json()
    binding = client.post(f"{root}/scope-bindings", headers={"Idempotency-Key": "b"}, json={
        "environment_id": "prod", "connection_id": connection["id"],
        "resource_type": "langfuse_project", "external_resource_id": "project-test",
    }).json()
    client.app.state.core.product.registry.record_connection_check(
        workspace["id"], application["id"], connection["id"], binding["id"],
        expected_connection_revision=1, expected_binding_revision=1,
        idempotency_key="verify", request_parameters={"environment_id": "prod"},
        check={"checked_at": "2026-09-23T09:00:00Z", "outcome": "empty",
               "connection_status": "connected", "binding_status": "confirmed",
               "scope_confirmed": True, "http_status": 200, "coverage": {"complete": True}},
    )

    signal_url = f"{root}/signals"
    base_signal = {
        "source_id": "obs-api-repeat", "signal_type": "langfuse_observation", "source_kind": "poll",
        "occurred_at": "2026-09-23T09:00:00Z", "observed_at": "2026-09-23T10:00:00Z",
        "summary": "Neutral Langfuse observation",
    }
    first = client.post(signal_url, headers={"Idempotency-Key": "signal-v1"}, json={
        "environment_id": "prod", "source_binding_id": binding["id"],
        "items": [{**base_signal, "source_version": "v1", "deduplication_key": "obs-api-repeat:v1"}],
    }).json()
    created_incident = client.post(f"{root}/incidents", headers={"Idempotency-Key": "incident"}, json={
        "environment_id": "prod", "title": "Repeat object", "severity": "high",
        "signals": [{"signal_id": first["items"][0]["id"], "expected_revision": 1}],
    })
    assert created_incident.status_code == 201, created_incident.text
    incident = created_incident.json()

    second = client.post(signal_url, headers={"Idempotency-Key": "signal-v2"}, json={
        "environment_id": "prod", "source_binding_id": binding["id"],
        "items": [{**base_signal, "source_version": "v2", "deduplication_key": "obs-api-repeat:v2",
                   "observed_at": "2026-09-23T11:00:00Z"}],
    })

    assert second.status_code == 201, second.text
    imported = second.json()
    assert imported["created_count"] == 1
    assert imported["auto_linked_count"] == 1
    assert imported["correlation_ambiguous_count"] == 0
    assert imported["items"][0]["status"] == "clustered"
    current = client.get(f"{root}/incidents/{incident['id']}", params={"environment_id": "prod"}).json()
    assert current["revision"] == incident["revision"] + 1
    assert current["signal_ids"] == [first["items"][0]["id"], imported["items"][0]["id"]]


def test_api_splits_then_merges_incidents_with_idempotent_revisions(tmp_path):
    client = _client(tmp_path)
    workspace = client.post("/api/v1/workspaces", headers={"Idempotency-Key": "w"}, json={"name": "W"}).json()
    apps_url = f"/api/v1/workspaces/{workspace['id']}/applications"
    application = client.post(apps_url, headers={"Idempotency-Key": "a"}, json={"name": "App"}).json()
    root = f"{apps_url}/{application['id']}"
    connection = client.post(f"{root}/connections", headers={"Idempotency-Key": "c"},
        json={"provider": "Manual intake"}).json()
    binding = client.post(f"{root}/scope-bindings", headers={"Idempotency-Key": "b"}, json={
        "environment_id": "prod", "connection_id": connection["id"],
        "resource_type": "manual_source", "external_resource_id": "operator-reports",
    }).json()
    records = [{
        "source_id": f"report-{index}", "source_version": "v1", "deduplication_key": f"report-{index}",
        "signal_type": "manual_observation", "source_kind": "manual",
        "occurred_at": "2026-09-23T09:00:00Z", "observed_at": "2026-09-23T10:00:00Z",
        "summary": f"Operator report {index}",
    } for index in range(4)]
    batch = client.post(f"{root}/signals", headers={"Idempotency-Key": "batch"}, json={
        "environment_id": "prod", "source_binding_id": binding["id"], "items": records,
    }).json()
    signal_items = batch["items"]
    source = client.post(f"{root}/incidents", headers={"Idempotency-Key": "source"}, json={
        "environment_id": "prod", "title": "Source", "severity": "high",
        "signals": [{"signal_id": signal_items[0]["id"], "expected_revision": 1}],
    }).json()
    target = client.post(f"{root}/incidents", headers={"Idempotency-Key": "target"}, json={
        "environment_id": "prod", "title": "Target", "severity": "medium",
        "signals": [{"signal_id": item["id"], "expected_revision": 1} for item in signal_items[1:]],
    }).json()

    split_body = {
        "environment_id": "prod", "expected_incident_revision": target["revision"],
        "title": "Separate report", "severity": "low", "reason": "人工判断为独立问题",
        "signals": [{"signal_id": signal_items[1]["id"], "expected_revision": 2}],
    }
    split_url = f"{root}/incidents/{target['id']}/split"
    split = client.post(split_url, headers={"Idempotency-Key": "split"}, json=split_body)
    assert split.status_code == 200, split.text
    split_result = split.json()
    assert split_result["source"]["signal_ids"] == [signal_items[2]["id"], signal_items[3]["id"]]
    assert split_result["created"]["signal_ids"] == [signal_items[1]["id"]]
    assert client.post(split_url, headers={"Idempotency-Key": "split"}, json=split_body).json() == split_result

    merge_body = {
        "environment_id": "prod", "target_incident_id": source["id"],
        "expected_incident_revision": split_result["created"]["revision"],
        "expected_target_revision": source["revision"], "reason": "人工确认重复报告",
    }
    merge_url = f"{root}/incidents/{split_result['created']['id']}/merge"
    merged = client.post(merge_url, headers={"Idempotency-Key": "merge"}, json=merge_body)
    assert merged.status_code == 200, merged.text
    merged_result = merged.json()
    assert merged_result["source"]["state"] == "merged"
    assert merged_result["source"]["merged_into_id"] == source["id"]
    assert merged_result["target"]["signal_ids"] == [signal_items[0]["id"], signal_items[1]["id"]]
    assert client.post(merge_url, headers={"Idempotency-Key": "merge"}, json=merge_body).json() == merged_result

    stale = client.post(merge_url, headers={"Idempotency-Key": "merge-stale"}, json={
        **merge_body, "expected_incident_revision": merged_result["source"]["revision"],
        "expected_target_revision": 1,
    })
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "STALE_TARGET_INCIDENT_REVISION"
    events = client.get(f"/api/v1/workspaces/{workspace['id']}/events").json()["items"]
    event_types = [item["event_type"] for item in events]
    assert "incident.signals_split" in event_types
    assert "incident.created_by_split" in event_types
    assert "incident.merged" in event_types
    assert "incident.signals_merged" in event_types


def test_api_claims_releases_and_updates_incident_severity(tmp_path):
    client = _client(tmp_path)
    workspace = client.post("/api/v1/workspaces", headers={"Idempotency-Key": "w"}, json={"name": "W"}).json()
    apps_url = f"/api/v1/workspaces/{workspace['id']}/applications"
    application = client.post(apps_url, headers={"Idempotency-Key": "a"}, json={"name": "App"}).json()
    root = f"{apps_url}/{application['id']}"
    connection = client.post(f"{root}/connections", headers={"Idempotency-Key": "c"},
        json={"provider": "Manual intake"}).json()
    binding = client.post(f"{root}/scope-bindings", headers={"Idempotency-Key": "b"}, json={
        "environment_id": "prod", "connection_id": connection["id"],
        "resource_type": "manual_source", "external_resource_id": "operator-reports",
    }).json()
    signal = client.post(f"{root}/signals", headers={"Idempotency-Key": "signal"}, json={
        "environment_id": "prod", "source_binding_id": binding["id"], "items": [{
            "source_id": "report", "source_version": "v1", "deduplication_key": "report",
            "signal_type": "manual_observation", "source_kind": "manual",
            "occurred_at": "2026-09-23T09:00:00Z", "observed_at": "2026-09-23T10:00:00Z",
            "summary": "Operator report",
        }],
    }).json()["items"][0]
    incident = client.post(f"{root}/incidents", headers={"Idempotency-Key": "incident"}, json={
        "environment_id": "prod", "title": "Incident", "severity": "medium",
        "signals": [{"signal_id": signal["id"], "expected_revision": signal["revision"]}],
    }).json()

    assignment_url = f"{root}/incidents/{incident['id']}/assignment"
    claimed = client.post(assignment_url, headers={"Idempotency-Key": "claim"}, json={
        "environment_id": "prod", "expected_revision": 1, "action": "claim", "reason": "开始处理",
    })
    assert claimed.status_code == 200, claimed.text
    assert claimed.json()["assignee_id"] == "local-operator"
    severity = client.post(f"{root}/incidents/{incident['id']}/severity", headers={"Idempotency-Key": "severity"}, json={
        "environment_id": "prod", "expected_revision": 2, "severity": "high", "reason": "影响范围已确认",
    })
    assert severity.status_code == 200, severity.text
    assert severity.json()["revision"] == 3
    released = client.post(assignment_url, headers={"Idempotency-Key": "release"}, json={
        "environment_id": "prod", "expected_revision": 3, "action": "release", "reason": "交接给下一班",
    })
    assert released.status_code == 200, released.text
    assert released.json()["assignee_id"] is None
    events = client.get(f"/api/v1/workspaces/{workspace['id']}/events").json()["items"]
    event_types = [item["event_type"] for item in events]
    assert [name for name in event_types if name in {
        "incident.claimed", "incident.severity_changed", "incident.released",
    }] == ["incident.claimed", "incident.severity_changed", "incident.released"]
