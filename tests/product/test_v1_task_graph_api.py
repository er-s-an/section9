from types import SimpleNamespace
from datetime import datetime, timedelta
import asyncio
import hashlib
import json

import httpx
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
import pytest

import s9.product.registry as registry_module
from s9.product.registry import ProductError, ProductRegistry
from s9.product.v1_api import product_error_response, router, validation_error_response
from s9.product.task_runtime import InvestigationTaskRuntime
from s9.product.v1_contracts import BudgetPolicy


def _client(tmp_path, *, two_sources=False, client_host="testclient", test_local_operator=True):
    app = FastAPI()
    app.include_router(router)
    app.state.product_test_local_operator = test_local_operator
    app.state.worker_tokens = {}
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
        "summary": "Checkout error rate rose after the latest release",
    }], idempotency_key="signal")["items"][0]
    signals = [signal]
    if two_sources:
        connection2 = registry.create_connection(workspace["id"], application["id"], "Second source", idempotency_key="c2")
        binding2 = registry.create_scope_binding(workspace["id"], application["id"], "prod", connection2["id"],
            "manual_source", "release-timeline", idempotency_key="b2")
        signals.extend(registry.ingest_signals(workspace["id"], application["id"], "prod", binding2["id"], [{
            "source_id": "report-2", "source_version": "v1", "deduplication_key": "report-2",
            "signal_type": "manual_observation", "source_kind": "manual",
            "occurred_at": "2026-09-22T08:00:00Z", "observed_at": "2026-09-22T10:00:00Z",
            "summary": "No comparable checkout errors were seen in the previous release window",
        }], idempotency_key="signal-2")["items"])
    incident = registry.create_incident(workspace["id"], application["id"], "prod", "Checkout failure", "high",
        [{"signal_id": item["id"], "expected_revision": 1} for item in signals], idempotency_key="incident")
    app.state.ids = (workspace["id"], application["id"], incident["id"])
    return TestClient(app, client=(client_host, 50000))


def _root(client):
    workspace, application, _ = client.app.state.ids
    return f"/api/v1/workspaces/{workspace}/applications/{application}"


def _worker_headers(client, worker_id):
    tokens = client.app.state.worker_tokens
    if worker_id not in tokens:
        workspace_id, application_id, _ = client.app.state.ids
        tokens[worker_id] = client.app.state.core.product.registry.create_investigation_worker(
            workspace_id, application_id, "prod", worker_id,
            ["evidence.investigate", "counterexample.prepare", "counterexample.review",
             "conclusion.synthesize"],
        )["token"]
    return {"Authorization": "Bearer " + tokens[worker_id]}


def _start_swarm(client):
    root = _root(client)
    _, _, incident_id = client.app.state.ids
    response = client.post(f"{root}/incidents/{incident_id}/investigation-runs",
        headers={"Idempotency-Key": "swarm-run"}, json={
            "environment_id": "prod", "expected_incident_revision": 1, "mode": "swarm",
        })
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["run"]["execution_mode"] == "swarm"
    return root, body["run"]["id"], body["task_graph"]


def _claim(client, root, run_id, task, worker_id, capability, *, revision=1, key=None):
    response = client.post(f"{root}/investigation-runs/{run_id}/tasks/{task['id']}/claim",
        headers={**_worker_headers(client, worker_id),
            "Idempotency-Key": key or f"claim-{task['task_key']}-{worker_id}"}, json={
            "environment_id": "prod", "expected_revision": revision,
            "worker_id": worker_id, "capabilities": [capability], "lease_seconds": 60,
        })
    return response


def _finish(client, root, run_id, task, worker_id, result, *, key):
    return client.post(f"{root}/investigation-runs/{run_id}/tasks/{task['id']}/finish",
        headers={**_worker_headers(client, worker_id), "Idempotency-Key": key}, json={
            "environment_id": "prod", "expected_revision": task["revision"],
            "worker_id": worker_id, "epoch": task["epoch"], "result": result,
        })


