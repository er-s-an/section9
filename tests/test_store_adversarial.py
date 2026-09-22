from __future__ import annotations

import json

import pytest

from s9.store import Rejected, Store


def _incident(tmp_path, *, actors=("fixer-a", "fixer-b", "verifier")):
    store = Store(tmp_path / "s9.sqlite")
    tokens = {actor: store.register(actor) for actor in actors}
    run = store.inject("prompt", "swarm", "test-model", 7, 4000)
    observation = store.emit("victim.observation", {"signal": "quality_mismatch", "summary": "probe"}, run_id=run["id"], producer="victim")
    assert store.open_incident([observation["event_id"]])["opened"]
    return store, tokens, run["id"]


def _task(store: Store, run_id: str, kind: str) -> dict:
    with store.tx() as db:
        rows = db.execute("SELECT data FROM tasks WHERE run_id=?", (run_id,)).fetchall()
        tasks = [json.loads(row[0]) for row in rows]
    return next(task for task in tasks if task["kind"] == kind)


def _plan(store: Store, fixer: str, run_id: str, task: dict) -> dict:
    config = store.current_config()
    with store.tx() as db:
        agent = store.get(db, "agents", fixer)
        run = store.get(db, "runs", run_id)
        transport_epoch = str(store.meta(db, "transport_epoch"))
    return store.create_plan(fixer, {
        "run_id": run_id, "task_id": task["id"], "task_epoch": task["epoch"],
        "instance_id": agent["instance_id"], "generation": run["generation"], "transport_epoch": transport_epoch,
        "expected_revision": config["revision"],
        "actions": [{"type": "apply_retry_policy", "values": {"retry_limit": 2}}],
        "rationale": "component-test repair", "evidence_ids": [],
    })


def _rejection_events(store: Store, run_id: str) -> list[dict]:
    return [event for event in store.events(run_id=run_id) if event["event_type"] in {"action.rejected", "grant.rejected"}]


def test_l0_blocks_grant_and_preserves_revision_with_audit(tmp_path):
    store, _, run_id = _incident(tmp_path)
    task = _task(store, run_id, "repair")
    store.claim("fixer-a", task["id"])
    plan = _plan(store, "fixer-a", run_id, {**task, "epoch": "1"})
    before = store.current_config()["revision"]
    store.set_autonomy("L0")
    with pytest.raises(Rejected, match="L0") as exc:
        store.grant("fixer-a", plan["id"])
    assert exc.value.code == "POLICY_DENIED"
    assert store.current_config()["revision"] == before
    assert any(event["payload"]["code"] == "POLICY_DENIED" for event in _rejection_events(store, run_id)), "policy rejection must be auditable"


def test_l1_requires_exact_approval_then_allows_that_plan(tmp_path):
    store, _, run_id = _incident(tmp_path)
    store.set_autonomy("L1")
    task = _task(store, run_id, "repair")
    claimed = store.claim("fixer-a", task["id"])
    plan = _plan(store, "fixer-a", run_id, {**task, "epoch": claimed["epoch"]})
    with pytest.raises(Rejected) as denied:
        store.grant("fixer-a", plan["id"])
    assert denied.value.code == "APPROVAL_REQUIRED"
    store.approve(plan["id"])
    grant = store.grant("fixer-a", plan["id"])
    assert grant["plan_hash"] == plan["hash"]


def test_l2_white_list_rejects_unknown_action(tmp_path):
    store, _, run_id = _incident(tmp_path)
    task = _task(store, run_id, "repair")
    claimed = store.claim("fixer-a", task["id"])
    with store.tx() as db:
        agent = store.get(db, "agents", "fixer-a")
        run = store.get(db, "runs", run_id)
        transport_epoch = str(store.meta(db, "transport_epoch"))
    with pytest.raises(Rejected) as denied:
        store.create_plan("fixer-a", {
            "run_id": run_id, "task_id": task["id"], "task_epoch": claimed["epoch"],
            "instance_id": agent["instance_id"], "generation": run["generation"], "transport_epoch": transport_epoch,
            "expected_revision": store.current_config()["revision"],
            "actions": [{"type": "set_prompt_revision", "values": {"prompt_version": "evil"}}],
            "rationale": "must be rejected", "evidence_ids": [],
        })
    assert denied.value.code == "POLICY_DENIED"


def test_non_writer_and_verifier_cannot_execute(tmp_path):
    store, _, run_id = _incident(tmp_path)
    task = _task(store, run_id, "repair")
    claimed = store.claim("fixer-a", task["id"])
    plan = _plan(store, "fixer-a", run_id, {**task, "epoch": claimed["epoch"]})
    grant = store.grant("fixer-a", plan["id"])
    before = store.current_config()["revision"]
    with pytest.raises(Rejected) as denied:
        store.execute("verifier", plan["id"], grant["grant_id"], "outsider-key")
    assert denied.value.code == "ROLE_FORBIDDEN"
    assert store.current_config()["revision"] == before
    assert any(event["payload"]["code"] == "ROLE_FORBIDDEN" for event in _rejection_events(store, run_id))


