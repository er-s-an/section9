"""Shared, fair provider admission for model requests."""
from __future__ import annotations

import asyncio
from collections import deque
from contextlib import asynccontextmanager

import httpx

from s9 import config


class ProviderGateway:
    """A single provider client with FIFO round-robin admission by scope key."""

    def __init__(self, *, capacity: int | None = None, client: httpx.AsyncClient | None = None):
        self.capacity = max(1, int(capacity if capacity is not None else config.MODEL_CONCURRENCY))
        self.client = client or httpx.AsyncClient(timeout=httpx.Timeout(config.MODEL_TIMEOUT, connect=10), trust_env=True)
        self._owns_client = client is None
        self._active = 0
        self._queues: dict[object, deque[asyncio.Future]] = {}
        self._order: deque[object] = deque()
        self._lock = asyncio.Lock()

    def _pump_locked(self):
        while self._active < self.capacity and self._order:
            selected = None
            for _ in range(len(self._order)):
                key = self._order.popleft()
                queue = self._queues.get(key)
                if queue:
                    selected = (key, queue)
                    break
                self._queues.pop(key, None)
            if selected is None:
                return
            key, queue = selected
            waiter = queue.popleft()
            if queue:
                self._order.append(key)
            else:
                self._queues.pop(key, None)
            if waiter.cancelled():
                continue
            self._active += 1
            waiter.set_result(None)

    @asynccontextmanager
    async def slot(self, key):
        loop = asyncio.get_running_loop()
        waiter = loop.create_future()
        granted = False
        async with self._lock:
            self._queues.setdefault(key, deque()).append(waiter)
            if key not in self._order:
                self._order.append(key)
            self._pump_locked()
        try:
            await waiter
            granted = True
        except BaseException:
            async with self._lock:
                queue = self._queues.get(key)
                if queue and waiter in queue:
                    queue.remove(waiter)
                    if not queue:
                        self._queues.pop(key, None)
                if granted or (waiter.done() and not waiter.cancelled()):
                    self._active = max(0, self._active - 1)
                self._pump_locked()
            raise
        try:
            yield
        finally:
            async with self._lock:
                self._active = max(0, self._active - 1)
                self._pump_locked()

    async def close(self):
        if self._owns_client:
            await self.client.aclose()
