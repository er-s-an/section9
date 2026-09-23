from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient

from s9.product.registry import ProductError, ProductRegistry
from s9.product.v1_api import product_error_response, router, validation_error_response


def _client(tmp_path):
    app = FastAPI()
    app.include_router(router)
    registry = ProductRegistry(tmp_path / "product.sqlite")
    app.state.core = SimpleNamespace(product=SimpleNamespace(registry=registry))
    app.add_exception_handler(ProductError, product_error_response)
    app.add_exception_handler(RequestValidationError, validation_error_response)
    workspace = registry.create_workspace("W", idempotency_key="w")
    application = registry.create_application(workspace["id"], "App", idempotency_key="a")
    connection = registry.create_connection(workspace["id"], application["id"], "Manual intake", idempotency_key="c")
    binding = registry.create_scope_binding(workspace["id"], application["id"], "prod", connection["id"],
        "manual_source", "operator-reports", idempotency_key="b")
    signal = registry.ingest_signals(workspace["id"], application["id"], "prod", binding["id"], [{
        "source_id": "report-1", "source_version": "v1", "deduplication_key": "report-1",
        "signal_type": "manual_observation", "source_kind": "manual",
        "occurred_at": "2026-09-22T09:00:00Z", "observed_at": "2026-09-22T10:00:00Z",
        "summary": "Checkout failed after the latest release",
    }], idempotency_key="signal")["items"][0]
    incident = registry.create_incident(workspace["id"], application["id"], "prod", "Checkout failure", "high",
        [{"signal_id": signal["id"], "expected_revision": 1}], idempotency_key="incident")
    app.state.ids = (workspace["id"], application["id"], incident["id"], signal["id"])
    return TestClient(app)


def _root(client):
    workspace, application, _, _ = client.app.state.ids
    return f"/api/v1/workspaces/{workspace}/applications/{application}"


def test_manual_investigation_freezes_evidence_and_keeps_run_incident_lifecycles_separate(tmp_path):
    client = _client(tmp_path)
    root = _root(client)
    _, _, incident_id, _ = client.app.state.ids
    create_path = f"{root}/incidents/{incident_id}/investigation-runs/manual"
    body = {"environment_id": "prod", "expected_incident_revision": 1}
    created = client.post(create_path, headers={"Idempotency-Key": "run-1"}, json=body)
    assert created.status_code == 201, created.text
    detail = created.json()
    run = detail["run"]
    assert run["execution_mode"] == "manual"
    assert run["state"] == "running"
    assert len(run["input_snapshot_sha256"]) == 64
    assert detail["input_snapshot"]["incident"]["id"] == incident_id
    assert detail["input_snapshot"]["signals"][0]["summary"] == "Checkout failed after the latest release"
    assert len(detail["evidence"]) == 1
    evidence = detail["evidence"][0]
    assert evidence["origin"].endswith(detail["input_snapshot"]["signals"][0]["id"])
    assert evidence["coverage_complete"] is False

    replay = client.post(create_path, headers={"Idempotency-Key": "run-1"}, json=body)
    assert replay.json() == detail
    concurrent = client.post(create_path, headers={"Idempotency-Key": "run-2"}, json={
        "environment_id": "prod", "expected_incident_revision": 2,
    })
    assert concurrent.status_code == 409
    assert concurrent.json()["error"]["code"] == "ACTIVE_INVESTIGATION_EXISTS"

    hypothesis_url = f"{root}/investigation-runs/{run['id']}/hypotheses"
    hypothesis_body = {
        "environment_id": "prod", "expected_run_revision": 1,
        "statement": "The latest release introduced a checkout regression.", "confidence": 0.7,
        "support_evidence_ids": [evidence["id"]], "counterevidence_ids": [],
    }
    hypothesis_response = client.post(hypothesis_url,
        headers={"Idempotency-Key": "hypothesis-1"}, json=hypothesis_body)
    assert hypothesis_response.status_code == 201
    hypothesis = hypothesis_response.json()
    assert hypothesis["state"] == "proposed"
    assert hypothesis["support_evidence_ids"] == [evidence["id"]]

    invalid_refs = client.post(hypothesis_url, headers={"Idempotency-Key": "hypothesis-bad-ref"}, json={
        **hypothesis_body, "support_evidence_ids": ["evidence-from-another-run"],
    })
    assert invalid_refs.status_code == 404
    assert invalid_refs.json()["error"]["code"] == "EVIDENCE_NOT_FOUND"

    finish_url = f"{root}/investigation-runs/{run['id']}/finish"
    finish_body = {
        "environment_id": "prod", "expected_revision": 1,
        "result_type": "root_cause_identified", "reason": "人工复核支持该假设",
    }
    premature_finish = client.post(finish_url, headers={"Idempotency-Key": "finish-too-early"}, json=finish_body)
    assert premature_finish.status_code == 409
    assert premature_finish.json()["error"]["code"] == "SUPPORTED_HYPOTHESIS_REQUIRED"

    decision_url = f"{hypothesis_url}/{hypothesis['id']}/decision"
    decision_body = {
        "environment_id": "prod", "expected_revision": 1, "target_state": "supported",
        "confidence": 0.82, "support_evidence_ids": [evidence["id"]],
        "counterevidence_ids": [], "reason": "信号时间与发布窗口相符",
    }
    decision = client.post(decision_url, headers={"Idempotency-Key": "hypothesis-decision"}, json=decision_body)
    assert decision.status_code == 200
    assert decision.json()["state"] == "supported"
    assert decision.json()["revision"] == 2
    stale_decision = client.post(decision_url, headers={"Idempotency-Key": "hypothesis-stale"}, json=decision_body)
    assert stale_decision.status_code == 409
    assert stale_decision.json()["error"]["code"] == "STALE_HYPOTHESIS_REVISION"

    finished = client.post(finish_url, headers={"Idempotency-Key": "finish-run-1"}, json=finish_body)
    assert finished.status_code == 200
    assert finished.json()["state"] == "succeeded"
    assert finished.json()["result_type"] == "root_cause_identified"
    assert finished.json()["revision"] == 2
    after_finish = client.get(f"{root}/investigation-runs/{run['id']}", params={"environment_id": "prod"}).json()
    assert after_finish["run"] == finished.json()
    assert after_finish["hypotheses"][0]["state"] == "supported"

    new_run = client.post(create_path, headers={"Idempotency-Key": "run-2"}, json={
        "environment_id": "prod", "expected_incident_revision": 2,
    })
    assert new_run.status_code == 201
    assert new_run.json()["run"]["id"] != run["id"]
    no_root_cause = client.post(
        f"{root}/investigation-runs/{new_run.json()['run']['id']}/finish",
        headers={"Idempotency-Key": "finish-run-2"}, json={
            "environment_id": "prod", "expected_revision": 1,
            "result_type": "needs_data", "reason": "缺少发布前后对照数据",
        },
    )
    assert no_root_cause.status_code == 200
    assert no_root_cause.json()["state"] == "inconclusive"
    incident = client.get(f"{root}/incidents/{incident_id}", params={"environment_id": "prod"}).json()
    assert incident["state"] == "needs_input"