def test_worker_tokens_bind_identity_scope_and_server_capabilities_and_can_be_revoked(tmp_path):
    client = _client(tmp_path)
    root, run_id, graph = _start_swarm(client)
    workspace_id, application_id, _ = client.app.state.ids
    task = next(item for item in graph["tasks"] if item["role"] == "investigator")
    worker_url = f"/api/v1/workspaces/{workspace_id}/applications/{application_id}/workers"
    created = client.post(worker_url, json={"environment_id": "prod", "worker_id": "reader-1",
        "capabilities": ["evidence.investigate"]})
    assert created.status_code == 201, created.text
    assert created.headers["cache-control"] == "no-store"
    token = created.json()["token"]
    assert token.startswith("s9w_")
    assert created.json()["worker"]["capabilities"] == ["evidence.investigate"]

    listed = client.get(worker_url, params={"environment_id": "prod"})
    assert listed.status_code == 200
    assert listed.json()["items"][0]["worker_id"] == "reader-1"
    assert token not in json.dumps(listed.json())
    with client.app.state.core.product.registry.tx() as db:
        stored = db.execute("SELECT token_sha256,data FROM investigation_workers").fetchone()
    assert stored["token_sha256"] == hashlib.sha256(token.encode()).hexdigest()
    assert token not in stored["data"]

    claim_url = f"{root}/investigation-runs/{run_id}/tasks/{task['id']}/claim"
    body = {"environment_id": "prod", "expected_revision": task["revision"],
        "worker_id": "reader-1", "capabilities": ["evidence.investigate"], "lease_seconds": 60}
    missing_auth = client.post(claim_url, headers={"Idempotency-Key": "no-worker-auth"}, json=body)
    assert (missing_auth.status_code, missing_auth.json()["error"]["code"]) == (401, "WORKER_CREDENTIAL_REQUIRED")
    forged_name = client.post(claim_url, headers={"Authorization": "Bearer " + token,
        "Idempotency-Key": "wrong-worker-name"}, json={**body, "worker_id": "forged-reader"})
    assert (forged_name.status_code, forged_name.json()["error"]["code"]) == (403, "WORKER_IDENTITY_MISMATCH")
    forged_capability = client.post(claim_url, headers={"Authorization": "Bearer " + token,
        "Idempotency-Key": "wrong-capability"}, json={**body, "capabilities": ["counterexample.prepare"]})
    assert (forged_capability.status_code, forged_capability.json()["error"]["code"]) == (403, "TASK_CAPABILITY_REQUIRED")
    accepted = client.post(claim_url, headers={"Authorization": "Bearer " + token,
        "Idempotency-Key": "correct-worker-claim"}, json=body)
    assert accepted.status_code == 200, accepted.text
    claimed = accepted.json()

    context_url = f"{root}/investigation-runs/{run_id}/tasks/{task['id']}/context"
    wrong_scope = client.get(context_url, headers={"Authorization": "Bearer " + token},
        params={"environment_id": "staging", "worker_id": "reader-1", "epoch": claimed["epoch"]})
    assert (wrong_scope.status_code, wrong_scope.json()["error"]["code"]) == (403, "WORKER_SCOPE_MISMATCH")
    revoke_url = worker_url + "/reader-1/revoke"
    revoked = client.post(revoke_url, params={"environment_id": "prod"})
    assert revoked.status_code == 200 and revoked.json()["state"] == "revoked"
    revoked_context = client.get(context_url, headers={"Authorization": "Bearer " + token},
        params={"environment_id": "prod", "worker_id": "reader-1", "epoch": claimed["epoch"]})
    assert (revoked_context.status_code, revoked_context.json()["error"]["code"]) == (401, "WORKER_CREDENTIAL_INVALID")

    rotated = client.post(worker_url, json={"environment_id": "prod", "worker_id": "reader-1",
        "capabilities": ["evidence.investigate"]})
    assert rotated.status_code == 201, rotated.text
    new_token = rotated.json()["token"]
    assert new_token != token
    resumed_context = client.get(context_url, headers={"Authorization": "Bearer " + new_token},
        params={"environment_id": "prod", "worker_id": "reader-1", "epoch": claimed["epoch"]})
    assert resumed_context.status_code == 200, resumed_context.text


