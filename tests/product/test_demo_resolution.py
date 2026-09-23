import asyncio
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from s9.product.demo import DemoWorkflow
from s9.product.demo_resolution import DemoResolution, NEW_POLICY, OLD_POLICY
from s9.product.registry import ProductError, ProductRegistry


def make_service(tmp_path, monkeypatch):
    import s9.product.demo as demo_module
    monkeypatch.setattr(demo_module.config, "DATA", tmp_path / "data")
    repo = tmp_path / "target"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "tools.py").write_text("def get_order_status(order_id): return {'status':'delivered'}")
    product = SimpleNamespace(registry=ProductRegistry(tmp_path / "registry.sqlite"), repo=repo,
        runtime_commit="a" * 40, _model_env=lambda: {}, _configuration_sha256=lambda: "b" * 64)
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={})))
    workflow = DemoWorkflow(product, client)
    workflow.spawn = lambda coro: coro.close()
    async def ensure_target():
        return {"support_policy": "legacy", "source_commit": "a" * 40}
    workflow.ensure_target = ensure_target
    signal = workflow.registry.ingest_signals(workflow.wid, workflow.aid, workflow.env, workflow.binding["id"], [{
        "source_id": "source-demo", "source_version": "a" * 64, "deduplication_key": "resolution-demo",
        "signal_type": "application_task", "source_kind": "manual",
        "occurred_at": datetime.now(timezone.utc).isoformat(), "observed_at": datetime.now(timezone.utc).isoformat(),
        "summary": "用户报告客服助手使用旧路由"}], idempotency_key="resolution-signal")
    signal = (signal.get("created") or signal.get("items") or signal.get("duplicates"))[0]
    incident = workflow.registry.create_incident(workflow.wid, workflow.aid, workflow.env,
        "用户报告客服助手使用旧路由", "low", [{"signal_id": signal["id"], "expected_revision": signal["revision"]}],
        idempotency_key="resolution-incident")
    run = workflow.registry.create_manual_investigation_run(workflow.wid, workflow.aid, workflow.env,
        incident["id"], expected_incident_revision=incident["revision"], idempotency_key="resolution-run")
    job = {"id": "task-resolution", "state": "completed", "analysis_state": "complete",
        "observation_state": "verified", "incident_id": incident["id"], "runs": {"single": run["run"]["id"]},
        "source_commit": "a" * 40, "message": "Where is order 1001?",
        "business_result": {"support_policy": "legacy", "agent": "escalate", "trace": []},
        "events": [], "created_at": "2026-09-24T00:00:00+00:00"}
    workflow.save(job)
    resolution = workflow.resolution
    resolution._source = lambda: {"source_commit": "a" * 40, "source_hash": "c" * 64,
                                  "source_clean": True, "source_path": "src/tools.py"}
    policy = {"value": OLD_POLICY}

    async def current_policy():
        return {"policy": policy["value"], "health": {"support_policy": policy["value"], "source_commit": "a" * 40}}

    resolution.current_policy = current_policy
    workflow.runtime.git_state = lambda: {"matches_expected": True, "clean": True, "commit": "a" * 40}
    workflow.runtime.status = lambda: {"status": "running", "owned": True}
    return workflow, policy


def test_prepare_is_fixed_hash_bound_and_rejection_requires_new_version(tmp_path, monkeypatch):
    workflow, _ = make_service(tmp_path, monkeypatch)

    async def run():
        prepared = await workflow.resolution.prepare("task-resolution")
        r = prepared["resolution"]
        assert r["runbook_id"] == "support-policy-legacy-to-guarded"
        assert r["operator_selected"] is True
        assert r["old_policy"] == OLD_POLICY and r["new_policy"] == NEW_POLICY
        assert r["payload"] == {"schema": "section9.demo.support-policy/v1", "environment_variable": "S9_SUPPORT_POLICY",
            "from": "legacy", "to": "guarded", "restart_owned_runtime": True}
        with pytest.raises(ProductError, match="摘要"):
            await workflow.resolution.review("task-resolution", "0" * 64, "approved", "review")
        rejected = await workflow.resolution.review("task-resolution", r["plan_sha256"], "rejected", "不接受当前预案")
        assert rejected["resolution"]["status"] == "rejected"
        second = await workflow.resolution.prepare("task-resolution")
        assert second["resolution"]["version"] == 2
        assert second["resolution"]["plan_sha256"] != r["plan_sha256"]
        await workflow.client.aclose()

    asyncio.run(run())


