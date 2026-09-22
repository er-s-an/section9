"""SQLite authority boundaries for paired Section9 stores.

These tests deliberately use no model or service calls: a scope violation must
be rejected by the local transactional authority before any mutation.
"""
import hashlib
import json

import pytest

from s9.store import DEFAULT_CONFIG, Rejected, Store


BASELINE = {**DEFAULT_CONFIG, "prompt_version": "healthy", "max_output_tokens": 2048}


def _store(tmp_path, arm, run_id, baseline=BASELINE):
    scope = {"pair_id": "pair-1", "run_id": run_id, "arm": arm, "spec_hash": "spec-1"}
    return Store(tmp_path / f"{arm}.sqlite", scope=scope, baseline_config=baseline)


def _content_hash(value):
    value = {k: v for k, v in value.items() if k not in {"revision", "generation"}}
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _repair_fixture(store):
    for aid in ["fixer-a", "verifier"]:
        store.register(aid)
    run = store.inject("prompt", "swarm", "component", 42, 4000)
    observation = store.emit("observation.probe", {"signal": "quality_mismatch"}, run_id=run["id"], producer="sentry")
    store.open_incident([observation["event_id"]])
    with store.tx() as db:
        task = next(json.loads(row[0]) for row in db.execute("SELECT data FROM tasks") if json.loads(row[0])["kind"] == "repair")
        agent = store.get(db, "agents", "fixer-a")
        current_run = store.get(db, "runs", run["id"])
        transport = str(store.meta(db, "transport_epoch"))
    lease = store.claim("fixer-a", task["id"])
    plan = store.create_plan("fixer-a", {"run_id": run["id"], "task_id": task["id"], "task_epoch": lease["epoch"],
        "instance_id": agent["instance_id"], "generation": current_run["generation"], "transport_epoch": transport,
        "expected_revision": store.current_config()["revision"],
        "actions": [{"type": "set_prompt_revision", "values": {"prompt_version": "healthy"}}], "rationale": "restore"})
    grant = store.grant("fixer-a", plan["id"])
    return run, task, plan, grant


def test_pair_stores_have_same_frozen_baseline_content_hash(tmp_path):
    baseline = _store(tmp_path, "baseline", "run-baseline")
    injected = _store(tmp_path, "swarm", "run-swarm")
    assert _content_hash(baseline.current_config()) == _content_hash(injected.current_config()) == _content_hash(BASELINE)
    baseline.inject("prompt", "baseline", "component", 42, 4000)
    injected.inject("prompt", "swarm", "component", 42, 4000)
    assert _content_hash(baseline.current_config()) == _content_hash(injected.current_config())


def test_valid_swarm_repair_does_not_mutate_frozen_baseline_or_fence(tmp_path):
    baseline = _store(tmp_path, "baseline", "run-baseline")
    swarm = _store(tmp_path, "swarm", "run-swarm")
    baseline.inject("prompt", "single", "baseline", 42, 4000, run_id="run-baseline")
    run, task, plan, grant = _repair_fixture(swarm)
    before_baseline_config = dict(baseline.current_config())
    before_baseline_revision = baseline.current_config()["revision"]
    with baseline.tx() as db:
        before_baseline_fence = store_fence = baseline.meta(db, "resource_fence")
    result = swarm.execute("fixer-a", plan["id"], grant["id"], "apply-once")
    assert result["status"] == "applied"
    assert baseline.current_config() == before_baseline_config
    assert baseline.current_config()["revision"] == before_baseline_revision
    with baseline.tx() as db:
        assert baseline.meta(db, "resource_fence") == before_baseline_fence == store_fence
    assert swarm.current_config()["prompt_version"] == "healthy"
    assert int(swarm.current_config()["revision"]) == int(run["injected_revision"]) + 1