def test_worker_management_rejects_non_loopback_clients(tmp_path):
    client = _client(tmp_path, client_host="198.51.100.17", test_local_operator=False)
    workspace_id, application_id, _ = client.app.state.ids
    response = client.post(
        f"/api/v1/workspaces/{workspace_id}/applications/{application_id}/workers",
        json={"environment_id": "prod", "worker_id": "remote-admin",
            "capabilities": ["evidence.investigate"]},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "LOCAL_OPERATOR_REQUIRED"


def test_task_graph_migration_is_additive_and_repeatable(tmp_path):
    path = tmp_path / "product.sqlite"
    first = ProductRegistry(path)
    with first.tx() as db:
        db.execute("DROP TABLE investigation_tasks")
        db.execute("DROP TABLE investigation_task_graphs")
        db.execute("DELETE FROM product_schema_migrations WHERE version IN (6,7)")
    second = ProductRegistry(path)
    with second.tx() as db:
        assert db.execute("SELECT MAX(version) FROM product_schema_migrations").fetchone()[0] == 8
        assert db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='investigation_tasks'").fetchone()
        assert db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='investigation_task_graphs'").fetchone()


def test_swarm_plan_enforces_blind_challenger_then_review_and_cites_same_run_evidence(tmp_path):
    client = _client(tmp_path)
    root, run_id, graph = _start_swarm(client)
    assert graph["mode"] == "swarm"
    by_role = {task["role"]: task for task in graph["tasks"]}
    investigator, seed = by_role["investigator"], by_role["challenger_seed"]
    review, synthesizer = by_role["challenger_review"], by_role["synthesizer"]
    assert seed["dependencies"] == []
    assert set(review["dependencies"]) == {investigator["id"], seed["id"]}
    assert set(synthesizer["dependencies"]) == {investigator["id"], review["id"]}
    evidence_id = seed["input_evidence_ids"][0]

    missing_capability = _claim(client, root, run_id, investigator, "worker-i", "counterexample.prepare")
    assert missing_capability.status_code == 403
    assert missing_capability.json()["error"]["code"] == "TASK_CAPABILITY_REQUIRED"

    early_review = _claim(client, root, run_id, review, "worker-r", "counterexample.review")
    assert early_review.status_code == 409
    assert early_review.json()["error"]["code"] == "TASK_DEPENDENCIES_PENDING"

    seed_claim = _claim(client, root, run_id, seed, "worker-c", "counterexample.prepare")
    assert seed_claim.status_code == 200, seed_claim.text
    seed_task = seed_claim.json()
    seed_context = client.get(f"{root}/investigation-runs/{run_id}/tasks/{seed['id']}/context",
        headers=_worker_headers(client, "worker-c"),
        params={"environment_id": "prod", "worker_id": "worker-c", "epoch": seed_task["epoch"]})
    assert seed_context.status_code == 200
    assert seed_context.json()["dependency_results"] == []
    assert [item["id"] for item in seed_context.json()["input_evidence"]] == [evidence_id]
    seed_result = {"summary": "先从输入信号列出可证伪条件。", "evidence_ids": [evidence_id],
        "challenge_questions": [{"question": "错误是否在发布前已经出现？", "evidence_ids": [evidence_id],
            "failure_condition": "若发布前同样出现，则该发布因果假设不成立。"}]}
    seed_done = _finish(client, root, run_id, seed_task, "worker-c", seed_result, key="seed-done")
    assert seed_done.status_code == 200, seed_done.text
    assert seed_done.json()["task"]["state"] == "succeeded"

    investigator_claim = _claim(client, root, run_id, investigator, "worker-i", "evidence.investigate")
    assert investigator_claim.status_code == 200, investigator_claim.text
    investigator_task = investigator_claim.json()
    candidate = {"summary": "错误与发布后的时间窗相关。", "evidence_ids": [evidence_id],
        "hypotheses": [{"statement": "最近发布引入了结算回归。", "confidence": 0.72,
            "support_evidence_ids": [evidence_id], "counterevidence_ids": []}]}
    candidate_done = _finish(client, root, run_id, investigator_task, "worker-i", candidate, key="investigator-done")
    assert candidate_done.status_code == 200, candidate_done.text

    review_claim = _claim(client, root, run_id, review, "worker-r", "counterexample.review")
    assert review_claim.status_code == 200, review_claim.text
    review_task = review_claim.json()
    review_context = client.get(f"{root}/investigation-runs/{run_id}/tasks/{review['id']}/context",
        headers=_worker_headers(client, "worker-r"),
        params={"environment_id": "prod", "worker_id": "worker-r", "epoch": review_task["epoch"]})
    assert review_context.status_code == 200
    assert {item["role"] for item in review_context.json()["dependency_results"]} == {"investigator", "challenger_seed"}
    review_done = _finish(client, root, run_id, review_task,
        "worker-r", {"summary": "反例条件尚未被当前证据排除。", "evidence_ids": [evidence_id],
            "review_verdict": "insufficient"}, key="review-done")
    assert review_done.status_code == 200, review_done.text

    synth_claim = _claim(client, root, run_id, synthesizer, "worker-s", "conclusion.synthesize")
    assert synth_claim.status_code == 200, synth_claim.text
    synth_task = synth_claim.json()
    synth_done = _finish(client, root, run_id, synth_task, "worker-s",
        {"summary": "证据不足以排除发布前既有故障，暂不确认根因。", "evidence_ids": [evidence_id],
            "conclusion": "needs_data"}, key="synth-done")
    assert synth_done.status_code == 200, synth_done.text
    assert synth_done.json()["task_graph"]["state"] == "complete"
    detail = client.get(f"{root}/investigation-runs/{run_id}", params={"environment_id": "prod"}).json()
    assert len(detail["hypotheses"]) == 1
    assert detail["hypotheses"][0]["source_task_id"] == investigator["id"]
    assert detail["hypotheses"][0]["source_task_epoch"] == investigator_task["epoch"]
    # A completed graph is not a recovered incident or a human-approved root cause.
    assert detail["run"]["state"] == "running"
    assert detail["task_graph"]["state"] == "complete"


def test_swarm_investigator_context_is_partitioned_by_verified_source_binding(tmp_path):
    client = _client(tmp_path, two_sources=True)
    root, run_id, graph = _start_swarm(client)
    investigators = [item for item in graph["tasks"] if item["role"] == "investigator"]
    seed = next(item for item in graph["tasks"] if item["role"] == "challenger_seed")
    assert len(investigators) == 2
    assert len(seed["input_evidence_ids"]) == 2
    claim = _claim(client, root, run_id, investigators[0], "worker-i", "evidence.investigate")
    assert claim.status_code == 200, claim.text
    context = client.get(f"{root}/investigation-runs/{run_id}/tasks/{investigators[0]['id']}/context",
        headers=_worker_headers(client, "worker-i"),
        params={"environment_id": "prod", "worker_id": "worker-i", "epoch": claim.json()["epoch"]})
    assert context.status_code == 200
    assert len(context.json()["input_evidence"]) == 1
    assert len(context.json()["input_snapshot"]["signals"]) == 1
    assert len(context.json()["input_snapshot"]["signals"]) < len(seed["input_evidence_ids"])


def test_single_investigator_receives_the_full_frozen_evidence_baseline(tmp_path):
    client = _client(tmp_path, two_sources=True)
    root = _root(client)
    _, _, incident_id = client.app.state.ids
    response = client.post(f"{root}/incidents/{incident_id}/investigation-runs",
        headers={"Idempotency-Key": "single-run"}, json={
            "environment_id": "prod", "expected_incident_revision": 1, "mode": "single",
        })
    assert response.status_code == 201, response.text
    body = response.json()
    tasks = body["task_graph"]["tasks"]
    investigator = next(item for item in tasks if item["role"] == "investigator")
    assert len([item for item in tasks if item["role"] == "investigator"]) == 1
    assert not any(item["role"].startswith("challenger") for item in tasks)
    assert len(investigator["input_evidence_ids"]) == 2


def test_failed_task_retry_advances_epoch_and_fences_old_worker(tmp_path):
    client = _client(tmp_path)
    root, run_id, graph = _start_swarm(client)
    task = next(item for item in graph["tasks"] if item["role"] == "investigator")
    claimed = _claim(client, root, run_id, task, "worker-old", "evidence.investigate")
    assert claimed.status_code == 200
    task = claimed.json()
    failed = client.post(f"{root}/investigation-runs/{run_id}/tasks/{task['id']}/finish",
        headers={**_worker_headers(client, "worker-old"), "Idempotency-Key": "task-failed"}, json={
            "environment_id": "prod", "expected_revision": task["revision"],
            "worker_id": "worker-old", "epoch": task["epoch"], "failure_reason": "读取工具超时",
        })
    assert failed.status_code == 200, failed.text
    task = failed.json()["task"]
    retry = client.post(f"{root}/investigation-runs/{run_id}/tasks/{task['id']}/retry",
        headers={"Idempotency-Key": "task-retry"}, json={
            "environment_id": "prod", "expected_revision": task["revision"], "reason": "连接恢复后重新读取",
        })
    assert retry.status_code == 200, retry.text
    task = retry.json()["task"]
    assert task["state"] == "open"
    assert task["attempt_history"][0]["epoch"] == 1
    reclaimed = _claim(client, root, run_id, task, "worker-new", "evidence.investigate",
        revision=task["revision"], key="task-claim-new")
    assert reclaimed.status_code == 200, reclaimed.text
    assert reclaimed.json()["epoch"] == 2
    stale = client.post(f"{root}/investigation-runs/{run_id}/tasks/{task['id']}/finish",
        headers={**_worker_headers(client, "worker-old"), "Idempotency-Key": "late-old-result"}, json={
            "environment_id": "prod", "expected_revision": reclaimed.json()["revision"],
            "worker_id": "worker-old", "epoch": 1,
            "result": {"summary": "迟到结果", "hypotheses": [{"statement": "无效结论", "confidence": 0.5,
                "support_evidence_ids": ["unrelated-evidence"], "counterevidence_ids": []}]},
        })
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "TASK_LEASE_FENCED"


def test_model_runtime_executes_swarm_and_records_provider_usage(tmp_path):
    client = _client(tmp_path)
    registry = client.app.state.core.product.registry
    workspace_id, application_id, _ = client.app.state.ids
    registry.update_workspace_budget(workspace_id, BudgetPolicy(token_limit=50_000),
        expected_revision=1, idempotency_key="budget")
    root, run_id, _ = _start_swarm(client)
    seen_roles = []

    def provider(request):
        payload = json.loads(request.content)
        user = json.loads(payload["messages"][1]["content"])
        role = user["task"]["role"]
        seen_roles.append(role)
        evidence_ids = [item["id"] for item in user["context"]["input_evidence"]]
        assert evidence_ids
        evidence_id = evidence_ids[0]
        if role == "investigator":
            result = {"summary": "信号显示问题在近期发布后出现，仍需更多时间序列验证。",
                "evidence_ids": [evidence_id], "hypotheses": [{"statement": "近期发布可能引入结算回归。",
                    "confidence": 0.6, "support_evidence_ids": [evidence_id], "counterevidence_ids": []}]}
        elif role == "challenger_seed":
            result = {"summary": "先检查发布前是否已有相同错误。", "evidence_ids": [evidence_id],
                "challenge_questions": [{"question": "错误是否在发布前已出现？", "evidence_ids": [evidence_id],
                    "failure_condition": "若發布前已存在，近期发布假设不成立。"}]}
        elif role == "challenger_review":
            result = {"summary": "当前证据不足以排除反例。", "evidence_ids": [evidence_id],
                "review_verdict": "insufficient"}
        else:
            result = {"summary": "先补充发布前后对照数据。", "evidence_ids": [evidence_id],
                "conclusion": "needs_data"}
        return httpx.Response(200, json={"model": "mock-model", "choices": [{
            "message": {"content": json.dumps(result, ensure_ascii=False)},
        }], "usage": {"prompt_tokens": 21, "completion_tokens": 9, "total_tokens": 30}})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as provider_client:
            client.app.state.product_task_runtime = InvestigationTaskRuntime(
                registry, provider_client, model_url="https://model.example/v1/chat/completions",
                model_key="test-only-secret", model="mock-model", timeout=2,
            )
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=client.app),
                                         base_url="http://testserver") as api_client:
                response = await api_client.post(
                    f"{root}/investigation-runs/{run_id}/execute",
                    json={"environment_id": "prod"},
                )
                assert response.status_code == 200, response.text
                return response.json()

    body = asyncio.run(run())
    assert body["execution"]["state"] == "complete"
    assert body["execution"]["completed"] == 4
    assert set(seen_roles) == {"investigator", "challenger_seed", "challenger_review", "synthesizer"}
    detail = body["detail"]
    assert detail["task_graph"]["state"] == "complete"
    assert all(task["state"] == "succeeded" for task in detail["task_graph"]["tasks"])
    assert len(detail["model_usage"]) == 4
    assert all(row["state"] == "settled" and row["actual_tokens"] == 30 for row in detail["model_usage"])
    assert len(detail["hypotheses"]) == 1
    assert "test-only-secret" not in json.dumps(detail)


