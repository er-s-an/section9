from __future__ import annotations

import asyncio
import json
import time

import httpx
import pytest

from s9.core import Core
from s9.store import Store
from s9.victim import VictimApp


class DelayedTransport:
    def __init__(self):
        self.started = 0
        self.gate = asyncio.Event()
        self.new_started = asyncio.Event()

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        if any("new generation" in str(message.get("content")) for message in payload["messages"]):
            self.new_started.set()
            return httpx.Response(200, json={"model": "component-model", "usage": {"total_tokens": 4},
                                             "choices": [{"message": {"content": "new generation answer"}}]})
        self.started += 1
        await self.gate.wait()
        return httpx.Response(200, json={"model": "component-model", "usage": {"total_tokens": 4},
                                         "choices": [{"message": {"content": "old answer"}}]})


def _core(tmp_path, monkeypatch):
    import dotenv
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *args, **kwargs: None)
    from s9 import config
    from s9.model import ModelClient

    monkeypatch.setattr(config, "MODEL_KEY", "component-key")
    monkeypatch.setattr(config, "MODEL_TIMEOUT", 1.0)
    monkeypatch.setattr(config, "MODEL_CONCURRENCY", 2)
    store = Store(tmp_path / "reset.sqlite")
    model = ModelClient(store)
    victim = VictimApp(model, store.current_config, lambda *args, **kwargs: asyncio.sleep(0))
    core = Core.__new__(Core)
    core.store, core.model, core.victim = store, model, victim
    core.jobs, core.job_generations = set(), {}
    core.progress, core.pending_detection, core.probe_schedule = {}, set(), {}
    core.children = {}
    return core, config


@pytest.mark.asyncio
async def test_core_reset_cancels_two_http_chats_and_new_generation_is_immediate(tmp_path, monkeypatch):
    core, _ = _core(tmp_path, monkeypatch)
    transport = DelayedTransport()
    await core.model.client.aclose()
    core.model.client = httpx.AsyncClient(transport=httpx.MockTransport(transport), trust_env=False)
    run = core.store.inject("prompt", "swarm", "component-model", 42, 16000)
    old_generation = core.store.current_config()["generation"]
    old_tasks = [core.job(core.victim.chat("old generation request", run_id=run["id"]), old_generation) for _ in range(2)]
    deadline = time.monotonic() + 1
    while transport.started < 2 and time.monotonic() < deadline:
        await asyncio.sleep(.005)
    assert transport.started == 2

    reset_started = time.monotonic()
    reset_result = await core.reset()
    assert time.monotonic() - reset_started < 1
    results = await asyncio.gather(*old_tasks)
    assert all(result["status"] == "cancelled" for result in results)
    assert reset_result["model_cancellation"]["remaining"] == 0
    assert core.model.status()["active_requests"] == 0
    usage = core.store.usage_records(run["id"])
    assert len(usage) == 2
    assert all(item["unknown"] is True for item in usage)
    assert core.store.run(run["id"])["unknown_reserved_tokens"] > 0

    new_run = core.store.inject("prompt", "swarm", "component-model", 42, 16000)
    new_task = core.job(core.victim.chat("new generation request", run_id=new_run["id"]))
    new_result = await asyncio.wait_for(new_task, 1)
    assert transport.new_started.is_set()
    assert new_result["status"] == "success"

    calls_before = transport.started
    with pytest.raises(Exception, match="MODEL_CANCELLED"):
        await core.model.complete([{"role": "user", "content": "late old generation"}],
                                  run_id=new_run["id"], generation=old_generation)
    assert transport.started == calls_before
    await core.model.close()


@pytest.mark.asyncio
async def test_core_reset_exits_waiting_semaphore_chat_without_provider_usage(tmp_path, monkeypatch):
    core, _ = _core(tmp_path, monkeypatch)
    transport = DelayedTransport()
    await core.model.client.aclose()
    core.model.client = httpx.AsyncClient(transport=httpx.MockTransport(transport), trust_env=False)
    run = core.store.inject("prompt", "swarm", "component-model", 42, 16000)
    generation = core.store.current_config()["generation"]
    tasks = [core.job(core.victim.chat("old generation request", run_id=run["id"]), generation) for _ in range(3)]
    deadline = time.monotonic() + 1
    while transport.started < 2 and time.monotonic() < deadline:
        await asyncio.sleep(.005)
    assert transport.started == 2
    await core.reset()
    results = await asyncio.gather(*tasks)
    assert all(result["status"] == "cancelled" for result in results)
    assert len(core.store.usage_records(run["id"])) == 2
    assert core.model.status()["active_requests"] == 0
    await core.model.close()


@pytest.mark.asyncio
async def test_core_reset_exits_budget_waiter_and_keeps_existing_unknown_reservation(tmp_path, monkeypatch):
    core, _ = _core(tmp_path, monkeypatch)
    run = core.store.inject("prompt", "swarm", "component-model", 42, 1000)
    held = core.store.reserve_usage(run["id"], 900, "held")
    generation = core.store.current_config()["generation"]
    waiter = core.job(core.model.complete([{"role": "user", "content": "budget waiter"}], run_id=run["id"],
                                           max_tokens=500, generation=generation), generation)
    await asyncio.sleep(.08)
    await core.reset()
    waiter_result = await asyncio.gather(waiter, return_exceptions=True)
    assert isinstance(waiter_result[0], (asyncio.CancelledError, Exception))
    assert len(core.store.usage_records(run["id"])) == 1
    core.store.settle_usage(held, None, .01, "provider_cancelled")
    assert core.store.run(run["id"])["unknown_reserved_tokens"] == 900
    await core.model.close()
