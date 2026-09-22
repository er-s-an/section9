from __future__ import annotations

import asyncio
import time

import pytest

from s9.store import Store


class Response:
    status_code = 200

    @staticmethod
    def json():
        return {"model": "test-model", "usage": {"total_tokens": 3},
                "choices": [{"message": {"content": "ok"}}]}


def setup_client(store, monkeypatch):
    import dotenv
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *args, **kwargs: None)
    from s9 import config
    from s9.model import ModelClient

    monkeypatch.setattr(config, "MODEL_KEY", "component-key")
    monkeypatch.setattr(config, "MODEL_TIMEOUT", 1.0)
    monkeypatch.setattr(config, "MODEL_CONCURRENCY", 2)
    return ModelClient(store), config


def _run(store, generation="old"):
    return store.inject("prompt", "swarm", "test-model", 42, 16000)["id"]


@pytest.mark.asyncio
async def test_cancel_old_generation_releases_two_http_slots_and_new_generation_starts(tmp_path, monkeypatch):
    store = Store(tmp_path / "cancel.sqlite")
    run_id = _run(store)
    client, config = setup_client(store, monkeypatch)
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def delayed(*args, **kwargs):
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return Response()

    monkeypatch.setattr(client.client, "post", delayed)
    old = [asyncio.create_task(client.complete([{"role": "user", "content": "x"}], run_id=run_id,
                                                max_tokens=300, generation="1")) for _ in range(2)]
    await asyncio.wait_for(started.wait(), 1)
    while calls < 2:
        await asyncio.sleep(0)
    store.reset()
    result = await client.cancel_generation("1", timeout=1)
    assert result["remaining"] == 0
    with pytest.raises(Exception, match="MODEL_CANCELLED"):
        await asyncio.gather(*old)
    records = store.usage_records(run_id)
    assert len(records) == 2
    assert all(item["unknown"] is True for item in records)
    assert store.run(run_id)["unknown_reserved_tokens"] > 0

    # The new generation has a new task set and does not wait for old HTTP.
    new_run = _run(store)
    release.set()
    begin = time.monotonic()
    value = await asyncio.wait_for(client.complete([{"role": "user", "content": "new"}], run_id=new_run,
                                                    max_tokens=300), 1)
    assert time.monotonic() - begin < 1
    assert value["content"] == "ok"
    assert calls == 3
    await client.close()


@pytest.mark.asyncio
async def test_cancel_waiting_semaphore_never_creates_provider_usage(tmp_path, monkeypatch):
    store = Store(tmp_path / "semaphore.sqlite")
    run_id = _run(store)
    client, _ = setup_client(store, monkeypatch)
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def delayed(*args, **kwargs):
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return Response()

    monkeypatch.setattr(client.client, "post", delayed)
    tasks = [asyncio.create_task(client.complete([{"role": "user", "content": "x"}], run_id=run_id,
                                                  max_tokens=300, generation="1")) for _ in range(3)]
    await asyncio.wait_for(started.wait(), 1)
    while calls < 2:
        await asyncio.sleep(0)
    await client.cancel_generation("1", timeout=1)
    release.set()
    with pytest.raises(Exception):
        await asyncio.gather(*tasks)
    assert calls == 2
    assert len(store.usage_records(run_id)) == 2
    await client.close()


@pytest.mark.asyncio
async def test_cancel_waiting_budget_preserves_existing_unknown_reservation(tmp_path, monkeypatch):
    store = Store(tmp_path / "budget-cancel.sqlite")
    run_id = _run(store, "1")
    held = store.reserve_usage(run_id, 15000, "held")
    client, _ = setup_client(store, monkeypatch)
    task = asyncio.create_task(client.complete([{"role": "user", "content": "waiting"}], run_id=run_id,
                                                max_tokens=1000, generation="1"))
    await asyncio.sleep(.08)
    result = await client.cancel_generation("1", timeout=1)
    assert result["remaining"] == 0
    with pytest.raises(Exception, match="MODEL_CANCELLED"):
        await task
    # The waiting request never reserved or called the provider; the existing
    # reservation remains until its own settlement and is not zeroed.
    assert len(store.usage_records(run_id)) == 1
    store.settle_usage(held, None, .01, "provider_cancelled")
    assert store.run(run_id)["unknown_reserved_tokens"] == 15000
    await client.close()


@pytest.mark.asyncio
async def test_external_task_cancellation_closes_http_and_keeps_async_cancel(tmp_path, monkeypatch):
    store = Store(tmp_path / "external-cancel.sqlite")
    run_id = _run(store)
    client, _ = setup_client(store, monkeypatch)
    closed = asyncio.Event()

    async def hanging(*args, **kwargs):
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            closed.set()
            raise

    monkeypatch.setattr(client.client, "post", hanging)
    task = asyncio.create_task(client.complete([{"role": "user", "content": "x"}], run_id=run_id,
                                                max_tokens=300, generation="1"))
    await asyncio.sleep(.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert closed.is_set()
    assert store.usage_records(run_id)[0]["unknown"] is True
    await client.close()