def test_wrong_tested_config_hash_cannot_resolve_one_arm_or_mutate_other(tmp_path):
    baseline = _store(tmp_path, "baseline", "run-baseline")
    swarm = _store(tmp_path, "swarm", "run-swarm")
    baseline.inject("prompt", "single", "baseline", 42, 4000, run_id="run-baseline")
    run, _, plan, grant = _repair_fixture(swarm)
    applied = swarm.execute("fixer-a", plan["id"], grant["id"], "apply-before-verify")
    before_baseline = dict(baseline.current_config())
    with baseline.tx() as db:
        baseline_fence = baseline.meta(db, "resource_fence")
    required = ["heldout_semantic_policy", "unaffected_product_fact", "terminal_tool_stops",
                "cost_budget", "no_stalled_requests", "heldout_outside_return_window"]
    result = swarm.verification("verifier", run["id"], {
        "passed": True,
        "tested_revision": applied["revision"],
        "tested_config_hash": "deliberately-wrong-hash",
        "checks": [{"name": name, "passed": True} for name in required],
    })
    assert result["passed"] is False
    assert swarm.run(run["id"])["status"] == "failed"
    assert baseline.current_config() == before_baseline
    with baseline.tx() as db:
        assert baseline.meta(db, "resource_fence") == baseline_fence


def test_cross_arm_token_and_foreign_task_are_rejected_without_writes(tmp_path):
    baseline = _store(tmp_path, "baseline", "run-baseline")
    swarm = _store(tmp_path, "swarm", "run-swarm")
    token = baseline.register("fixer-a")
    swarm.register("fixer-a")
    with pytest.raises(Rejected) as error:
        swarm.authenticate(token)
    assert error.value.code == "UNAUTHENTICATED"
    run, task, plan, grant = _repair_fixture(baseline)
    before = len(swarm.events())
    with pytest.raises(Rejected):
        swarm.claim("fixer-a", task["id"])
    assert len(swarm.events()) == before


def test_copied_records_with_colliding_epochs_fail_scope_validation(tmp_path):
    source = _store(tmp_path, "baseline", "run-baseline")
    target = _store(tmp_path, "swarm", "run-swarm")
    _, task, plan, grant = _repair_fixture(source)
    for record in (task, plan, grant):
        with pytest.raises(Rejected) as error:
            target._valid_scope(record)
        assert error.value.code == "SCOPE_MISMATCH"


def test_reset_restores_only_target_frozen_baseline(tmp_path):
    healthy = {**BASELINE, "max_output_tokens": 1024}
    baseline = _store(tmp_path, "baseline", "run-baseline", healthy)
    swarm = _store(tmp_path, "swarm", "run-swarm", BASELINE)
    baseline.inject("prompt", "baseline", "component", 42, 4000)
    swarm.inject("prompt", "swarm", "component", 42, 4000)
    baseline.reset()
    swarm.reset()
    assert baseline.current_config()["max_output_tokens"] == 1024
    assert swarm.current_config()["max_output_tokens"] == 2048
    assert _content_hash(baseline.current_config()) != _content_hash(swarm.current_config())


def test_per_run_budgets_and_unknown_settlement_stay_in_scope(tmp_path):
    a = _store(tmp_path, "baseline", "run-a")
    b = _store(tmp_path, "swarm", "run-b")
    ra = a.inject("prompt", "baseline", "component", 42, 1000)
    rb = b.inject("prompt", "swarm", "component", 42, 1000)
    ua = a.reserve_usage(ra["id"], 900, "a")
    ub = b.reserve_usage(rb["id"], 100, "b")
    a.settle_usage(ua, None, .01, "provider_cancelled")
    b.settle_usage(ub, {"total_tokens": 50}, .01)
    assert a.run(ra["id"])["usage_unknown"] is True
    assert a.run(ra["id"])["unknown_reserved_tokens"] == 900
    assert b.run(rb["id"])["usage_unknown"] is False
    assert b.run(rb["id"])["usage_tokens"] == 50
