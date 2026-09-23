from __future__ import annotations

import asyncio

import httpx
import pytest

from s9 import config
from s9.model import ModelClient, ModelFailure
from s9.model_scheduler import ProviderGateway
from s9.store import Store


def _run(store, name="budget-test", budget=16000):
    return store.inject("prompt", "swarm", name, 7, budget)["id"]


def _scoped_store(path, arm):
    run_id = arm + "-run"
    scope = {"pair_id": "p", "run_id": run_id, "arm": arm}
    return Store(path, scope=scope)


def _response(text="ok", tokens=3):
    return httpx.Response(200, json={"model": "test-model", "usage": {"total_tokens": tokens},
                                     "choices": [{"message": {"content": text}}]})


@pytest.mark.asyncio
async def test_gateway_capacity_and_round_robin_at_queued_boundary():
    gateway = ProviderGateway(capacity=1, client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: _response())))
    order, release = [], asyncio.Event()

    async def worker(key, label):
        async with gateway.slot(key):
            order.append(label)
            if len(order) < 3:
                await release.wait()

    tasks = [asyncio.create_task(worker("a", "a1"))]
    await asyncio.sleep(0)
    tasks += [asyncio.create_task(worker("b", "b1")), asyncio.create_task(worker("a", "a2")),
              asyncio.create_task(worker("b", "b2"))]
    await asyncio.wait_for(asyncio.sleep(0), 1)
    assert order == ["a1"]
    release.set()
    await asyncio.wait_for(asyncio.gather(*tasks), 1)
    assert order == ["a1", "b1", "a2", "b2"]
    await gateway.close()


@pytest.mark.asyncio
async def test_gateway_cancel_after_admission_does_not_leak_slot():
    gateway = ProviderGateway(capacity=1)
    entered = asyncio.Event()

    async def first():
        async with gateway.slot("a"):
            entered.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(first())
    await asyncio.wait_for(entered.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    async with asyncio.timeout(1):
        async with gateway.slot("b"):
            pass
    await gateway.close()


@pytest.mark.asyncio
async def test_pair_generation_cancel_isolated_and_baseline_usage_known(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MODEL_KEY", "test-key")
    monkeypatch.setattr(config, "MODEL_CONCURRENCY", 2)
    store = _scoped_store(tmp_path / "pair.sqlite", "baseline")
    swarm_store = _scoped_store(tmp_path / "swarm.sqlite", "swarm")
    baseline_run = _run(store)
    swarm_run = _run(swarm_store)
    release_swarm = asyncio.Event()

    async def handler(request):
        if b"swarm" in request.content:
            await release_swarm.wait()
            return _response("swarm", 9)
        return _response("baseline", 4)

    transport = httpx.MockTransport(handler)
    gateway = ProviderGateway(capacity=2, client=httpx.AsyncClient(transport=transport))
    baseline = ModelClient(store, gateway=gateway, scope=store.scope)
    swarm = ModelClient(swarm_store, gateway=gateway, scope=swarm_store.scope)
    try:
        base_task = asyncio.create_task(baseline.complete([{"role": "user", "content": "baseline"}], run_id=baseline_run, generation="1"))
        swarm_task = asyncio.create_task(swarm.complete([{"role": "user", "content": "swarm"}], run_id=swarm_run, generation="1"))
        base = await asyncio.wait_for(base_task, 1)
        await asyncio.sleep(0)
        await swarm.cancel_generation("1")
        with pytest.raises(ModelFailure, match="MODEL_CANCELLED"):
            await swarm_task
        assert base["usage_unknown"] is False
        assert store.run(baseline_run)["usage_unknown"] is False
        assert swarm_store.run(swarm_run)["usage_unknown"] is True
    finally:
        release_swarm.set()
        await gateway.close()


@pytest.mark.asyncio
async def test_queued_cancel_never_calls_provider_and_settles_known_zero(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MODEL_KEY", "test-key")
    store = _scoped_store(tmp_path / "queued.sqlite", "queued")
    run_id = _run(store)
    calls = 0

    async def handler(request):
        nonlocal calls
        calls += 1
        return _response()

    gateway = ProviderGateway(capacity=1, client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    hold = asyncio.create_task(_hold_slot(gateway))
    await asyncio.sleep(0)
    client = ModelClient(store, gateway=gateway, scope=store.scope)
    task = asyncio.create_task(client.complete([{"role": "user", "content": "queued"}], run_id=run_id, generation="1"))
    await asyncio.sleep(.05)
    await client.cancel_generation("1")
    with pytest.raises(ModelFailure, match="MODEL_CANCELLED"):
        await task
    hold.cancel()
    with pytest.raises(asyncio.CancelledError):
        await hold
    record = store.usage_records(run_id)[0]
    assert calls == 0
    assert record["unknown"] is False and record["usage"]["total_tokens"] == 0
    await gateway.close()


async def _hold_slot(gateway):
    async with gateway.slot("holder"):
        await asyncio.Event().wait()


@pytest.mark.asyncio
async def test_budget_exhaustion_in_one_arm_does_not_consume_other_arm(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MODEL_KEY", "test-key")
    store = _scoped_store(tmp_path / "budget.sqlite", "baseline")
    exhausted = _run(store, budget=100)
    gateway = ProviderGateway(capacity=2, client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: _response())))
    first = store.reserve_usage(exhausted, 100, "held")
    healthy_store = _scoped_store(tmp_path / "healthy.sqlite", "swarm")
    healthy = _run(healthy_store, budget=1000)
    a = ModelClient(store, gateway=gateway, scope=store.scope)
    b = ModelClient(healthy_store, gateway=gateway, scope=healthy_store.scope)
    try:
        with pytest.raises(Exception):
            await a.complete([{"role": "user", "content": "a"}], run_id=exhausted, max_tokens=1, generation="1")
        result = await asyncio.wait_for(b.complete([{"role": "user", "content": "b"}], run_id=healthy, max_tokens=1, generation="1"), 1)
        assert result["content"] == "ok"
        store.settle_usage(first, {"total_tokens": 0}, .01)
    finally:
        await gateway.close()


@pytest.mark.asyncio
async def test_provider_missing_model_identity_is_unknown_not_requested_alias(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'MODEL_KEY', 'test-key')
    store = Store(tmp_path / 'unknown-identity.sqlite')
    rid = _run(store)
    provider = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={
        'choices': [{'message': {'content': 'ok'}}], 'usage': {'total_tokens': 3}})))
    gateway = ProviderGateway(capacity=1, client=provider)
    client = ModelClient(store, gateway=gateway)
    try:
        result = await client.complete([{'role': 'user', 'content': 'hi'}], run_id=rid)
        assert result['model'] is None
        terminal = [e for e in store.events(run_id=rid) if e['event_type'] == 'model.completed' and e['payload'].get('request_id')]
        assert len(terminal) == 1 and terminal[0]['payload']['returned_model'] is None
    finally:
        await provider.aclose()
