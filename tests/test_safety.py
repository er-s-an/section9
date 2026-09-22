"""Transactional regression proofs; these are not live-model acceptance records."""
from concurrent.futures import ThreadPoolExecutor

import pytest

from s9.store import Rejected, Store


def setup(tmp_path):
    s = Store(tmp_path / "test.sqlite")
    for aid in ["fixer-a", "fixer-b", "verifier"]:
        s.register(aid)
    run = s.inject("prompt", "swarm", "component-test", 42, 4000)
    e = s.emit("observation.probe", {"signal": "quality_mismatch"}, run_id=run["id"])
    s.open_incident([e["event_id"]])
    with s.tx() as db:
        import json
        task = next(json.loads(r[0]) for r in db.execute("SELECT data FROM tasks") if json.loads(r[0])["kind"] == "repair")
    lease = s.claim("fixer-a", task["id"])
    p = s.create_plan("fixer-a", {"run_id": run["id"], "task_id": task["id"], "task_epoch": lease["epoch"],
        "expected_revision": s.current_config()["revision"], "actions": [{"type": "set_prompt_revision", "values": {"prompt_version": "healthy"}}], "rationale": "component fixture"})
    return s, run, lease, p


def test_reset_rejects_old_renew_completion_and_messages(tmp_path):
    s, r, lease, p = setup(tmp_path)
    s.reset()
    calls = [lambda: s.renew("fixer-a", lease["task_id"], lease["epoch"]),
             lambda: s.complete_task("fixer-a", lease["task_id"], lease["epoch"], "late"),
             lambda: s.message("fixer-a", {"run_id": r["id"], "kind": "result", "content": "late"})]
    for call in calls:
        with pytest.raises(Rejected):
            call()


def test_resolved_run_rejects_late_completion_without_event(tmp_path):
    s, r, lease, p = setup(tmp_path)
    with s.tx() as db:
        run = s.get(db, "runs", r["id"])
        run.update(status="resolved")
        s.save(db, "runs", run)
    before = [e for e in s.events(run_id=r["id"]) if e["event_type"] == "task.completed"]
    with pytest.raises(Rejected) as error:
        s.complete_task("fixer-a", lease["task_id"], lease["epoch"], "late after close")
    assert error.value.code == "RUN_CLOSED"
    after = [e for e in s.events(run_id=r["id"]) if e["event_type"] == "task.completed"]
    assert after == before


def test_duplicate_completion_is_task_lost_and_emits_once(tmp_path):
    s, r, lease, p = setup(tmp_path)
    s.complete_task("fixer-a", lease["task_id"], lease["epoch"], "first completion")
    before = [e for e in s.events(run_id=r["id"]) if e["event_type"] == "task.completed"]
    with pytest.raises(Rejected) as error:
        s.complete_task("fixer-a", lease["task_id"], lease["epoch"], "duplicate completion")
    assert error.value.code == "LEASE_EXPIRED"
    after = [e for e in s.events(run_id=r["id"]) if e["event_type"] == "task.completed"]
    assert after == before


def test_policy_change_between_grant_and_execute_rejected(tmp_path):
    s, r, lease, p = setup(tmp_path)
    g = s.grant("fixer-a", p["id"])
    rev = s.current_config()["revision"]
    s.set_autonomy("L0")
    with pytest.raises(Rejected) as error:
        s.execute("fixer-a", p["id"], g["id"], "stale-policy")
    assert error.value.code == "POLICY_CHANGED"
    assert s.current_config()["revision"] == rev


def test_mute_invalidates_cached_peer_plan_and_existing_grant(tmp_path):
    s, r, lease, p = setup(tmp_path)
    g = s.grant("fixer-a", p["id"])
    s.set_muted(True)
    with pytest.raises(Rejected) as error:
        s.grant("fixer-a", p["id"])
    assert error.value.code == "CONTEXT_STALE"
    with pytest.raises(Rejected) as error:
        s.execute("fixer-a", p["id"], g["id"], "stale-transport")
    assert error.value.code == "POLICY_CHANGED"


def test_parallel_apply_is_one_cas_mutation(tmp_path):
    s, r, lease, p = setup(tmp_path)
    g = s.grant("fixer-a", p["id"])
    rev = int(s.current_config()["revision"])
    def apply(index):
        try:
            return s.execute("fixer-a", p["id"], g["id"], str(index))["status"]
        except Rejected as exc:
            return exc.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(apply, range(2)))
    assert sorted(outcomes) == sorted(["applied", "PRECONDITION_FAILED"])
    assert int(s.current_config()["revision"]) == rev + 1


def test_empty_acceptance_cannot_close_even_with_current_revision(tmp_path):
    s, r, lease, p = setup(tmp_path)
    g = s.grant("fixer-a", p["id"])
    result = s.execute("fixer-a", p["id"], g["id"], "apply")
    proof = s.verification("verifier", r["id"], {"passed": True, "checks": [], "tested_revision": result["revision"]})
    assert not proof["passed"]
    assert s.run(r["id"])["status"] != "resolved"


def test_unknown_usage_reservation_still_limits_later_calls(tmp_path):
    s, r, lease, p = setup(tmp_path)
    request = s.reserve_usage(r["id"], 3500, "timeout")
    s.settle_usage(request, None, 60, "ReadTimeout")
    with pytest.raises(Rejected) as error:
        s.reserve_usage(r["id"], 600, "retry")
    assert error.value.code == "TOKEN_BUDGET_EXHAUSTED"
    assert s.run(r["id"])["usage_unknown"]