def test_execute_claim_reconcile_then_idempotent_retry(tmp_path, monkeypatch):
    workflow, policy = make_service(tmp_path, monkeypatch)
    calls = {"stop": 0, "start": 0}
    original_stop = workflow.runtime.stop

    def stop():
        calls["stop"] += 1
        if calls["stop"] == 1:
            raise RuntimeError("simulated crash boundary before process stop")
        return {"status": "stopped", "owned": True}

    def start(*, model_env=None):
        calls["start"] += 1
        policy["value"] = model_env["S9_SUPPORT_POLICY"]
        return {"status": "starting"}

    workflow.runtime.stop = stop
    workflow.runtime.start = start

    async def healthy(wanted, samples=1):
        policy["value"] = wanted
        return [{"status": "passed", "support_policy": wanted, "source_commit": "a" * 40}]

    workflow.resolution._wait_health = healthy

    async def run():
        plan = (await workflow.resolution.prepare("task-resolution"))["resolution"]
        await workflow.resolution.review("task-resolution", plan["plan_sha256"], "approved", "人工批准固定预案")
        first = await workflow.resolution.execute("task-resolution", plan["plan_sha256"], "stable-key")
        assert first["resolution"]["status"] == "outcome_unknown"
        claim_id = first["resolution"]["execution_attempt_id"]
        claim = workflow.registry.get("execution_attempt", claim_id, project_id=workflow.wid,
            environment_id=workflow.env, incident_id=plan["incident_id"])
        assert claim["state"] == "outcome_unknown"
        assert claim["history"][0]["state"] == "running"
        reconciled = await workflow.resolution.reconcile("task-resolution")
        assert reconciled["resolution"]["execution"]["state"] == "not_applied"
        retried = await workflow.resolution.execute("task-resolution", plan["plan_sha256"], "stable-key")
        assert retried["resolution"]["status"] == "applied"
        assert policy["value"] == NEW_POLICY
        assert calls == {"stop": 2, "start": 1}
        attempts = workflow.registry.list("execution_attempt", project_id=workflow.wid,
            environment_id=workflow.env, incident_id=plan["incident_id"])
        assert len(attempts) == 2
        assert attempts[0]["history"][0]["state"] == "running"
        assert attempts[0]["history"][-1]["state"] == "not_applied"
        await workflow.client.aclose()

    asyncio.run(run())


def test_database_snapshot_missing_fails_closed(tmp_path, monkeypatch):
    workflow, _ = make_service(tmp_path, monkeypatch)
    workflow.runtime.data_path = tmp_path / "missing-business.sqlite"
    snapshot = workflow.resolution._database_snapshot()
    assert snapshot["valid"] is False
    assert snapshot["logical_rows_sha256"] is None