def test_model_runtime_fails_closed_before_provider_when_budget_is_too_small(tmp_path):
    client = _client(tmp_path)
    registry = client.app.state.core.product.registry
    workspace_id, _, _ = client.app.state.ids
    registry.update_workspace_budget(workspace_id, BudgetPolicy(token_limit=1),
        expected_revision=1, idempotency_key="budget-small")
    root, run_id, _ = _start_swarm(client)
    provider_calls = 0

    def provider(request):
        nonlocal provider_calls
        provider_calls += 1
        return httpx.Response(500)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as provider_client:
            client.app.state.product_task_runtime = InvestigationTaskRuntime(
                registry, provider_client, model_url="https://model.example/chat/completions",
                model_key="test-only-secret", model="mock-model", timeout=2,
            )
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=client.app),
                                         base_url="http://testserver") as api_client:
                response = await api_client.post(
                    f"{root}/investigation-runs/{run_id}/execute",
                    json={"environment_id": "prod"},
                )
                assert response.status_code == 200, response.text
                return response.json()

    body = asyncio.run(run())
    assert provider_calls == 0
    assert body["execution"]["state"] == "blocked"
    assert body["execution"]["error_code"] == "TOKEN_BUDGET_EXHAUSTED"
    assert body["detail"]["task_graph"]["state"] == "ready"
    assert all(task["state"] == "open" for task in body["detail"]["task_graph"]["tasks"])
    assert body["detail"]["model_usage"] == []


