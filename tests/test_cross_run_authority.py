from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from s9.store import Rejected, Store


def _incident(store: Store, *, seed: int = 1):
    for agent_id in ("fixer-a", "fixer-b", "verifier"):
        store.register(agent_id)
    run = store.inject("prompt", "swarm", "authority-test", seed, 4000)
    event = store.emit("observation.probe", {"signal": "quality_mismatch"}, run_id=run["id"])
    store.open_incident([event["event_id"]])
    with store.tx() as db:
        task = next(json.loads(row[0]) for row in db.execute("SELECT data FROM tasks")
                    if json.loads(row[0])["kind"] == "repair")
    lease = store.claim("fixer-a", task["id"])
    return run, lease


def _active_message(store: Store, run_id: str, lease: dict) -> dict:
    with store.tx() as db:
        agent = store.get(db, "agents", "fixer-a")
        run = store.get(db, "runs", run_id)
        generation = str(store.meta(db, "generation"))
        transport_epoch = str(store.meta(db, "transport_epoch"))
    return {
        "run_id": run_id,
        "task_id": lease["task_id"],
        "task_epoch": lease["epoch"],
        "instance_id": agent["instance_id"],
        "generation": str(run["generation"]) if run else generation,
        "transport_epoch": transport_epoch,
        "kind": "result",
        "content": "model result",
        "evidence_ids": [],
        "confidence": 0.8,
    }


def _action_rows(store: Store, run_id: str) -> list[dict]:
    with store.tx() as db:
        return [json.loads(row[0]) for row in db.execute("SELECT data FROM actions WHERE run_id=?", (run_id,))]


def _active_plan(store: Store, run_id: str, lease: dict) -> dict:
    with store.tx() as db:
        agent = store.get(db, "agents", "fixer-a")
        run = store.get(db, "runs", run_id)
        transport_epoch = str(store.meta(db, "transport_epoch"))
    return {
        "run_id": run_id,
        "task_id": lease["task_id"],
        "task_epoch": lease["epoch"],
        "instance_id": agent["instance_id"],
        "generation": str(run["generation"]),
        "transport_epoch": transport_epoch,
        "expected_revision": store.current_config()["revision"],
        "actions": [{"type": "set_prompt_revision", "values": {"prompt_version": "healthy"}}],
        "rationale": "authority test plan",
        "evidence_ids": [],
    }


def test_cross_run_lease_cannot_propose_or_validate_against_new_run(tmp_path):
    store = Store(tmp_path / "authority.sqlite")
    run_a, lease_a = _incident(store, seed=1)
    store.fail_run(run_a["id"], "test_takeover")
    run_b = store.inject("prompt", "swarm", "authority-test", 2, 4000)
    before_config = store.current_config()
    before_actions = _action_rows(store, run_b["id"])
    with store.tx() as db:
        agent_a = store.get(db, "agents", "fixer-a")
        transport_epoch = str(store.meta(db, "transport_epoch"))
    plan = {
        "run_id": run_b["id"],
        "task_id": lease_a["task_id"],
        "task_epoch": lease_a["epoch"],
        "instance_id": agent_a["instance_id"],
        "generation": str(run_b["generation"]),
        "transport_epoch": transport_epoch,
        "expected_revision": before_config["revision"],
        "actions": [{"type": "set_prompt_revision", "values": {"prompt_version": "healthy"}}],
        "rationale": "cross-run stale proposal",
    }
    with pytest.raises(Rejected) as error:
        store.create_plan("fixer-a", plan)
    assert error.value.code in {"TASK_RUN_MISMATCH", "TASK_TERMINAL"}
    assert store.current_config() == before_config
    assert _action_rows(store, run_b["id"]) == before_actions
    assert store.run(run_b["id"])["plan"] is None
    assert store.run(run_b["id"])["last_action"] is None

    with store.tx() as db:
        with pytest.raises(Rejected) as direct_error:
            store._valid_task(db, store.get(db, "agents", "fixer-a"),
                              store.get(db, "tasks", lease_a["task_id"]),
                              store.get(db, "runs", run_b["id"]), lease_a["epoch"])
    assert direct_error.value.code in {"TASK_RUN_MISMATCH", "TASK_TERMINAL"}


def test_valid_current_lease_message_is_delivered(tmp_path):
    store = Store(tmp_path / "authority.sqlite")
    run, lease = _incident(store)
    result = store.message("fixer-a", _active_message(store, run["id"], lease))
    assert result["delivered"] is True
    assert [e for e in store.events(run_id=run["id"]) if e["event_type"] == "dialog.received"]


def test_old_context_after_new_run_cannot_deliver_model_result(tmp_path):
    store = Store(tmp_path / "authority.sqlite")
    run_a, lease_a = _incident(store, seed=1)
    store.fail_run(run_a["id"], "test_takeover")
    run_b = store.inject("prompt", "swarm", "authority-test", 2, 4000)
    old_context = _active_message(store, run_b["id"], lease_a)
    before = [e for e in store.events() if e["event_type"] == "dialog.received"]
    with pytest.raises(Rejected):
        store.message("fixer-a", old_context)
    after = [e for e in store.events() if e["event_type"] == "dialog.received"]
    assert after == before


