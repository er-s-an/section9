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

    async def complete(self, messages, *, run_id=None, purpose="victim", max_tokens=512):
        if not config.MODEL_KEY:
            raise ModelFailure("MODEL_UNCONFIGURED: 缺少本地受保护模型配置")
        estimated_input = max(100, sum(len(str(m.get("content", ""))) for m in messages) // 2)
        reservation = estimated_input + max_tokens
        async with self.semaphore:
            usage_id = await self.reserve(run_id, reservation, purpose)
            started = time.monotonic()
            start_ns = time.time_ns()
            usage = None
            content = ""
            error = None
            returned_model = config.MODEL
            trace_id = None
            try:
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

    def status(self):
        return {"status": self.last_status, "model": config.MODEL, "detail": "远程 EvoMap API" if not self.last_error else self.last_error,
                "remote_inference": True, "max_concurrency": config.MODEL_CONCURRENCY}

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
