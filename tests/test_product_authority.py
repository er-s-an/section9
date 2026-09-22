from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from s9.core import Core
from s9.store import Rejected, Store


REQUIRED_CHECKS = [
    "heldout_semantic_policy",
    "unaffected_product_fact",
    "terminal_tool_stops",
    "cost_budget",
    "no_stalled_requests",
    "heldout_outside_return_window",
]


def _task(store: Store, run_id: str, kind: str) -> dict:
    with store.tx() as db:
        return next(json.loads(row[0]) for row in db.execute("SELECT data FROM tasks WHERE run_id=?", (run_id,))
                    if json.loads(row[0])["kind"] == kind)


def _fixture(tmp_path, *, condition="swarm", scope=None, with_token=False):
    store = Store(tmp_path / ("authority-" + condition + ".sqlite"), scope=scope)
    ids = ["single"] if condition == "single" else ["fixer-a", "verifier"]
    tokens = {agent_id: store.register(agent_id) for agent_id in ids}
    run = store.inject("prompt", condition, "component", 7, 4000)
    event = store.emit("observation.probe", {"signal": "quality_mismatch"}, run_id=run["id"])
    store.open_incident([event["event_id"]])
    writer = "single" if condition == "single" else "fixer-a"
    writer_task = _task(store, run["id"], condition if condition == "single" else "repair")
    writer_lease = store.claim(writer, writer_task["id"])
    with store.tx() as db:
        agent = store.get(db, "agents", writer)
        current = store.get(db, "runs", run["id"])
        epoch = str(store.meta(db, "transport_epoch"))
    plan = store.create_plan(writer, {
        "run_id": run["id"], "task_id": writer_task["id"], "task_epoch": writer_lease["epoch"],
        "instance_id": agent["instance_id"], "generation": current["generation"],
        "transport_epoch": epoch, "expected_revision": store.current_config()["revision"],
        "actions": [{"type": "set_prompt_revision", "values": {"prompt_version": "healthy"}}],
        "rationale": "authority regression fixture",
    })
    grant = store.grant(writer, plan["id"])
    store.execute(writer, plan["id"], grant["grant_id"], "authority-fixture")
    verifier = "single" if condition == "single" else "verifier"
    verify_task = _task(store, run["id"], condition if condition == "single" else "verify")
    lease = writer_lease if condition == "single" else store.claim(verifier, verify_task["id"])
    with store.tx() as db:
        agent = store.get(db, "agents", verifier)
        current = store.get(db, "runs", run["id"])
        transport = str(store.meta(db, "transport_epoch"))
    request = {
        "run_id": run["id"], "task_id": lease["task_id"], "task_epoch": lease["epoch"],
        "instance_id": agent["instance_id"], "generation": current["generation"],
        "transport_epoch": transport, "expected_revision": store.current_config()["revision"],
    }
    return (store, run, verifier, verify_task, lease, request, tokens) if with_token else (store, run, verifier, verify_task, lease, request)


def _passed_result(store: Store, run_id: str, job: dict) -> dict:
    return {"passed": True, "tested_revision": store.run(run_id)["last_action"]["after_revision"],
            "tested_config_hash": job["config_hash"],
            "checks": [{"name": name, "passed": True} for name in REQUIRED_CHECKS]}


def _agent(store: Store, agent_id: str) -> dict:
    with store.tx() as db:
        return store.get(db, "agents", agent_id)


@pytest.mark.parametrize("mutation", ["unclaimed", "paused", "expired", "reregistered", "reset"])
def test_invalid_initial_verification_requests_are_rejected_before_probe(tmp_path, mutation):
    store, run, verifier, task, lease, request = _fixture(tmp_path)
    if mutation == "unclaimed":
        with store.tx() as db:
            raw = store.get(db, "tasks", task["id"])
            raw.update(status="available", holder=None, holder_instance_id=None, lease_deadline=0)
            store.save(db, "tasks", raw)
    elif mutation == "paused":
        store.pause(verifier, True)
    elif mutation == "expired":
        with store.tx() as db:
            raw = store.get(db, "tasks", task["id"])
            raw["lease_deadline"] = 0
            store.save(db, "tasks", raw)
    elif mutation == "reregistered":
        store.register(verifier)
    else:
        store.reset()
    with pytest.raises(Rejected):
        store.begin_verification(_agent(store, verifier), request)


def test_cross_run_and_cross_arm_verification_scope_is_rejected(tmp_path):
    store, run, _, _, _, request = _fixture(tmp_path)
    store.fail_run(run["id"], "authority-test-takeover")
    other = store.inject("prompt", "swarm", "component", 8, 4000)
    request["run_id"] = other["id"]
    agent = _agent(store, "verifier")
    with pytest.raises(Rejected):
        store.begin_verification(agent, request)

    arm = _fixture(tmp_path / "arm", scope={"pair_id": "p", "run_id": "arm-run", "arm": "baseline", "spec_hash": "s"})
    _, _, _, _, _, foreign_request = arm
    foreign_request["run_id"] = run["id"]
    with pytest.raises(Rejected) as error:
        store.begin_verification(agent, foreign_request)
    assert error.value.code in {"SCOPE_MISMATCH", "TASK_RUN_MISMATCH", "INSTANCE_STALE"}


