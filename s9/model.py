from __future__ import annotations

import asyncio
import json
import os
import re
import time

import httpx

from s9 import config
from s9.store import Rejected


class ModelFailure(Exception):
    pass


class ModelClient:
    def __init__(self, store, telemetry=None):
        self.store, self.telemetry = store, telemetry
        self.semaphore = asyncio.Semaphore(config.MODEL_CONCURRENCY)
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(config.MODEL_TIMEOUT, connect=10), trust_env=True)
        self.last_status = "configured" if config.MODEL_KEY else "unconfigured"
        self.last_error = None
        self.last_at = None
        self._generation_tasks: dict[str, set[asyncio.Task]] = {}
        self._cancelled_generations: set[str] = set()
        self._tasks_lock = asyncio.Lock()

    def _current_generation(self) -> str:
        with self.store.tx() as db:
            return str(self.store.meta(db, "generation"))

    def _ensure_generation(self, generation: str) -> None:
        if generation in self._cancelled_generations or self._current_generation() != str(generation):
            raise ModelFailure(f"MODEL_CANCELLED: generation {generation}")

    async def _track(self, generation: str, task: asyncio.Task) -> None:
        async with self._tasks_lock:
            self._generation_tasks.setdefault(generation, set()).add(task)

    async def _untrack(self, generation: str, task: asyncio.Task) -> None:
        async with self._tasks_lock:
            tasks = self._generation_tasks.get(generation)
            if tasks is not None:
                tasks.discard(task)
                if not tasks:
                    self._generation_tasks.pop(generation, None)

    async def reserve(self, run_id, reservation, purpose):
        # A concurrent request may temporarily occupy budget that is returned
        # on settlement. Queue for that settlement instead of falsely treating
        # contention as spent tokens. Never retry a provider request here.
        deadline = time.monotonic() + config.MODEL_TIMEOUT
        while True:
            try:
                return self.store.reserve_usage(run_id, reservation, purpose)
            except Rejected as exc:
                run = self.store.run(run_id) if run_id else None
                committed = (run["usage_tokens"] + run.get("unknown_reserved_tokens", 0)) if run else 0
                can_wait = (exc.code == "TOKEN_BUDGET_EXHAUSTED" and run and run["reserved_tokens"] > 0
                            and committed + reservation <= run["token_budget"] and time.monotonic() < deadline)
                if not can_wait:
                    self.store.emit("model.rejected", {"purpose": purpose, "code": exc.code,
                        "provider_called": False, "summary": exc.message}, run_id=run_id, producer="model")
                    raise
                await asyncio.sleep(.05)

    async def complete(self, messages, *, run_id=None, purpose="victim", max_tokens=512, generation=None):
        generation = str(generation if generation is not None else self._current_generation())
        owned = asyncio.create_task(self._complete_owned(messages, run_id=run_id, purpose=purpose,
                                                         max_tokens=max_tokens, generation=generation))
        await self._track(generation, owned)
        try:
            return await asyncio.shield(owned)
        except asyncio.CancelledError:
            # A caller shutdown must remain a normal asyncio cancellation.  The
            # owned task still gets a chance to settle an in-flight usage row.
            if not owned.done():
                owned.cancel()
            try:
                await asyncio.shield(owned)
            except (asyncio.CancelledError, ModelFailure):
                pass
            raise
        finally:
            await self._untrack(generation, owned)

    async def _complete_owned(self, messages, *, run_id, purpose, max_tokens, generation):
        self._ensure_generation(generation)
        if not config.MODEL_KEY:
            raise ModelFailure("MODEL_UNCONFIGURED: 缺少本地受保护模型配置")
        estimated_input = max(100, sum(len(str(m.get("content", ""))) for m in messages) // 2)
        reservation = estimated_input + max_tokens
        try:
            async with self.semaphore:
                self._ensure_generation(generation)
                usage_id = None
                try:
                    usage_id = await self.reserve(run_id, reservation, purpose)
                except asyncio.CancelledError:
                    if generation in self._cancelled_generations or self._current_generation() != generation:
                        raise ModelFailure(f"MODEL_CANCELLED: generation {generation}") from None
                    raise
                self._ensure_generation(generation)
                started = time.monotonic()
                start_ns = time.time_ns()
                usage = None
                content = ""
                error = None
                returned_model = config.MODEL
                trace_id = None
                try:
                    # The generation check immediately before provider I/O
                    # closes the reset/snapshot race for late old-generation
                    # tasks that were not yet inside the HTTP call.
                    self._ensure_generation(generation)
                    response = await self.client.post(config.MODEL_URL, headers={"Authorization": "Bearer " + config.MODEL_KEY},
                                                  json={"model": config.MODEL, "messages": messages, "max_tokens": max_tokens,
                                                        "stream": False, "temperature": 0, "thinking": {"type": "disabled"}})
                    if response.status_code != 200:
                        raise ModelFailure(f"MODEL_HTTP_{response.status_code}: 模型服务拒绝请求")
                    body = response.json()
                    usage = body.get("usage")
                    returned_model = body.get("model", config.MODEL)
                    choice = (body.get("choices") or [{}])[0]
                    content = str(choice.get("message", {}).get("content") or "").strip()
                    if not content:
                        raise ModelFailure(f"MODEL_EMPTY: 模型未产生业务答案 ({choice.get('finish_reason', 'unknown')})")
                    self.last_status, self.last_error = "available", None
                except asyncio.CancelledError:
                    error = "CANCELLED_USAGE_UNKNOWN"
                    if generation in self._cancelled_generations:
                        raise ModelFailure(f"MODEL_CANCELLED: generation {generation}") from None
                    raise
                except Exception as exc:
                    error = str(exc) if isinstance(exc, (ModelFailure, Rejected)) else type(exc).__name__
                    error = error.replace(config.MODEL_KEY, "[REDACTED]")
                    self.last_status, self.last_error = "degraded", error[:300]
                    raise ModelFailure(error) from None
                finally:
                    elapsed = round(time.monotonic() - started, 3)
                    self.store.settle_usage(usage_id, usage, elapsed, error)
                    self.last_at = time.time()
                    if self.telemetry:
                        try:
                            run = self.store.run(run_id) if run_id else None
                            environment = run["manifest"]["environment"] if run else os.getenv("S9_ENVIRONMENT", "demo")
                            trace_id = self.telemetry.record("s9." + purpose, start_ns=start_ns, end_ns=time.time_ns(),
                                                             input_data={"messages": messages}, output_data={"content": content, "error": error},
                                                             metadata={"run_id": run_id or "interactive", "purpose": purpose, "model": returned_model,
                                                                       "environment": environment, "status": "failed" if error else "completed"}, usage=usage)
                        except Exception as exc:
                            self.store.emit("telemetry.export_failed", {"summary": "遥测记录失败", "error": type(exc).__name__}, run_id=run_id)
                return {"content": content, "usage": usage or {}, "usage_unknown": usage is None,
                        "elapsed_s": elapsed, "model": returned_model, "trace_id": trace_id}
        except asyncio.CancelledError:
            if generation in self._cancelled_generations or self._current_generation() != generation:
                raise ModelFailure(f"MODEL_CANCELLED: generation {generation}") from None
            raise

    async def cancel_generation(self, generation: str, timeout: float = 2.0) -> dict:
        generation = str(generation)
        self._cancelled_generations.add(generation)
        async with self._tasks_lock:
            tasks = list(self._generation_tasks.get(generation, set()))
        for task in tasks:
            if not task.done():
                task.cancel()
        pending = {task for task in tasks if not task.done()}
        if pending:
            done, pending = await asyncio.wait(pending, timeout=max(0.0, timeout))
        cancelled = sum(1 for task in tasks if task.done() and task.cancelled())
        failed = sum(1 for task in tasks if task.done() and not task.cancelled() and task.exception() is not None)
        remaining = sum(1 for task in pending if not task.done())
        if remaining == 0:
            self._cancelled_generations.discard(generation)
        return {"generation": generation, "requested": len(tasks), "cancelled": cancelled,
                "failed": failed, "remaining": remaining, "timed_out": bool(remaining)}

    def status(self):
        active = sum(len(tasks) for tasks in self._generation_tasks.values())
        return {"status": self.last_status, "model": config.MODEL, "detail": "远程 EvoMap API" if not self.last_error else self.last_error,
                "remote_inference": True, "max_concurrency": config.MODEL_CONCURRENCY,
                "active_requests": active, "tracked_generations": len(self._generation_tasks)}

    async def close(self):
        await self.client.aclose()


def parse_json(text):
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start:end + 1])
        raise
