from __future__ import annotations

import asyncio

import pytest

from s9.core import Core
from s9.pairs.coordinator import PairCoordinator
from s9.store import Store


def test_new_reservations_are_marked_not_sent_and_recover_as_known_zero(tmp_path):
    store = Store(tmp_path / "recovery.sqlite")
    run = store.inject("prompt", "swarm", "component", 1, 1000)
    usage_id = store.reserve_usage(run["id"], 120, "probe")
    record = store.usage_records(run["id"])[0]
    assert record["provider_state"] == "not_sent"
    assert store.recover_usage(run["id"]) == {"recovered": 1, "known_zero": 1, "unknown": 0}
    record = store.usage_records(run["id"])[0]
    assert record["unknown"] is False and record["usage"]["total_tokens"] == 0
    assert store.run(run["id"]).get("unknown_reserved_tokens", 0) == 0
    assert store.recover_usage(run["id"]) == {"recovered": 0, "known_zero": 0, "unknown": 0}
    assert usage_id == record["id"]


def test_mark_usage_sent_before_network_is_conservatively_unknown_on_recovery(tmp_path):
    store = Store(tmp_path / "recovery.sqlite")
    run = store.inject("prompt", "swarm", "component", 1, 1000)
    usage_id = store.reserve_usage(run["id"], 180, "chat")
    store.mark_usage_sent(usage_id)
    assert store.usage_records(run["id"])[0]["provider_state"] == "sent"
    assert store.recover_usage(run["id"]) == {"recovered": 1, "known_zero": 0, "unknown": 1}
    record = store.usage_records(run["id"])[0]
    assert record["unknown"] is True and record["usage"] is None
    assert store.run(run["id"])["unknown_reserved_tokens"] == 180


def test_legacy_reserved_row_without_provider_state_is_unknown_and_idempotent(tmp_path):
    store = Store(tmp_path / "legacy.sqlite")
    run = store.inject("prompt", "swarm", "component", 1, 1000)
    usage_id = store.reserve_usage(run["id"], 90, "legacy")
    with store.tx() as db:
        row = store.get(db, "usage", usage_id)
        row.pop("provider_state", None)
        store.save(db, "usage", row)
    assert store.recover_usage(run["id"]) == {"recovered": 1, "known_zero": 0, "unknown": 1}
    assert store.run(run["id"])["unknown_reserved_tokens"] == 90
    assert store.recover_usage(run["id"]) == {"recovered": 0, "known_zero": 0, "unknown": 0}


def test_recovery_is_scoped_to_store_and_arm(tmp_path):
    swarm = Store(tmp_path / "swarm.sqlite", scope={"pair_id": "p", "run_id": "swarm-run", "arm": "swarm", "spec_hash": "s"})
    baseline = Store(tmp_path / "baseline.sqlite", scope={"pair_id": "p", "run_id": "base-run", "arm": "baseline", "spec_hash": "s"})
    swarm_run = swarm.inject("prompt", "swarm", "component", 1, 1000)
    base_run = baseline.inject("prompt", "single", "component", 1, 1000)
    swarm_id = swarm.reserve_usage(swarm_run["id"], 30, "swarm")
    baseline_id = baseline.reserve_usage(base_run["id"], 40, "base")
    baseline.mark_usage_sent(baseline_id)
    assert swarm.recover_usage() == {"recovered": 1, "known_zero": 1, "unknown": 0}
    assert swarm.usage_records(swarm_run["id"])[0]["unknown"] is False
    assert baseline.usage_records(base_run["id"])[0]["status"] == "reserved"
    assert baseline.recover_usage() == {"recovered": 1, "known_zero": 0, "unknown": 1}
    assert baseline.usage_records(base_run["id"])[0]["unknown"] is True


@pytest.mark.asyncio
async def test_core_start_calls_recovery_helper_before_workers(tmp_path, monkeypatch):
    store = Store(tmp_path / "core-start.sqlite")
    calls = []
    original = store.recover_usage

    def recover(*args, **kwargs):
        calls.append((args, kwargs))
        return original(*args, **kwargs)

    monkeypatch.setattr(store, "recover_usage", recover)
    core = Core(data_dir=tmp_path, store=store, model=object(), worker_ids=[])
    core.job = lambda coroutine, generation=None: (coroutine.close(), None)[1]
    await core.start()
    assert calls and calls[0][1] == {"reason": "SERVER_RESTARTED"}


@pytest.mark.asyncio
async def test_pair_boot_uses_each_arm_store_recovery_helper(tmp_path):
    calls = []
    class StoreStub:
        def run(self, run_id):
            return {"status": "running"}
        def fail_run(self, run_id, reason):
            calls.append(("fail", run_id, reason))
        def recover_usage(self, **kwargs):
            calls.append(("recover", kwargs))
        def emit(self, *args, **kwargs):
            pass
    class Runtime:
        def __init__(self, store):
            self.store = store
    class Journal:
        def ingest(self, *args):
            pass
    class Catalog:
        def list(self):
            return [{"pair_id": "p", "status": "ready", "swarm_run_id": "swarm-run", "baseline_run_id": "base-run"}]
        def update(self, *args, **kwargs):
            return None

    coordinator = PairCoordinator.__new__(PairCoordinator)
    coordinator.catalog = Catalog()
    coordinator.journal = Journal()
    coordinator.runtimes = {("p", "swarm"): Runtime(StoreStub()), ("p", "baseline"): Runtime(StoreStub())}
    coordinator.runtime = lambda pair, arm: coordinator.runtimes[(pair["pair_id"], arm)]
    coordinator.task = None
    coordinator.lock = asyncio.Lock()
    await coordinator.boot()
    assert [entry for entry in calls if entry[0] == "recover"] == [
        ("recover", {"reason": "SERVER_RESTARTED"}),
        ("recover", {"reason": "SERVER_RESTARTED"}),
    ]
    coordinator.task.cancel()
    await asyncio.gather(coordinator.task, return_exceptions=True)