def test_valid_verifier_and_single_contracts_capture_authority_and_close(tmp_path):
    for condition in ("swarm", "single"):
        store, run, verifier, _, _, request = _fixture(tmp_path / condition, condition=condition)
        agent = _agent(store, verifier)
        job = store.begin_verification(agent, request)
        assert {"run_id", "task_id", "task_epoch", "instance_id", "generation",
                "transport_epoch", "expected_revision", "config_hash", "contract_hash", "action_id"}.issubset(job)
        result = store.verification(verifier, run["id"], _passed_result(store, run["id"], job), job=job)
        assert result["passed"] is True
        assert store.run(run["id"])["status"] == "resolved"


@pytest.mark.asyncio
async def test_core_rejects_authority_lost_during_probe_without_terminal_write(tmp_path):
    store, run, verifier, _, _, request = _fixture(tmp_path)
    started = asyncio.Event()
    release = asyncio.Event()

    class Victim:
        async def probe(self, **kwargs):
            started.set()
            await release.wait()
            with store.tx() as db:
                cfg = store.current_config(db)
            return {"passed": True, "tested_revision": cfg["revision"],
                    "checks": [{"name": name, "passed": True} for name in REQUIRED_CHECKS]}

    core = Core.__new__(Core)
    core.store, core.victim, core.verifying = store, Victim(), set()
    core.memory_candidates = {}
    class Memory:
        def record_success(self, *args):
            return {"ok": True}
    core.memory = core.evaluation_results = Memory()
    task = asyncio.create_task(core.verify({"id": verifier, "instance_id": request["instance_id"], "role": "verifier"}, request))
    await asyncio.wait_for(started.wait(), 1)
    before_usage = store.usage_records(run["id"])
    store.pause(verifier, True)
    release.set()
    with pytest.raises(Rejected):
        await task
    assert store.run(run["id"])["status"] == "verifying"
    assert store.run(run["id"])["verification"] is None
    assert store.usage_records(run["id"]) == before_usage


@pytest.mark.asyncio
async def test_http_verify_contract_fences_before_probe_and_allows_valid_terminal_commit(tmp_path):
    from s9 import api

    store, run, verifier, _, _, request, tokens = _fixture(tmp_path, with_token=True)
    calls = 0
    class Victim:
        async def probe(self, **kwargs):
            nonlocal calls
            calls += 1
            return {"passed": True, "tested_revision": store.current_config()["revision"],
                    "checks": [{"name": name, "passed": True} for name in REQUIRED_CHECKS]}
    core = Core.__new__(Core)
    core.store, core.victim, core.verifying = store, Victim(), set()
    core.memory_candidates = {}
    class Memory:
        def record_success(self, *args):
            return {"ok": True}
    core.memory = core.evaluation_results = Memory()
    api.app.state.core = core
    api.app.dependency_overrides[api.core] = lambda: core
    transport = httpx.ASGITransport(app=api.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:9021") as client:
        old = await client.post("/agent/verify", headers={"authorization": "Bearer " + tokens[verifier]}, json={"run_id": run["id"]})
        assert old.status_code == 422 and calls == 0
        before = list(store.usage_records(run["id"]))
        bad = dict(request, expected_revision="stale")
        denied = await client.post("/agent/verify", headers={"authorization": "Bearer " + tokens[verifier]}, json=bad)
        assert denied.status_code in {400, 403, 409} and calls == 0
        assert store.usage_records(run["id"]) == before
        valid = await client.post("/agent/verify", headers={"authorization": "Bearer " + tokens[verifier]}, json=request)
        assert valid.status_code == 200
        assert calls == 1 and valid.json()["passed"] is True
        assert store.run(run["id"])["status"] == "resolved"
    api.app.dependency_overrides.clear()


@pytest.mark.asyncio
@pytest.mark.parametrize("loss", ["paused", "reregistered", "reset", "transport"])
async def test_http_verify_rechecks_authority_after_probe_started(tmp_path, loss):
    from s9 import api

    store, run, verifier, _, _, request, tokens = _fixture(tmp_path, with_token=True)
    started, release = asyncio.Event(), asyncio.Event()
    calls = 0
    class Victim:
        async def probe(self, **kwargs):
            nonlocal calls
            calls += 1
            started.set()
            await release.wait()
            return {"passed": True, "tested_revision": store.current_config()["revision"],
                    "checks": [{"name": name, "passed": True} for name in REQUIRED_CHECKS]}
    core = Core.__new__(Core)
    core.store, core.victim, core.verifying = store, Victim(), set()
    api.app.state.core = core
    api.app.dependency_overrides[api.core] = lambda: core
    transport = httpx.ASGITransport(app=api.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:9021") as client:
        pending = asyncio.create_task(client.post("/agent/verify", headers={"authorization": "Bearer " + tokens[verifier]}, json=request))
        await asyncio.wait_for(started.wait(), 1)
        usage_id = store.reserve_usage(run["id"], 5, "settled-before-authority-loss")
        store.settle_usage(usage_id, {"total_tokens": 3}, 0.01)
        before = list(store.usage_records(run["id"]))
        if loss == "paused":
            store.pause(verifier, True)
        elif loss == "reregistered":
            store.register(verifier)
        elif loss == "reset":
            store.reset()
        else:
            store.set_muted(True)
            store.set_muted(False)
        release.set()
        response = await pending
        assert response.status_code in {400, 403, 409}
        assert calls == 1
        assert store.usage_records(run["id"]) == before
        assert store.run(run["id"])["verification"] is None
        assert store.run(run["id"])["status"] != "resolved"
    api.app.dependency_overrides.clear()
