from __future__ import annotations


import pytest

from s9.core import Core
from s9.store import Store


class FakeModel:
    async def close(self):
        return None


class FakeTelemetry:
    def flush(self):
        return None


def make_core(tmp_path, arm="swarm", **kwargs):
    scope = {"pair_id": "pair-1", "run_id": "run-1", "arm": arm, "spec_hash": "spec-1"}
    return Core(data_dir=tmp_path, store=Store(tmp_path / "section9.sqlite"),
                model=FakeModel(), telemetry=FakeTelemetry(), scope=scope, **kwargs)


def test_pair_cores_have_independent_data_dirs_and_worker_sets(tmp_path):
    swarm = make_core(tmp_path / "swarm")
    baseline = make_core(tmp_path / "baseline", arm="baseline")
    assert swarm.data_dir != baseline.data_dir
    assert swarm.worker_ids == ["sentry", "diagnoser", "fixer-a", "fixer-b", "verifier"]
    assert baseline.worker_ids == ["sentry", "single"]
    assert swarm.memory.base_dir != baseline.memory.base_dir
    assert swarm.runtime != baseline.runtime


def test_context_is_barrier_gated(tmp_path):
    core = make_core(tmp_path)
    token = core.store.register("sentry")
    core.store.inject("prompt", "swarm", "fake", 42, 1000)
    core.execution_enabled = False
    context = core.context(core.store.authenticate(token))
    assert context["execution_enabled"] is False
    assert context["tasks"] == []
    assert context["incident"] is None


@pytest.mark.asyncio
async def test_activate_run_schedules_once_and_verify_event_is_ordered(tmp_path, monkeypatch):
    core = make_core(tmp_path)
    calls = []

    def fake_job(coro, generation=None):
        calls.append((coro, generation))
        coro.close()
        return None

    monkeypatch.setattr(core, "job", fake_job)
    result = await core.inject("prompt", run_id="reserved-run", schedule=False)
    assert calls == []
    assert core.activate_run(result["run_id"]) is True
    assert core.activate_run(result["run_id"]) is False
    assert len(calls) == 1
