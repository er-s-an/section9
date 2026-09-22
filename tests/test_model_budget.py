from __future__ import annotations

import asyncio

import pytest

from s9.store import Rejected, Store


def _run(store: Store, budget: int = 1000) -> str:
    return store.inject("prompt", "swarm", "budget-test", 7, budget)["id"]


def _client(store: Store, monkeypatch: pytest.MonkeyPatch, timeout: float = 0.5):
    # Importing config is safe for this test only after dotenv loading is disabled;
    # no provider or credential is used by reserve().
    import dotenv

    monkeypatch.setattr(dotenv, "load_dotenv", lambda *args, **kwargs: None)
    from s9 import config
    from s9.model import ModelClient

    monkeypatch.setattr(config, "MODEL_TIMEOUT", timeout)
    return ModelClient(store)


@pytest.mark.asyncio
async def test_temporary_reservation_is_released_then_waiter_succeeds(tmp_path, monkeypatch):
    store = Store(tmp_path / "budget.sqlite")
    run_id = _run(store)
    first = store.reserve_usage(run_id, 700, "first")
    client = _client(store, monkeypatch)
    try:
        waiting = asyncio.create_task(client.reserve(run_id, 400, "second"))
        await asyncio.sleep(0.08)
        store.settle_usage(first, {"total_tokens": 300}, 0.01)
        second = await asyncio.wait_for(waiting, 1)
        assert second != first
        store.settle_usage(second, {"total_tokens": 400}, 0.01)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_committed_shortage_is_rejected_without_wait(tmp_path, monkeypatch):
    store = Store(tmp_path / "budget.sqlite")
    run_id = _run(store)
    first = store.reserve_usage(run_id, 900, "first")
    store.settle_usage(first, {"total_tokens": 850}, 0.01)
    client = _client(store, monkeypatch)
    try:
        with pytest.raises(Rejected) as error:
            await asyncio.wait_for(client.reserve(run_id, 200, "too-much"), 0.2)
        assert error.value.code == "TOKEN_BUDGET_EXHAUSTED"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_unknown_usage_is_committed_and_not_counted_as_zero(tmp_path, monkeypatch):
    store = Store(tmp_path / "budget.sqlite")
    run_id = _run(store)
    first = store.reserve_usage(run_id, 600, "timeout")
    store.settle_usage(first, None, 0.01, "ReadTimeout")
    run = store.run(run_id)
    assert run["usage_unknown"] is True
    assert run["unknown_reserved_tokens"] == 600
    client = _client(store, monkeypatch)
    try:
        with pytest.raises(Rejected) as error:
            await client.reserve(run_id, 500, "retry")
        assert error.value.code == "TOKEN_BUDGET_EXHAUSTED"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_reset_invalidates_a_waiting_reservation(tmp_path, monkeypatch):
    store = Store(tmp_path / "budget.sqlite")
    run_id = _run(store)
    store.reserve_usage(run_id, 700, "inflight")
    client = _client(store, monkeypatch)
    try:
        waiting = asyncio.create_task(client.reserve(run_id, 400, "after-reset"))
        await asyncio.sleep(0.08)
        store.reset()
        with pytest.raises(Rejected) as error:
            await asyncio.wait_for(waiting, 1)
        assert error.value.code == "RUN_STALE"
    finally:
        await client.close()
