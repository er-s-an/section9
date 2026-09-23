from __future__ import annotations

import asyncio
import json
import os
import re
import time
import uuid
from contextlib import asynccontextmanager

import httpx

from s9 import config
from s9.store import Rejected


class ModelFailure(Exception):
    def __init__(self, message, *, provider_called=None):
        super().__init__(message)
        self.provider_called = provider_called


class ModelClient:
    def __init__(self, store, telemetry=None, *, gateway=None, scope=None):
        self.store, self.telemetry = store, telemetry
        self.gateway, self.scope = gateway, scope
        self.semaphore = asyncio.Semaphore(config.MODEL_CONCURRENCY) if gateway is None else None
        self.client = gateway.client if gateway else httpx.AsyncClient(timeout=httpx.Timeout(config.MODEL_TIMEOUT, connect=10), trust_env=True)
        self._local_client = gateway is None
        self.last_status = "configured" if config.MODEL_KEY else "unconfigured"
        self.last_error = None
        self.last_at = None
        self._generation_tasks: dict[str, set[asyncio.Task]] = {}
        self._cancelled_generations: set[str] = set()
        self._tasks_lock = asyncio.Lock()

    def _scope_parts(self, run_id=None):
        if isinstance(self.scope, dict):
            return (self.scope.get("pair_id"), self.scope.get("run_id", run_id), self.scope.get("arm"))
        if isinstance(self.scope, (tuple, list)):
            return tuple((list(self.scope) + [None, None, None])[:3])
        return (None, run_id, None)

    def _cancel_key(self, generation, run_id=None):
        return (*self._scope_parts(run_id), str(generation))

    def _current_generation(self) -> str:
        with self.store.tx() as db:
            return str(self.store.meta(db, "generation"))

    def _ensure_generation(self, generation: str, cancel_key=None) -> None:
        if (cancel_key in self._cancelled_generations if cancel_key is not None else any(k[-1] == str(generation) for k in self._cancelled_generations)) or self._current_generation() != str(generation):
            raise ModelFailure(f"MODEL_CANCELLED: generation {generation}")

    async def _track(self, generation, task: asyncio.Task) -> None:
        async with self._tasks_lock:
            self._generation_tasks.setdefault(generation, set()).add(task)

    async def _untrack(self, generation, task: asyncio.Task) -> None:
        async with self._tasks_lock:
            tasks = self._generation_tasks.get(generation)
            if tasks is not None:
                tasks.discard(task)
                if not tasks:
                    self._generation_tasks.pop(generation, None)

    async def reserve(self, run_id, reservation, purpose, *, request_id=None, request_context=None, deadline=None):
        # A concurrent request may temporarily occupy budget that is returned
        # on settlement. Queue for that settlement instead of falsely treating
        # contention as spent tokens. Never retry a provider request here.
        deadline = deadline if deadline is not None else time.monotonic() + config.MODEL_TIMEOUT
        request_id = request_id or ('model_' + uuid.uuid4().hex)
        if request_context is None:
            request_context = self.store.capture_model_request(
                run_id, self._current_generation(), expected_scope=dict(self.store.scope),
                deadline_at=time.time() + max(0.0, deadline - time.monotonic()))
        while True:
            try:
                return self.store.reserve_usage(run_id, reservation, purpose, request_id=request_id,
                                                request_context=request_context)
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

    async def complete(self, messages, *, run_id=None, purpose="victim", max_tokens=512, generation=None,
                       authority=None):
        generation = str(generation if generation is not None else self._current_generation())
        track_key = self._cancel_key(generation, run_id)
        owned = asyncio.create_task(self._complete_owned(messages, run_id=run_id, purpose=purpose,
                                                         max_tokens=max_tokens, generation=generation, cancel_key=track_key,
                                                         authority=authority))
        await self._track(track_key, owned)
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
            await self._untrack(track_key, owned)

    async def _complete_owned(self, messages, *, run_id, purpose, max_tokens, generation, cancel_key=None,
                              authority=None):
        cancel_key = cancel_key or self._cancel_key(generation, run_id)
        try:
            self._ensure_generation(generation, cancel_key)
        except ModelFailure as exc:
            exc.provider_called = False
            raise
        if not config.MODEL_KEY:
            raise ModelFailure("MODEL_UNCONFIGURED: 缺少本地受保护模型配置", provider_called=False)
        deadline = time.monotonic() + config.MODEL_TIMEOUT
        deadline_at = time.time() + config.MODEL_TIMEOUT
        request_id = 'model_' + uuid.uuid4().hex
        request_scope = dict(self.store.scope)
        if isinstance(self.scope, dict) and self.scope != request_scope:
            rejected = Rejected("SCOPE_MISMATCH", "模型客户端与预算权威的运行作用域不一致", 403)
            rejected.provider_called = False
            raise rejected
        try:
            request_context = self.store.capture_model_request(
                run_id, generation, expected_scope=request_scope, deadline_at=deadline_at, authority=authority)
        except Rejected as exc:
            exc.provider_called = False
            raise
        estimated_input = max(100, sum(len(str(m.get('content', ''))) for m in messages) // 2)
        reservation = estimated_input + max_tokens
        provider_key = self._scope_parts(run_id)
        producer = {'diagnose': 'diagnoser', 'single': 'single', 'cost': 'cost'}.get(purpose, 'model')
        if purpose == 'repair' and run_id:
            with self.store.tx() as db:
                tasks = [json.loads(row[0]) for row in db.execute('SELECT data FROM tasks WHERE run_id=?', (run_id,))]
                producer = next((t['holder'] for t in tasks if t['kind'] == 'repair' and t['status'] == 'claimed'), 'model')
        scope_meta = {'pair_id': provider_key[0], 'run_id': run_id, 'arm': provider_key[2], 'generation': generation}
        base = {'request_id': request_id, 'purpose': purpose, **scope_meta}
        queued = time.monotonic()
        usage_id, usage, error, content, trace_id = None, None, None, '', None
        admitted, provider_started, start_ns = None, False, None
        returned_model = None
        self.store.emit('model.queued', {**base, 'summary': '等待模型预算及共享准入'}, run_id=run_id, producer=producer)
        try:
            # Reserve independently before shared admission; budget waits never
            # consume a shared provider slot. Legacy local semaphore kept compatible.
            if self.gateway:
                usage_id = await self.reserve(run_id, reservation, purpose, request_id=request_id,
                                              request_context=request_context, deadline=deadline)
            slot = self.gateway.slot(provider_key) if self.gateway else _local_slot(self.semaphore)
            try:
                async with asyncio.timeout(max(0.0, deadline - time.monotonic())):
                    async with slot:
                        self._ensure_generation(generation, cancel_key)
                        if not self.gateway:
                            usage_id = await self.reserve(run_id, reservation, purpose, request_id=request_id,
                                                          request_context=request_context, deadline=deadline)
                        self._ensure_generation(generation, cancel_key)
                        admitted = time.monotonic()
                        self.store.emit('model.admitted', {**base, 'usage_id': usage_id, 'queue_ms': round((admitted-queued)*1000, 3),
                                        'summary': '共享模型槽位已准入'}, run_id=run_id, producer=producer)
                        self._ensure_generation(generation, cancel_key)
                        start_ns = time.time_ns()
                        self.store.authorize_usage_dispatch(usage_id, request_id=request_id,
                                                            request_context=request_context)
                        provider_started = True
                        self.store.emit('model.provider_started', {**base, 'usage_id': usage_id, 'summary': '远程模型请求已发起'}, run_id=run_id, producer=producer)
                        response = await self.client.post(config.MODEL_URL, headers={'Authorization': 'Bearer ' + config.MODEL_KEY},
                            json={'model': config.MODEL, 'messages': messages, 'max_tokens': max_tokens,
                                  'stream': False, 'temperature': 0, 'thinking': {'type': 'disabled'}})
            except TimeoutError as exc:
                raise ModelFailure("MODEL_DEADLINE_EXCEEDED: 供应商准入或请求超过期限") from exc
            if response.status_code != 200:
                raise ModelFailure(f'MODEL_HTTP_{response.status_code}: 模型服务拒绝请求')
            body = response.json()
            usage = body.get('usage')
            returned_model = body.get('model') if isinstance(body.get('model'), str) and body['model'] else None
            choice = (body.get('choices') or [{}])[0]
            content = str(choice.get('message', {}).get('content') or '').strip()
            if not content:
                raise ModelFailure(f"MODEL_EMPTY: 模型未产生业务答案 ({choice.get('finish_reason', 'unknown')})")
            self.last_status, self.last_error = 'available', None
        except asyncio.CancelledError:
            error = 'CANCELLED_USAGE_UNKNOWN' if provider_started else 'CANCELLED_BEFORE_PROVIDER'
            if cancel_key in self._cancelled_generations or self._current_generation() != generation:
                raise ModelFailure(f'MODEL_CANCELLED: generation {generation}', provider_called=provider_started) from None
            raise
        except Exception as exc:
            error = str(exc) if isinstance(exc, (ModelFailure, Rejected)) else type(exc).__name__
            error = error.replace(config.MODEL_KEY, '[REDACTED]')
            self.last_status, self.last_error = 'degraded', error[:300]
            if isinstance(exc, Rejected):
                exc.provider_called = provider_started
                raise
            raise ModelFailure(error, provider_called=provider_started) from None
        finally:
            finished = time.monotonic()
            elapsed = round(finished - (admitted or queued), 3)
            settled_usage = usage if provider_started else {'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0}
            if usage_id:
                self.store.settle_usage(usage_id, settled_usage, elapsed, error)
            event_name = 'model.cancelled' if error and 'CANCELLED' in error else 'model.failed' if error else 'model.completed'
            self.store.emit(event_name, {**base, 'usage_id': usage_id, 'provider_called': provider_started,
                'queue_ms': round(((admitted or finished)-queued)*1000, 3), 'provider_ms': round(elapsed*1000, 3) if provider_started else 0,
                'elapsed_ms': round((finished-queued)*1000, 3), 'error': error, 'returned_model': returned_model if provider_started else None,
                'usage_unknown': provider_started and usage is None, 'summary': error or '模型响应已返回'}, run_id=run_id, producer=producer)
            self.last_at = time.time()
            if self.telemetry and provider_started:
                try:
                    run = self.store.run(run_id) if run_id else None
                    environment = run['manifest']['environment'] if run else os.getenv('S9_ENVIRONMENT', 'demo')
                    trace_id = self.telemetry.record('s9.' + purpose, start_ns=start_ns, end_ns=time.time_ns(),
                        input_data={'messages': messages}, output_data={'content': content, 'error': error},
                        metadata={**scope_meta, 'run_id': run_id or 'interactive', 'purpose': purpose, 'model': returned_model,
                                  'environment': environment, 'status': 'failed' if error else 'completed', 'request_id': request_id}, usage=usage)
                except Exception as exc:
                    self.store.emit('telemetry.export_failed', {'summary': '遥测记录失败', 'error': type(exc).__name__}, run_id=run_id)
        return {'content': content, 'usage': usage or {}, 'usage_unknown': usage is None,
                'elapsed_s': elapsed, 'model': returned_model, 'trace_id': trace_id}

    async def cancel_generation(self, generation: str, timeout: float = 2.0) -> dict:
        generation = str(generation)
        key = self._cancel_key(generation)
        self._cancelled_generations.add(key)
        if self.scope is None:
            async with self._tasks_lock:
                self._cancelled_generations.update(k for k in self._generation_tasks if k[-1] == generation)
        async with self._tasks_lock:
            if self.scope is None:
                keys = [k for k in self._generation_tasks if k[-1] == generation]
                tasks = list({task for k in keys for task in self._generation_tasks.get(k, set())})
            else:
                tasks = list(self._generation_tasks.get(key, set()))
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
            self._cancelled_generations.discard(key)
        return {"generation": generation, "requested": len(tasks), "cancelled": cancelled,
                "failed": failed, "remaining": remaining, "timed_out": bool(remaining)}

    def status(self):
        active = sum(len(tasks) for tasks in self._generation_tasks.values())
        return {"status": self.last_status, "model": config.MODEL, "detail": "远程 EvoMap API" if not self.last_error else self.last_error,
                "remote_inference": True, "max_concurrency": config.MODEL_CONCURRENCY,
                "active_requests": active, "tracked_generations": len(self._generation_tasks)}

    async def close(self):
        if self._local_client:
            await self.client.aclose()




@asynccontextmanager
async def _local_slot(semaphore):
    async with semaphore:
        yield


def parse_json(text):
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start:end + 1])
        raise