def test_model_runtime_keeps_unknown_provider_usage_reserved(tmp_path):
    client = _client(tmp_path)
    registry = client.app.state.core.product.registry
    workspace_id, _, _ = client.app.state.ids
    registry.update_workspace_budget(workspace_id, BudgetPolicy(token_limit=50_000),
        expected_revision=1, idempotency_key="budget-unknown")
    root = _root(client)
    _, _, incident_id = client.app.state.ids
    created = client.post(f"{root}/incidents/{incident_id}/investigation-runs",
        headers={"Idempotency-Key": "single-unknown"}, json={
            "environment_id": "prod", "expected_incident_revision": 1, "mode": "single",
        })
    run_id = created.json()["run"]["id"]

    def provider(request):
        payload = json.loads(request.content)
        user = json.loads(payload["messages"][1]["content"])
        evidence_id = user["context"]["input_evidence"][0]["id"]
        role = user["task"]["role"]
        result = ({"summary": "保留当前假设，等待交叉验证。", "evidence_ids": [evidence_id],
                   "hypotheses": [{"statement": "近期发布可能导致回归。", "confidence": 0.5,
                       "support_evidence_ids": [evidence_id], "counterevidence_ids": []}]}
                  if role == "investigator" else
                  {"summary": "结论还需复核。", "evidence_ids": [evidence_id], "conclusion": "needs_data"})
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(result)}}]})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as provider_client:
            client.app.state.product_task_runtime = InvestigationTaskRuntime(
                registry, provider_client, model_url="https://model.example/chat/completions",
                model_key="test-only-secret", model="mock-model", timeout=2,
            )
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=client.app),
                                         base_url="http://testserver") as api_client:
                response = await api_client.post(
                    f"{root}/investigation-runs/{run_id}/execute",
                    json={"environment_id": "prod"},
                )
                assert response.status_code == 200, response.text
                return response.json()

    detail = asyncio.run(run())["detail"]
    assert detail["task_graph"]["state"] == "complete"
    assert len(detail["model_usage"]) == 2
    assert all(row["state"] == "outcome_unknown" and row["actual_tokens"] is None
               and row["reserved_tokens"] > 0 for row in detail["model_usage"])


