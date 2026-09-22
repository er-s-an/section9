from __future__ import annotations

import pytest

from s9.pairs.contracts import CreatePair
from s9.pairs.coordinator import PairCoordinator
from s9.pairs.runtime import ArmRuntime
from s9.provenance import capture_identity
from s9.store import Rejected


class FakeModel:
    def __init__(self):
        self.calls = 0

    def status(self):
        return {"active_requests": 0}

    async def close(self):
        return None


class FakeCore:
    def __init__(self, runtime):
        self.store = runtime.store
        self.model = FakeModel()
        self.verifying = set()
        self.execution_enabled = False
        self.started = 0
        self.stopped = 0
        self.spawned = []
        self.activated = []

    async def start(self):
        self.started += 1
        self.spawned.append(tuple(self.store.scope["arm"] for _ in [0]))

    async def inject(self, scenario, condition, seed, environment=None, run_id=None, schedule=True):
        self.model.calls += 0
        run = self.store.inject(scenario, condition, "fake", seed, 16000, run_id=run_id)
        return {"run_id": run["id"], "revision": run["injected_revision"]}

    def activate_run(self, run_id):
        self.activated.append(run_id)
        return True

    async def reset(self):
        return self.store.reset()

    async def stop(self):
        self.stopped += 1

    def snapshot(self):
        return {"dependencies": {}}


class FakeTelemetry:
    pass


@pytest.fixture
def coordinator(tmp_path, monkeypatch):
    def prepare(runtime):
        if runtime.core is None:
            runtime.core = FakeCore(runtime)
        return runtime.core

    monkeypatch.setattr(ArmRuntime, "prepare_core", prepare)
    return PairCoordinator(tmp_path / "showcase", object(), FakeTelemetry(), capture_identity())


async def create_pair(coordinator, scenario="prompt", key="key-1"):
    return await coordinator.create(CreatePair(scenario=scenario, seed=7), key=key)


@pytest.mark.asyncio
async def test_create_is_frozen_idempotent_and_has_two_reserved_run_ids(coordinator):
    pair = await create_pair(coordinator)
    same = await create_pair(coordinator)
    assert same["pair_id"] == pair["pair_id"]
    assert pair["status"] == "ready"
    assert pair["swarm_run_id"] != pair["baseline_run_id"]
    assert pair["immutable_spec"]["model_parameters"]["temperature"] == 0
    assert pair["immutable_spec"]["budget_policy"]["total_tokens_per_arm"] > 0
    assert coordinator.runtime(pair, "swarm").store.scope["run_id"] == pair["swarm_run_id"]
    with pytest.raises(Rejected, match="幂等"):
        await coordinator.create(CreatePair(scenario="cost", seed=7), key="key-1")


@pytest.mark.asyncio
async def test_start_is_one_shot_and_releases_both_arms(coordinator):
    pair = await create_pair(coordinator)
    started = await coordinator.start(pair["pair_id"], pair["spec_hash"])
    assert started["status"] == "running"
    assert all(coordinator.runtime(started, arm).core.activated == [started[arm + "_run_id"]]
               for arm in ("swarm", "baseline"))
    with pytest.raises(Rejected) as exc:
        await coordinator.start(pair["pair_id"], pair["spec_hash"])
    assert exc.value.code == "PAIR_NOT_READY"


@pytest.mark.asyncio
async def test_reset_one_arm_preserves_other_and_requires_new_pair(coordinator):
    pair = await create_pair(coordinator)
    await coordinator.start(pair["pair_id"], pair["spec_hash"])
    result = await coordinator.reset(pair["pair_id"], arm="baseline")
    assert result["rerun_requires_new_pair"] is True
    assert coordinator.runtime(pair, "baseline").store.run(pair["baseline_run_id"])["status"] == "reset"
    swarm_run = coordinator.runtime(pair, "swarm").store.run(pair["swarm_run_id"])
    assert swarm_run["status"] == "injected"
    assert coordinator.runtime(pair, "swarm").store.current_config()["prompt_version"] == "degraded"
    with pytest.raises(Rejected):
        await coordinator.start(pair["pair_id"], pair["spec_hash"])
    fresh = await coordinator.create(CreatePair(scenario="prompt", seed=8), key="fresh")
    assert fresh["pair_id"] != pair["pair_id"]