def test_old_transport_context_after_mute_cycle_is_rejected_and_audited(tmp_path):
    store = Store(tmp_path / "authority.sqlite")
    run, lease = _incident(store)
    old_context = _active_message(store, run["id"], lease)
    store.set_muted(True)
    store.set_muted(False)
    before_events = store.events(run_id=run["id"])
    with pytest.raises(Rejected):
        store.message("fixer-a", old_context)
    after_events = store.events(run_id=run["id"])
    new_events = after_events[len(before_events):]
    assert not [e for e in new_events if e["event_type"] == "dialog.received"]
    assert any(e["event_type"] in {"dialog.rejected", "agent.request_rejected"} for e in new_events)


def test_reregistered_agent_cannot_use_old_lease(tmp_path):
    store = Store(tmp_path / "authority.sqlite")
    run, lease = _incident(store)
    with store.tx() as db:
        old_agent = store.get(db, "agents", "fixer-a")
    store.register("fixer-a")
    with store.tx() as db:
        new_agent = store.get(db, "agents", "fixer-a")
    assert new_agent["instance_id"] != old_agent["instance_id"]
    with pytest.raises(Rejected) as error:
        store.renew("fixer-a", lease["task_id"], lease["epoch"])
    assert error.value.code == "FENCE_STALE"


def test_old_transport_plan_cannot_be_granted(tmp_path):
    store = Store(tmp_path / "authority.sqlite")
    run, lease = _incident(store)
    plan = store.create_plan("fixer-a", _active_plan(store, run["id"], lease))
    store.set_muted(True)
    with pytest.raises(Rejected) as error:
        store.grant("fixer-a", plan["id"])
    assert error.value.code == "CONTEXT_STALE"


def test_terminal_run_revokes_task_and_grant_with_audit(tmp_path):
    store = Store(tmp_path / "authority.sqlite")
    run, lease = _incident(store)
    plan = store.create_plan("fixer-a", _active_plan(store, run["id"], lease))
    grant = store.grant("fixer-a", plan["id"])
    store.fail_run(run["id"], "terminal_attack_regression")
    with store.tx() as db:
        task = store.get(db, "tasks", lease["task_id"])
        saved_grant = store.get(db, "grants", grant["grant_id"])
    assert task["status"] == "revoked"
    assert task["revoked_reason"] == "terminal_attack_regression"
    assert saved_grant["revoked_reason"] == "terminal_attack_regression"
    assert any(e["event_type"] == "authority.revoked" for e in store.events(run_id=run["id"]))


def test_valid_idempotent_retry_returns_receipt(tmp_path):
    store = Store(tmp_path / "authority.sqlite")
    run, lease = _incident(store)
    plan = store.create_plan("fixer-a", _active_plan(store, run["id"], lease))
    grant = store.grant("fixer-a", plan["id"])
    first = store.execute("fixer-a", plan["id"], grant["grant_id"], "retry-key")
    replay = store.execute("fixer-a", plan["id"], grant["grant_id"], "retry-key")
    assert replay["replayed_receipt"] is True
    assert replay["action_id"] == first["action_id"]


def test_terminal_idempotent_retry_is_rejected_without_second_write(tmp_path):
    store = Store(tmp_path / "authority.sqlite")
    run, lease = _incident(store)
    plan = store.create_plan("fixer-a", _active_plan(store, run["id"], lease))
    grant = store.grant("fixer-a", plan["id"])
    first = store.execute("fixer-a", plan["id"], grant["grant_id"], "terminal-key")
    store.fail_run(run["id"], "terminal_before_retry")
    before_config = store.current_config()
    before_actions = _action_rows(store, run["id"])
    with pytest.raises(Rejected) as error:
        store.execute("fixer-a", plan["id"], grant["grant_id"], "terminal-key")
    assert error.value.code in {"LEASE_EXPIRED", "RUN_CLOSED", "GRANT_MISMATCH"}
    assert store.current_config() == before_config
    assert _action_rows(store, run["id"]) == before_actions
    assert not [e for e in store.events(run_id=run["id"])
                 if e["event_type"] == "action.applied" and e["payload"].get("action_id") != first["action_id"]]


