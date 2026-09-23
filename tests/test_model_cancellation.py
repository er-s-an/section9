from __future__ import annotations

import asyncio
import time

import pytest

from s9.store import Rejected, Store


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


@pytest.mark.asyncio
async def test_terminal_run_denies_reserved_request_waiting_for_provider_slot(tmp_path, monkeypatch):
    import dotenv
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *args, **kwargs: None)
    from s9 import config
    from s9.model import ModelClient
    from s9.model_scheduler import ProviderGateway

    store = Store(tmp_path / "terminal-queued.sqlite")
    run_id = _run(store)
    monkeypatch.setattr(config, "MODEL_KEY", "component-key")
    monkeypatch.setattr(config, "MODEL_TIMEOUT", 1.0)
    gateway = ProviderGateway(capacity=1)
    client = ModelClient(store, gateway=gateway)
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    provider_calls = 0

    async def delayed_post(*args, **kwargs):
        nonlocal provider_calls
        provider_calls += 1
        if provider_calls == 1:
            first_started.set()
            await release_first.wait()
        return Response()

    monkeypatch.setattr(client.client, "post", delayed_post)
    first = asyncio.create_task(client.complete(
        [{"role": "user", "content": "already dispatched"}], run_id=run_id,
        purpose="first", max_tokens=300, generation="1"))
    await asyncio.wait_for(first_started.wait(), 1)
    queued = asyncio.create_task(client.complete(
        [{"role": "user", "content": "must not dispatch"}], run_id=run_id,
        purpose="queued", max_tokens=300, generation="1"))
    deadline = asyncio.get_running_loop().time() + 1
    while len(store.usage_records(run_id)) < 2 and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(.01)
    assert len(store.usage_records(run_id)) == 2, "second request must reserve budget before waiting for the shared slot"

    generation_before = store.run(run_id)["generation"]
    store.fail_run(run_id, "terminal_while_provider_queued")
    assert store.run(run_id)["generation"] == generation_before
    release_first.set()

    assert (await asyncio.wait_for(first, 1))["content"] == "ok"
    with pytest.raises(Rejected):
        await asyncio.wait_for(queued, 1)

    assert provider_calls == 1
    records = {row["purpose"]: row for row in store.usage_records(run_id)}
    assert records["first"]["provider_state"] == "sent"
    assert records["first"]["usage"]["total_tokens"] == 3
    assert records["queued"]["provider_state"] == "not_sent"
    assert records["queued"]["usage"]["total_tokens"] == 0
    assert records["queued"]["unknown"] is False
    assert store.run(run_id)["reserved_tokens"] == 0
    events = store.events(run_id=run_id)
    assert len([event for event in events if event["event_type"] == "model.provider_started"]) == 1
    await client.close()
    await gateway.close()


@pytest.mark.asyncio
async def test_policy_change_while_provider_queued_releases_reservation_without_sending(tmp_path, monkeypatch):
    import dotenv
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *args, **kwargs: None)
    from s9 import config
    from s9.model import ModelClient
    from s9.model_scheduler import ProviderGateway

    store = Store(tmp_path / "policy-queued.sqlite")
    run_id = _run(store)
    monkeypatch.setattr(config, "MODEL_KEY", "component-key")
    monkeypatch.setattr(config, "MODEL_TIMEOUT", 1.0)
    gateway = ProviderGateway(capacity=1)
    client = ModelClient(store, gateway=gateway)
    first_started, release_first = asyncio.Event(), asyncio.Event()
    provider_calls = 0

    async def delayed_post(*args, **kwargs):
        nonlocal provider_calls
        provider_calls += 1
        if provider_calls == 1:
            first_started.set()
            await release_first.wait()
        return Response()

    monkeypatch.setattr(client.client, "post", delayed_post)
    first = asyncio.create_task(client.complete([{"role": "user", "content": "active"}], run_id=run_id,
                                                purpose="first", max_tokens=300, generation="1"))
    await asyncio.wait_for(first_started.wait(), 1)
    queued = asyncio.create_task(client.complete([{"role": "user", "content": "stale policy"}], run_id=run_id,
                                                 purpose="queued", max_tokens=300, generation="1"))
    deadline = asyncio.get_running_loop().time() + 1
    while len(store.usage_records(run_id)) < 2 and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(.01)
    assert len(store.usage_records(run_id)) == 2
    store.set_autonomy("L1")
    release_first.set()

    assert (await asyncio.wait_for(first, 1))["content"] == "ok"
    with pytest.raises(Rejected) as error:
        await asyncio.wait_for(queued, 1)
    assert error.value.code == "POLICY_STALE"
    assert provider_calls == 1
    record = next(row for row in store.usage_records(run_id) if row["purpose"] == "queued")
    assert record["provider_state"] == "not_sent" and record["usage"]["total_tokens"] == 0
    assert store.run(run_id)["reserved_tokens"] == 0
    await client.close()
    await gateway.close()


@pytest.mark.asyncio
async def test_provider_queue_rechecks_budget_after_overrun(tmp_path, monkeypatch):
    import dotenv
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *args, **kwargs: None)
    from s9 import config
    from s9.model import ModelClient
    from s9.model_scheduler import ProviderGateway

    store = Store(tmp_path / "budget-queued.sqlite")
    run_id = store.inject("prompt", "swarm", "budget-test", 8, 1000)["id"]
    monkeypatch.setattr(config, "MODEL_KEY", "component-key")
    monkeypatch.setattr(config, "MODEL_TIMEOUT", 1.0)
    gateway = ProviderGateway(capacity=1)
    client = ModelClient(store, gateway=gateway)
    first_started, release_first = asyncio.Event(), asyncio.Event()
    provider_calls = 0

    class OverBudgetResponse:
        status_code = 200

        @staticmethod
        def json():
            return {"usage": {"total_tokens": 1200},
                    "choices": [{"message": {"content": "provider completed"}}]}

    async def delayed_post(*args, **kwargs):
        nonlocal provider_calls
        provider_calls += 1
        if provider_calls == 1:
            first_started.set()
            await release_first.wait()
            return OverBudgetResponse()
        return Response()

    monkeypatch.setattr(client.client, "post", delayed_post)
    first = asyncio.create_task(client.complete([{"role": "user", "content": "first"}], run_id=run_id,
                                                purpose="first", max_tokens=300, generation="1"))
    await asyncio.wait_for(first_started.wait(), 1)
    queued = asyncio.create_task(client.complete([{"role": "user", "content": "queued"}], run_id=run_id,
                                                 purpose="queued", max_tokens=300, generation="1"))
    deadline = asyncio.get_running_loop().time() + 1
    while len(store.usage_records(run_id)) < 2 and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(.01)
    assert len(store.usage_records(run_id)) == 2
    release_first.set()

    assert (await asyncio.wait_for(first, 1))["content"] == "provider completed"
    with pytest.raises(Rejected) as error:
        await asyncio.wait_for(queued, 1)
    assert error.value.code == "TOKEN_BUDGET_EXHAUSTED"
    assert provider_calls == 1
    run = store.run(run_id)
    assert run["budget_overrun"] is True and run["usage_tokens"] == 1200
    record = next(row for row in store.usage_records(run_id) if row["purpose"] == "queued")
    assert record["provider_state"] == "not_sent" and record["usage"]["total_tokens"] == 0
    assert run["reserved_tokens"] == 0
    await client.close()
    await gateway.close()