def test_expired_lease_is_reassigned_with_new_epoch_and_old_attempt_is_recorded(tmp_path, monkeypatch):
    client = _client(tmp_path)
    root, run_id, graph = _start_swarm(client)
    task = next(item for item in graph["tasks"] if item["role"] == "investigator")
    first = _claim(client, root, run_id, task, "worker-old", "evidence.investigate").json()

    class FutureDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.now(tz) + timedelta(minutes=10)

    monkeypatch.setattr(registry_module, "datetime", FutureDateTime)
    second = _claim(client, root, run_id, first, "worker-new", "evidence.investigate",
        revision=first["revision"], key="claim-after-expiry")
    assert second.status_code == 200, second.text
    assert second.json()["epoch"] == 2
    assert second.json()["attempt_history"][0]["reason"] == "worker lease expired before completion"
    stale = client.post(f"{root}/investigation-runs/{run_id}/tasks/{task['id']}/finish",
        headers={**_worker_headers(client, "worker-old"), "Idempotency-Key": "expired-worker-result"}, json={
            "environment_id": "prod", "expected_revision": second.json()["revision"],
            "worker_id": "worker-old", "epoch": 1,
            "result": {"summary": "迟到结果", "hypotheses": [{"statement": "根因", "confidence": 0.7,
                "support_evidence_ids": [task["input_evidence_ids"][0]], "counterevidence_ids": []}]},
        })
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "TASK_LEASE_FENCED"


def test_interrupted_task_without_dispatch_is_reopened_after_lease_expiry(tmp_path, monkeypatch):
    client = _client(tmp_path)
    root, run_id, graph = _start_swarm(client)
    workspace_id, application_id, _ = client.app.state.ids
    task = next(item for item in graph["tasks"] if item["role"] == "investigator")
    first = _claim(client, root, run_id, task, "worker-old", "evidence.investigate").json()

    class FutureDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.now(tz) + timedelta(minutes=10)

    monkeypatch.setattr(registry_module, "datetime", FutureDateTime)
    recovered = client.app.state.core.product.registry.recover_expired_investigation_task_leases(
        workspace_id, application_id, "prod", run_id,
    )
    assert recovered == {"reopened": 1, "stopped_for_provider_outcome": 0}
    detail = client.get(f"{root}/investigation-runs/{run_id}", params={"environment_id": "prod"}).json()
    restored = next(item for item in detail["task_graph"]["tasks"] if item["id"] == task["id"])
    assert restored["state"] == "open"
    assert restored["holder_id"] is None and restored["epoch"] == first["epoch"]
    assert restored["attempt_history"][-1]["reason"] == "worker lease expired before provider dispatch; safe to resume"
    assert detail["model_usage"] == []
    second = _claim(client, root, run_id, restored, "worker-new", "evidence.investigate",
        revision=restored["revision"], key="safe-recovery-reclaim")
    assert second.status_code == 200, second.text
    assert second.json()["epoch"] == first["epoch"] + 1