def test_verification_with_unreadable_database_cannot_create_recovery_or_lesson(tmp_path, monkeypatch):
    workflow, policy = make_service(tmp_path, monkeypatch)
    workflow.runtime.stop = lambda: {"status": "stopped", "owned": True}
    workflow.runtime.start = lambda *, model_env=None: policy.update(value=model_env["S9_SUPPORT_POLICY"])

    async def healthy(wanted, samples=1, interval_seconds=1.0):
        policy["value"] = wanted
        return [{"status": "passed", "support_policy": wanted, "source_commit": "a" * 40} for _ in range(samples)]

    async def probes(job, plan_id, assertions):
        return [{"id": name, "status": "passed", "reply": "ok", "trace": [], "trace_id": None}
                for name in ("order_truth", "refund_guardrail", "complaint_multi_intent", "original_replay")]

    workflow.resolution._wait_health = healthy
    workflow.resolution._business_probes = probes

    async def run():
        plan = (await workflow.resolution.prepare("task-resolution"))["resolution"]
        await workflow.resolution.review("task-resolution", plan["plan_sha256"], "approved", "人工批准")
        applied = await workflow.resolution.execute("task-resolution", plan["plan_sha256"], "verify-key")
        assert applied["resolution"]["status"] == "applied"
        workflow.resolution._database_snapshot = lambda: {"valid": False, "error": "database missing",
                                                           "logical_rows_sha256": None}
        result = await workflow.resolution.verify("task-resolution", plan["plan_sha256"])
        resolution = result["resolution"]
        assert resolution["verification"]["status"] == "failed"
        assert resolution["verification"]["database_guard"]["status"] == "failed"
        assert resolution["recovery_receipt_id"] is None
        assert resolution["lesson_candidate"] is None
        report = workflow.registry.get("validation_report", resolution["validation_report_id"],
            project_id=workflow.wid, environment_id=workflow.env, incident_id=plan["incident_id"])
        assert report["execution_attempt_id"] == resolution["execution_attempt_id"]
        await workflow.client.aclose()

    asyncio.run(run())


def test_rolled_back_idempotency_uses_rollback_receipt(tmp_path, monkeypatch):
    workflow, _ = make_service(tmp_path, monkeypatch)
    job = workflow.get("task-resolution")
    job["resolution"] = {"status": "rolled_back", "plan_id": "plan-rb", "plan_sha256": "d" * 64,
        "incident_id": job["incident_id"], "execution": {"state": "applied"},
        "recovery": {"status": "obsolete_after_rollback"},
        "rollback_receipt": {"id": "rollback-rb", "idempotency_key": "rollback-key", "status": "rolled_back"}}
    workflow.save(job)

    async def run():
        same = await workflow.resolution.rollback("task-resolution", "d" * 64, "rollback-key")
        assert same["resolution"]["status"] == "rolled_back"
        with pytest.raises(ProductError, match="另一个键"):
            await workflow.resolution.rollback("task-resolution", "d" * 64, "different-key")
        await workflow.client.aclose()

    asyncio.run(run())


def test_manifest_bound_source_refresh_preserves_policy_and_audits_old_binding(tmp_path, monkeypatch):
    workflow, _ = make_service(tmp_path, monkeypatch)
    resolution = workflow.resolution
    config = resolution._target_config()
    old_config = {**config, "policy": NEW_POLICY, "owner_plan_sha256": "old-plan-hash"}
    resolution._put("demo_target_config", "support_policy", old_config)
    workflow.runtime.expected_commit = "d" * 40
    workflow.runtime.git_state = lambda: {"matches_expected": True, "clean": True, "commit": "d" * 40}
    resolution._source = DemoResolution._source.__get__(resolution, DemoResolution)

    refreshed = resolution.target_config()
    assert refreshed["policy"] == NEW_POLICY
    assert refreshed["source_commit"] == "d" * 40
    assert refreshed["source_hash"]
    assert refreshed["revision"] == old_config["revision"] + 1
    assert "owner_plan_sha256" not in refreshed
    assert len(refreshed["binding_history"]) == 1
    assert refreshed["binding_history"][0]["source_commit"] == "a" * 40
    assert refreshed["binding_history"][0]["policy"] == NEW_POLICY


def test_unverified_source_binding_refresh_fails_without_mutating_config(tmp_path, monkeypatch):
    workflow, _ = make_service(tmp_path, monkeypatch)
    resolution = workflow.resolution
    original = resolution._target_config()
    workflow.runtime.expected_commit = "e" * 40
    workflow.runtime.git_state = lambda: {"matches_expected": False, "clean": False, "commit": "f" * 40}
    resolution._source = DemoResolution._source.__get__(resolution, DemoResolution)

    with pytest.raises(ProductError, match="源码版本"):
        resolution.target_config()
    unchanged = resolution._record("demo_target_config", "support_policy")
    assert unchanged["source_commit"] == original["source_commit"]
    assert unchanged["policy"] == OLD_POLICY
    assert "binding_history" not in unchanged