def test_expired_a_then_b_epoch_rejects_old_grant_without_revision_change(tmp_path):
    store, _, run_id = _incident(tmp_path)
    task = _task(store, run_id, "repair")
    a = store.claim("fixer-a", task["id"])
    plan = _plan(store, "fixer-a", run_id, {**task, "epoch": a["epoch"]})
    grant = store.grant("fixer-a", plan["id"])
    with store.tx() as db:
        expired = store.get(db, "tasks", task["id"])
        expired["lease_deadline"] = 0
        store.save(db, "tasks", expired)
    b = store.claim("fixer-b", task["id"])
    assert b["epoch"] != a["epoch"]
    before = store.current_config()["revision"]
    with pytest.raises(Rejected) as denied:
        store.execute("fixer-a", plan["id"], grant["grant_id"], "old-a")
    assert denied.value.code in {"FENCE_STALE", "LEASE_EXPIRED"}
    assert store.current_config()["revision"] == before
    assert any(event["payload"]["code"] in {"FENCE_STALE", "LEASE_EXPIRED"} for event in _rejection_events(store, run_id))


def test_reset_rejects_old_grant(tmp_path):
    store, _, run_id = _incident(tmp_path)
    task = _task(store, run_id, "repair")
    claimed = store.claim("fixer-a", task["id"])
    plan = _plan(store, "fixer-a", run_id, {**task, "epoch": claimed["epoch"]})
    grant = store.grant("fixer-a", plan["id"])
    old_revision = store.current_config()["revision"]
    store.reset()
    with pytest.raises(Rejected) as denied:
        store.execute("fixer-a", plan["id"], grant["grant_id"], "after-reset")
    assert denied.value.code == "RESET_GENERATION_STALE"
    assert store.current_config()["revision"] != old_revision


def test_verification_cannot_close_when_tested_revision_changes(tmp_path):
    store, _, run_id = _incident(tmp_path)
    task = _task(store, run_id, "repair")
    claimed = store.claim("fixer-a", task["id"])
    plan = _plan(store, "fixer-a", run_id, {**task, "epoch": claimed["epoch"]})
    grant = store.grant("fixer-a", plan["id"])
    applied = store.execute("fixer-a", plan["id"], grant["grant_id"], "verify-revision")
    result = store.verification("verifier", run_id, {"passed": True, "tested_revision": str(int(applied["revision"]) - 1), "checks": []})
    assert result["passed"] is False
    assert store.run(run_id)["status"] == "failed"


def test_idempotency_replays_once_and_conflict_does_not_mutate(tmp_path):
    store, _, run_id = _incident(tmp_path)
    task = _task(store, run_id, "repair")
    claimed = store.claim("fixer-a", task["id"])
    plan = _plan(store, "fixer-a", run_id, {**task, "epoch": claimed["epoch"]})
    grant = store.grant("fixer-a", plan["id"])
    first = store.execute("fixer-a", plan["id"], grant["grant_id"], "same-key")
    revision = store.current_config()["revision"]
    replay = store.execute("fixer-a", plan["id"], grant["grant_id"], "same-key")
    assert replay["replayed_receipt"] is True and replay["action_id"] == first["action_id"]
    assert store.current_config()["revision"] == revision
    # A second valid plan/grant reaches the idempotency table before mutation.
    task2 = _task(store, run_id, "repair")
    plan2 = _plan(store, "fixer-a", run_id, {**task2, "epoch": task2["epoch"]})
    grant2 = store.grant("fixer-a", plan2["id"])
    with pytest.raises(Rejected) as denied:
        store.execute("fixer-a", plan2["id"], grant2["grant_id"], "same-key")
    assert denied.value.code == "IDEMPOTENCY_CONFLICT"
    assert store.current_config()["revision"] == revision
    assert any(event["payload"]["code"] == "IDEMPOTENCY_CONFLICT" for event in _rejection_events(store, run_id))


def test_mutated_plan_hash_cannot_bypass_grant_binding(tmp_path):
    store, _, run_id = _incident(tmp_path)
    task = _task(store, run_id, "repair")
    claimed = store.claim("fixer-a", task["id"])
    plan = _plan(store, "fixer-a", run_id, {**task, "epoch": claimed["epoch"]})
    grant = store.grant("fixer-a", plan["id"])
    with store.tx() as db:
        mutated = store.get(db, "plans", plan["id"])
        mutated["actions"] = [{"type": "apply_retry_policy", "values": {"retry_limit": 0}}]
        store.save(db, "plans", mutated)
    with pytest.raises(Rejected) as denied:
        store.execute("fixer-a", plan["id"], grant["grant_id"], "mutated-plan")
    assert denied.value.code == "GRANT_MISMATCH"
    assert any(event["payload"]["code"] == "GRANT_MISMATCH" for event in _rejection_events(store, run_id))


def test_muted_message_is_not_delivered(tmp_path):
    store, _, run_id = _incident(tmp_path)
    task = _task(store, run_id, "repair")
    lease = store.claim("fixer-a", task["id"])
    with store.tx() as db:
        agent = store.get(db, "agents", "fixer-a")
        run = store.get(db, "runs", run_id)
    store.set_muted(True)
    with store.tx() as db:
        transport_epoch = str(store.meta(db, "transport_epoch"))
    result = store.message("fixer-a", {"run_id": run_id, "task_id": lease["task_id"], "task_epoch": lease["epoch"],
        "instance_id": agent["instance_id"], "generation": run["generation"], "transport_epoch": transport_epoch,
        "kind": "hypothesis", "content": "secret", "evidence_ids": [], "confidence": .7})
    assert result["delivered"] is False
    with store.tx() as db:
        assert db.execute("SELECT count(*) FROM messages WHERE run_id=?", (run_id,)).fetchone()[0] == 0
    assert any(event["event_type"] == "dialog.dropped" and event["payload"]["reason"] == "MUTED" for event in store.events(run_id=run_id))