def test_interrupted_after_dispatch_retains_unknown_usage_and_blocks_automatic_retry(tmp_path, monkeypatch):
    client = _client(tmp_path)
    registry = client.app.state.core.product.registry
    workspace_id, application_id, _ = client.app.state.ids
    registry.update_workspace_budget(workspace_id, BudgetPolicy(token_limit=50_000),
        expected_revision=1, idempotency_key="recovery-budget")
    root, run_id, graph = _start_swarm(client)
    task = next(item for item in graph["tasks"] if item["role"] == "investigator")
    claimed = _claim(client, root, run_id, task, "worker-old", "evidence.investigate").json()
    request_id = "request-interrupted-after-dispatch"
    registry.reserve_investigation_model_usage(workspace_id, application_id, "prod", run_id,
        task["id"], "worker-old", claimed["epoch"], request_id=request_id,
        provider="model.example", model="mock-model", prompt_sha256="a" * 64,
        reserved_tokens=1500)
    registry.authorize_investigation_model_dispatch(workspace_id, application_id, "prod", run_id,
        task["id"], "worker-old", claimed["epoch"], request_id=request_id)

    class FutureDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.now(tz) + timedelta(minutes=10)

    monkeypatch.setattr(registry_module, "datetime", FutureDateTime)
    recovered = registry.recover_expired_investigation_task_leases(
        workspace_id, application_id, "prod", run_id,
    )
    assert recovered == {"reopened": 0, "stopped_for_provider_outcome": 1}
    detail = client.get(f"{root}/investigation-runs/{run_id}", params={"environment_id": "prod"}).json()
    restored = next(item for item in detail["task_graph"]["tasks"] if item["id"] == task["id"])
    assert restored["state"] == "failed"
    assert "outcome is unknown" in restored["failure_reason"]
    usage = next(item for item in detail["model_usage"] if item["request_id"] == request_id)
    assert usage["state"] == "outcome_unknown" and usage["actual_tokens"] is None
    assert usage["reserved_tokens"] == 1500 and usage["provider_called"] == 1

    provider_calls = 0

    def provider(_request):
        nonlocal provider_calls
        provider_calls += 1
        return httpx.Response(500)

    async def resume_after_restart():
        async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as provider_client:
            runtime = InvestigationTaskRuntime(registry, provider_client,
                model_url="https://model.example/chat/completions", model_key="test-only-secret",
                model="mock-model", timeout=2)
            return await runtime.execute(workspace_id, application_id, "prod", run_id)

    resumed = asyncio.run(resume_after_restart())
    assert resumed["execution"]["state"] == "blocked"
    assert provider_calls == 0

    retry = client.post(f"{root}/investigation-runs/{run_id}/tasks/{task['id']}/retry",
        headers={"Idempotency-Key": "operator-explicit-unknown-retry"}, json={
            "environment_id": "prod", "expected_revision": restored["revision"],
            "reason": "操作者检查供应商账单后明确授权重新调查",
        })
    assert retry.status_code == 200, retry.text
    retried = retry.json()["task"]
    assert retried["state"] == "open"
    assert len(retried["attempt_history"]) == 1
    assert retried["attempt_history"][0]["epoch"] == claimed["epoch"]
    assert registry.get_investigation_run_detail(workspace_id, application_id, "prod", run_id)["model_usage"][0]["state"] == "outcome_unknown"