@pytest.mark.asyncio
async def test_start_failure_is_recorded_and_stops_both_arms(coordinator, monkeypatch):
    pair = await create_pair(coordinator)
    original = coordinator.runtime(pair, "baseline").prepare_core()

    async def fail_start():
        raise RuntimeError("start failed")

    original.start = fail_start
    with pytest.raises(RuntimeError):
        await coordinator.start(pair["pair_id"], pair["spec_hash"])
    failed = coordinator.catalog.get(pair["pair_id"])
    assert failed["status"] == "setup_failed"
    assert failed["comparison_integrity"]["eligible"] is False
    assert all(runtime.core is None or runtime.core.stopped >= 1 for runtime in coordinator.runtimes.values())


@pytest.mark.asyncio
async def test_boot_interrupts_running_pair_and_settles_unknown_usage(coordinator):
    pair = await create_pair(coordinator)
    await coordinator.start(pair["pair_id"], pair["spec_hash"])
    for arm in ("swarm", "baseline"):
        runtime = coordinator.runtime(pair, arm)
        usage_id = runtime.store.reserve_usage(pair[arm + "_run_id"], 100, "fake")
        runtime.store.mark_usage_sent(usage_id)
        assert runtime.store.usage_records(pair[arm + "_run_id"])[0]["status"] == "reserved"
    restarted = PairCoordinator(coordinator.root, object(), FakeTelemetry(), capture_identity())
    await restarted.boot()
    interrupted = restarted.catalog.get(pair["pair_id"])
    assert interrupted["status"] == "interrupted"
    for arm in ("swarm", "baseline"):
        run = restarted.runtime(interrupted, arm).store.run(interrupted[arm + "_run_id"])
        assert run["status"] == "failed"
        assert run["usage_unknown"] is True
    await restarted.close()
    await coordinator.close()


@pytest.mark.asyncio
async def test_boot_marks_ready_preparation_interrupted_and_active_pair_is_exclusive(coordinator):
    first = await create_pair(coordinator, key="first")
    second = await create_pair(coordinator, scenario="cost", key="second")
    await coordinator.start(first["pair_id"], first["spec_hash"])
    with pytest.raises(Rejected) as exc:
        await coordinator.start(second["pair_id"], second["spec_hash"])
    assert exc.value.code == "PAIR_ACTIVE"
    restarted = PairCoordinator(coordinator.root, object(), FakeTelemetry(), capture_identity())
    await restarted.boot()
    # The second arm never started, so restart leaves its reserved run IDs
    # without execution records while retaining the catalog interruption.
    recovered = restarted.catalog.get(second["pair_id"])
    assert recovered["status"] == "interrupted"
    assert restarted.runtime(recovered, "swarm").store.run(recovered["swarm_run_id"]) is None
    await restarted.close()
    await coordinator.close()


@pytest.mark.asyncio
async def test_preparation_failure_is_retained_in_catalog(coordinator, monkeypatch):
    original = ArmRuntime.__init__

    def fail_baseline(self, context, *args, **kwargs):
        if context.arm == "baseline":
            raise RuntimeError("baseline preparation failed")
        return original(self, context, *args, **kwargs)

    monkeypatch.setattr(ArmRuntime, "__init__", fail_baseline)
    with pytest.raises(RuntimeError):
        await create_pair(coordinator, key="preparation-failure")
    pair = coordinator.catalog.list()[0]
    assert pair["status"] == "setup_failed"
    assert pair["comparison_integrity"]["eligible"] is False
    assert pair["failure_reason"] == "RuntimeError"


@pytest.mark.asyncio
async def test_shutdown_revokes_survivor_after_single_arm_reset(coordinator):
    pair = await create_pair(coordinator)
    await coordinator.start(pair['pair_id'], pair['spec_hash'])
    await coordinator.reset(pair['pair_id'], arm='swarm')
    assert coordinator.runtime(pair, 'baseline').store.run(pair['baseline_run_id'])['status'] == 'injected'
    await coordinator.close()
    assert coordinator.runtime(pair, 'baseline').store.run(pair['baseline_run_id'])['status'] == 'reset'