@pytest.mark.asyncio
async def test_model_rejects_lease_change_after_provider_and_keeps_usage(tmp_path, monkeypatch):
    import dotenv
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *args, **kwargs: None)
    from s9 import config
    from s9.api import model as agent_model
    from s9.contracts import ModelRequest
    from s9.model import ModelClient

    monkeypatch.setattr(config, "MODEL_KEY", "component-key")
    monkeypatch.setattr(config, "MODEL_TIMEOUT", 1.0)
    store = Store(tmp_path / "authority.sqlite")
    run, lease = _incident(store)
    with store.tx() as db:
        agent_a = store.get(db, "agents", "fixer-a")
        task = store.get(db, "tasks", lease["task_id"])
    client = ModelClient(store)
    started = asyncio.Event()
    release = asyncio.Event()

    class Response:
        status_code = 200

        @staticmethod
        def json():
            return {"model": "component-model", "usage": {"total_tokens": 7},
                    "choices": [{"message": {"content": "provider result"}}]}

    async def delayed_post(*args, **kwargs):
        started.set()
        await release.wait()
        return Response()

    monkeypatch.setattr(client.client, "post", delayed_post)
    core = SimpleNamespace(store=store, model=client)
    item = ModelRequest(run_id=run["id"], purpose="repair", messages=[{"role": "user", "content": "repair"}], max_tokens=64)
    request = asyncio.create_task(agent_model(item, agent_a, core))
    await asyncio.wait_for(started.wait(), 1)
    with store.tx() as db:
        stale = store.get(db, "tasks", task["id"])
        stale["lease_deadline"] = 0.0
        store.save(db, "tasks", stale)
    store.claim("fixer-b", task["id"])
    release.set()
    with pytest.raises(Rejected) as error:
        await request
    assert error.value.code == "FENCE_STALE"
    assert store.run(run["id"])["usage_tokens"] == 7
    assert store.usage_records(run["id"])[0]["status"] == "completed"
    assert any(e["event_type"] == "model.source_stale" for e in store.events(run_id=run["id"]))
    await client.close()


@pytest.mark.asyncio
async def test_model_rechecks_task_fence_before_dispatch_after_queue(tmp_path, monkeypatch):
    import dotenv
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *args, **kwargs: None)
    from s9 import config
    from s9.api import model as agent_model
    from s9.contracts import ModelRequest
    from s9.model import ModelClient
    from s9.model_scheduler import ProviderGateway

    monkeypatch.setattr(config, "MODEL_KEY", "component-key")
    monkeypatch.setattr(config, "MODEL_TIMEOUT", 1.0)
    store = Store(tmp_path / "dispatch-fence.sqlite")
    run, lease = _incident(store)
    with store.tx() as db:
        agent_a = store.get(db, "agents", "fixer-a")
    gateway = ProviderGateway(capacity=1)
    client = ModelClient(store, gateway=gateway)
    provider_calls = 0
    first_started, release_first = asyncio.Event(), asyncio.Event()

    class Response:
        status_code = 200

        @staticmethod
        def json():
            return {"model": "component-model", "usage": {"total_tokens": 7},
                    "choices": [{"message": {"content": "held result"}}]}

    async def delayed_post(*args, **kwargs):
        nonlocal provider_calls
        provider_calls += 1
        if provider_calls == 1:
            first_started.set()
            await release_first.wait()
        return Response()

    monkeypatch.setattr(client.client, "post", delayed_post)
    held = asyncio.create_task(client.complete([{"role": "user", "content": "occupy shared slot"}],
                                               run_id=run["id"], purpose="hold", max_tokens=64,
                                               generation=run["generation"]))
    await asyncio.wait_for(first_started.wait(), 1)
    core = SimpleNamespace(store=store, model=client)
    request_item = ModelRequest(run_id=run["id"], purpose="repair",
                                messages=[{"role": "user", "content": "repair"}], max_tokens=64)
    request = asyncio.create_task(agent_model(request_item, agent_a, core))
    deadline = asyncio.get_running_loop().time() + 1
    while len(store.usage_records(run["id"])) < 2 and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(.01)
    assert len(store.usage_records(run["id"])) == 2

    with store.tx() as db:
        stale = store.get(db, "tasks", lease["task_id"])
        stale["lease_deadline"] = 0.0
        store.save(db, "tasks", stale)
    store.claim("fixer-b", lease["task_id"])
    release_first.set()

    assert (await asyncio.wait_for(held, 1))["content"] == "held result"
    with pytest.raises(Rejected) as error:
        await asyncio.wait_for(request, 1)
    assert error.value.code == "FENCE_STALE"
    assert provider_calls == 1
    records = {row["purpose"]: row for row in store.usage_records(run["id"])}
    assert records["repair"]["provider_state"] == "not_sent"
    assert records["repair"]["usage"]["total_tokens"] == 0
    started_ids = {event["payload"].get("request_id") for event in store.events(run_id=run["id"])
                   if event["event_type"] == "model.provider_started"}
    assert records["repair"]["request_id"] not in started_ids
    assert store.run(run["id"])["reserved_tokens"] == 0
    await client.close()
    await gateway.close()


def test_message_contract_requires_authority_context_fields():
    from pydantic import ValidationError
    from s9.contracts import MessageRequest, PlanRequest

    with pytest.raises(ValidationError):
        MessageRequest(run_id="run", kind="result", content="stale")
    with pytest.raises(ValidationError):
        PlanRequest(run_id="run", task_id="task", task_epoch="1", expected_revision="1",
                    actions=[], rationale="missing context")