@pytest.mark.parametrize("phase", ["reserved", "settled"])
def test_interrupted_usage_before_or_after_provider_return_is_reconciled_truthfully(tmp_path, monkeypatch, phase):
    client = _client(tmp_path)
    registry = client.app.state.core.product.registry
    workspace_id, application_id, _ = client.app.state.ids
    registry.update_workspace_budget(workspace_id, BudgetPolicy(token_limit=50_000),
        expected_revision=1, idempotency_key=f"recovery-{phase}-budget")
    root, run_id, graph = _start_swarm(client)
    task = next(item for item in graph["tasks"] if item["role"] == "investigator")
    claimed = _claim(client, root, run_id, task, "worker-interrupted", "evidence.investigate").json()
    request_id = f"request-interrupted-{phase}"
    registry.reserve_investigation_model_usage(workspace_id, application_id, "prod", run_id,
        task["id"], "worker-interrupted", claimed["epoch"], request_id=request_id,
        provider="model.example", model="mock-model", prompt_sha256="b" * 64,
        reserved_tokens=800)
    if phase == "settled":
        registry.authorize_investigation_model_dispatch(workspace_id, application_id, "prod", run_id,
            task["id"], "worker-interrupted", claimed["epoch"], request_id=request_id)
        registry.settle_investigation_model_usage(workspace_id, application_id, request_id=request_id,
            actual_tokens=23, provider_called=True)

    class FutureDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.now(tz) + timedelta(minutes=10)

    monkeypatch.setattr(registry_module, "datetime", FutureDateTime)
    recovered = registry.recover_expired_investigation_task_leases(
        workspace_id, application_id, "prod", run_id,
    )
    detail = registry.get_investigation_run_detail(workspace_id, application_id, "prod", run_id)
    restored = next(item for item in detail["task_graph"]["tasks"] if item["id"] == task["id"])
    usage = next(item for item in detail["model_usage"] if item["request_id"] == request_id)
    if phase == "reserved":
        assert recovered == {"reopened": 1, "stopped_for_provider_outcome": 0}
        assert restored["state"] == "open" and restored["failure_reason"] is None
        assert usage["state"] == "settled" and usage["actual_tokens"] == 0
        assert usage["provider_called"] == 0
    else:
        assert recovered == {"reopened": 0, "stopped_for_provider_outcome": 1}
        assert restored["state"] == "failed"
        assert "explicit retry will make another provider request" in restored["failure_reason"]
        assert usage["state"] == "settled" and usage["actual_tokens"] == 23
        assert usage["provider_called"] == 1


def test_worker_lease_can_be_renewed_but_not_by_a_different_epoch(tmp_path):
    client = _client(tmp_path)
    root, run_id, graph = _start_swarm(client)
    task = next(item for item in graph["tasks"] if item["role"] == "investigator")
    claimed = _claim(client, root, run_id, task, "worker-i", "evidence.investigate").json()
    renewed = client.post(f"{root}/investigation-runs/{run_id}/tasks/{task['id']}/renew",
        headers={**_worker_headers(client, "worker-i"), "Idempotency-Key": "renew-task"}, json={
            "environment_id": "prod", "expected_revision": claimed["revision"],
            "worker_id": "worker-i", "epoch": claimed["epoch"], "lease_seconds": 120,
        })
    assert renewed.status_code == 200, renewed.text
    assert renewed.json()["epoch"] == claimed["epoch"]
    assert renewed.json()["revision"] == claimed["revision"] + 1
    wrong_holder = client.post(f"{root}/investigation-runs/{run_id}/tasks/{task['id']}/renew",
        headers={**_worker_headers(client, "worker-other"), "Idempotency-Key": "renew-wrong-holder"}, json={
            "environment_id": "prod", "expected_revision": renewed.json()["revision"],
            "worker_id": "worker-other", "epoch": claimed["epoch"], "lease_seconds": 120,
        })
    assert wrong_holder.status_code == 409
    assert wrong_holder.json()["error"]["code"] == "TASK_LEASE_FENCED"


def test_task_completion_rejects_cross_run_evidence_and_role_mismatch(tmp_path):
    client = _client(tmp_path)
    root, run_id, graph = _start_swarm(client)
    task = next(item for item in graph["tasks"] if item["role"] == "investigator")
    claimed = _claim(client, root, run_id, task, "worker-i", "evidence.investigate").json()
    invalid = _finish(client, root, run_id, claimed, "worker-i",
        {"summary": "无效证据", "hypotheses": [{"statement": "跨范围", "confidence": 0.5,
            "support_evidence_ids": ["evidence-from-another-run"], "counterevidence_ids": []}]}, key="bad-evidence")
    assert invalid.status_code == 403
    assert invalid.json()["error"]["code"] == "TASK_EVIDENCE_SCOPE"
    malformed_role = _finish(client, root, run_id, claimed, "worker-i",
        {"summary": "角色不匹配", "challenge_questions": [{"question": "q", "evidence_ids": [task["input_evidence_ids"][0]],
            "failure_condition": "condition"}]}, key="bad-role")
    assert malformed_role.status_code == 422
    assert malformed_role.json()["error"]["code"] == "TASK_RESULT_ROLE_MISMATCH"
