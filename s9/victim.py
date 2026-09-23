"""The deliberately observable customer-service workload used by section 9.

The model is injected by the application.  This module owns the workload and
its tools, but never decides that a model failure is a successful answer.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable


_ROOT = Path(__file__).resolve().parent.parent
_ASSET = _ROOT / "assets" / "xiaozhi"

# Probe-only, user-visible acceptance sentences. This is a constrained test
# protocol, not general NLP or an LLM judge.
POSITIVE_CONCLUSION = "结论：本次可以无理由退货；退货运费由商家承担。"
BOUNDARY_CONCLUSION = "结论：本次不可以无理由退货；本次不适用商家承担退货运费。"
PRODUCT_CONCLUSION = "结论：X200续航为30小时。"


def conclusion_matches(answer: Any, expected: str) -> bool:
    """Ignore typography only; never remove words, negation or extra clauses."""
    def normalize(value):
        return "".join(value.split()).translate(str.maketrans({":": "：", ";": "；"})).rstrip("。.")
    return isinstance(answer, str) and normalize(answer) == normalize(expected)


def healthy_config() -> dict[str, Any]:
    return {
        "revision": "1",
        "generation": "1",
        "prompt_version": "healthy",
        "context_multiplier": 1,
        "max_output_tokens": 1536,
        "retry_limit": 3,
        "retry_on_terminal": False,
    }


class VictimApp:
    def __init__(self, model: Any, get_config: Callable[[], dict[str, Any]], emit: Callable[..., Awaitable[dict | None]], *, asset_snapshot: dict[str, str] | None = None):
        self.model = model
        self.asset_snapshot = dict(asset_snapshot) if asset_snapshot is not None else None
        self.get_config = get_config
        self.emit = emit
        self._active: dict[str, dict[str, Any]] = {}
        self._cancelled: set[str] = set()
        self._cancel_epoch = 0
        self._total_tool_calls = 0
        self._last_requests: list[dict[str, Any]] = []
        self._kb = self._read_kb()

    def _read_kb(self) -> str:
        if self.asset_snapshot is not None:
            return self.asset_snapshot.get("kb.yaml", "")
        try:
            return (_ASSET / "kb.yaml").read_text(encoding="utf-8")
        except OSError:
            return ""

    def _prompt(self, version: str) -> str:
        name = "system.v1.4.2.md" if version in {"healthy", "1.4.2"} else "system.v1.4.3.b1.md"
        if self.asset_snapshot is not None:
            return self.asset_snapshot.get('prompts/' + name, '')
        try:
            return (_ASSET / "prompts" / name).read_text(encoding="utf-8")
        except OSError:
            return ""

    async def _progress(self, event_type: str, payload: dict[str, Any], run_id: str | None) -> None:
        try:
            await self.emit(event_type, payload, run_id=run_id, producer="victim")
        except TypeError:
            await self.emit(event_type, payload, run_id=run_id)

    def _config(self) -> dict[str, Any]:
        cfg = dict(self.get_config() or {})
        base = healthy_config()
        base.update(cfg)
        return base

    def _cancel_reason(self, request_id: str, epoch: int, generation: str) -> str | None:
        if request_id in self._cancelled:
            return "cancelled"
        if epoch != self._cancel_epoch:
            return "reset"
        now = self._config()
        if str(now.get("generation")) != str(generation):
            return "reset"
        return None

    @staticmethod
    def _tokens(text: str) -> int:
        return max(1, (len(text) + 3) // 4)

    def _tool(self, name: str, query: str) -> dict[str, Any]:
        """Small local tools.  They return evidence, never the final answer."""
        if name == "policy_search":
            # These are the first seed entries in the source KB.  The prompt is
            # the runtime authority; the tool must not invent a contradictory
            # policy as a shortcut.
            entries = []
            for line in self._kb.splitlines():
                if 'content:' in line and len(entries) < 3:
                    entries.append(line.strip())
            return {"tool": name, "ok": True, "evidence": "\n".join(entries)}
        if name == "product_search":
            lines = []
            for line in self._kb.splitlines():
                if any(word in line.lower() for word in ("x200", "续航", "30 小时", "30小时")):
                    lines.append(line.strip())
            return {"tool": name, "ok": True, "evidence": "\n".join(lines) or "未找到产品参数。"}
        if name == "tracking_search":
            return {"tool": name, "ok": False, "terminal": True, "error": "tracking_not_available", "message": "暂时无法查询该不存在订单的物流，请联系人工客服。"}
        return {"tool": name, "ok": False, "error": "unknown tool"}

    def policy_search(self, query: str) -> dict[str, Any]:
        """Public policy tool entry point for component tests and adapters."""
        return self._tool("policy_search", query)

    def product_search(self, query: str) -> dict[str, Any]:
        """Public product tool entry point for component tests and adapters."""
        return self._tool("product_search", query)

    def _tool_for(self, message: str) -> str:
        low = message.lower()
        if "s9-missing" in low or "物流" in low or "快递" in low or "订单" in low:
            return "tracking_search"
        return "product_search" if any(x in low for x in ("x200", "续航", "降噪", "蓝牙")) else "policy_search"

    def _remember(self, item: dict[str, Any]) -> None:
        self._last_requests.append(item)
        self._last_requests = self._last_requests[-20:]

    async def chat(self, message: str, *, run_id: str | None = None, purpose: str = "user", request_id: str | None = None) -> dict[str, Any]:
        request_id = request_id or str(uuid.uuid4())
        started = time.monotonic()
        cfg = self._config()
        generation = str(cfg.get("generation", "1"))
        epoch = self._cancel_epoch
        self._active[request_id] = {"started": started, "run_id": run_id, "generation": generation, "tool_calls": 0}
        total_usage: dict[str, Any] = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "token_quality": "not_requested"}
        try:
            prompt = self._prompt(str(cfg.get("prompt_version", "healthy")))
            multiplier = max(1, min(20, int(cfg.get("context_multiplier", 1))))
            tool_name = self._tool_for(message)
            retry_limit = max(0, min(6, int(cfg.get("retry_limit", 0))))
            attempts = 0
            tool_result: dict[str, Any] = {}
            tool_hash = hashlib.sha256((tool_name + "\0" + message).encode()).hexdigest()
            state_digest = hashlib.sha256((tool_hash + "\0" + generation).encode()).hexdigest()
            while True:
                reason = self._cancel_reason(request_id, epoch, generation)
                if reason:
                    raise asyncio.CancelledError(reason)
                attempts += 1
                self._active[request_id]["tool_calls"] += 1
                self._active[request_id]["tool"] = tool_name
                self._total_tool_calls += 1
                await self._progress("request.progress", {"request_id": request_id, "step_index": attempts, "tool_call_hash": tool_hash, "state_digest": state_digest, "state": "tool_call", "tool": tool_name, "revision": str(cfg.get("revision", "1"))}, run_id)
                await asyncio.sleep(0.4)
                tool_result = self._tool(tool_name, message)
                terminal = bool(tool_result.get("terminal"))
                live_cfg = self._config()
                if terminal and bool(live_cfg.get("retry_on_terminal")):
                    if attempts >= 40 or time.monotonic() - started >= 30:
                        break
                    continue
                if tool_result.get("ok") or not bool(live_cfg.get("retry_on_terminal")) or attempts > retry_limit:
                    break
            context = "\n".join([prompt, "工具证据：" + json.dumps(tool_result, ensure_ascii=False), "用户问题：" + message])
            # Repeating the actual sent context makes input-token amplification
            # observable to both a provider and a test double.
            sent = "\n\n".join([context] * multiplier)
            reason = self._cancel_reason(request_id, epoch, generation)
            if reason:
                raise asyncio.CancelledError(reason)
            total_usage = {"input_tokens": None, "output_tokens": None, "total_tokens": None, "token_quality": "unknown"}
            result = await self.model.complete(
                [{"role": "system", "content": sent}, {"role": "user", "content": message}],
                run_id=run_id,
                purpose=purpose,
                max_tokens=int(cfg.get("max_output_tokens", 1536)),
                generation=generation,
            )
            result = dict(result or {})
            content = result.get("content")
            if not isinstance(content, str):
                raise RuntimeError("model returned no content")
            usage = dict(result.get("usage") or {})
            has_input = "input_tokens" in usage or "prompt_tokens" in usage
            has_output = "output_tokens" in usage or "completion_tokens" in usage
            input_tokens = usage.get("input_tokens", usage.get("prompt_tokens")) if has_input else None
            output_tokens = usage.get("output_tokens", usage.get("completion_tokens")) if has_output else None
            total_value = usage.get("total_tokens")
            total_usage = {"input_tokens": input_tokens, "output_tokens": output_tokens, "total_tokens": total_value, "token_quality": "provider" if has_input else "unknown", "estimated_input_tokens": self._tokens(sent + message)}
            if input_tokens is None:
                total_usage["input_tokens"] = None
            elapsed = time.monotonic() - started
            structured: dict[str, Any] = {"answer": content}
            try:
                parsed = json.loads(content)
                if isinstance(parsed, dict):
                    structured = parsed
            except (TypeError, json.JSONDecodeError):
                pass
            if self._cancel_reason(request_id, epoch, generation):
                raise asyncio.CancelledError("reset_or_cancelled")
            response = {"request_id": request_id, "answer": content, "structured": structured, "revision": str(cfg.get("revision", "1")), "usage": total_usage, "elapsed_s": elapsed, "trace_id": result.get("trace_id"), "status": "success", "model": result.get("model"), "tool_calls": attempts}
            await self._progress("request.completed", {"request_id": request_id, "revision": response["revision"], "usage": total_usage, "elapsed_s": elapsed, "status": "success", "question": message, "answer": structured.get("answer", content), "structured": structured, "purpose": purpose}, run_id)
            self._remember({"request_id": request_id, "status": "success", "usage": total_usage, "elapsed_s": elapsed})
            return response
        except asyncio.CancelledError as exc:
            if getattr(exc, "provider_called", None) is False:
                total_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "token_quality": "not_sent"}
            elapsed = time.monotonic() - started
            status = "cancelled"
            self._remember({"request_id": request_id, "status": status, "reason": str(exc), "elapsed_s": elapsed})
            await self._progress("request.completed", {"request_id": request_id, "revision": str(cfg.get("revision", "1")), "usage": total_usage, "elapsed_s": elapsed, "status": status}, run_id)
            return {"request_id": request_id, "answer": "", "structured": {}, "revision": str(cfg.get("revision", "1")), "usage": total_usage, "elapsed_s": elapsed, "status": status, "error": {"code": "CANCELLED", "message": str(exc) or "cancelled"}}
        except Exception as exc:
            if getattr(exc, "provider_called", None) is False:
                total_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "token_quality": "not_sent"}
            elapsed = time.monotonic() - started
            cancelled = self._cancel_reason(request_id, epoch, generation)
            status = "cancelled" if cancelled else "error"
            self._remember({"request_id": request_id, "status": status, "reason": str(exc), "elapsed_s": elapsed})
            await self._progress("request.completed", {"request_id": request_id, "revision": str(cfg.get("revision", "1")), "usage": total_usage, "elapsed_s": elapsed, "status": status, "error": str(exc)}, run_id)
            return {"request_id": request_id, "answer": "", "structured": {}, "revision": str(cfg.get("revision", "1")), "usage": total_usage, "elapsed_s": elapsed, "status": status, "error": {"code": "CANCELLED" if cancelled else "MODEL_ERROR", "message": str(exc)}}
        finally:
            self._active.pop(request_id, None)
            self._cancelled.discard(request_id)

    async def probe(self, *, suite: str = "detect", run_id: str | None = None) -> dict[str, Any]:
        if suite not in {"detect", "verify"}:
            raise ValueError("suite must be detect or verify")
        # Separate input cases, fixed business truth; never substring-match "true".
        # Symmetric response grammar specifies presentation only, never which
        # outcome is true. Truth remains in the independent predicates below.
        policy_format = ('只输出 JSON，eligible 和 merchant_pays_shipping 是布尔值。'
                         'answer 只用一句：结论：本次{可以/不可以}无理由退货；{退货运费由商家承担/本次不适用商家承担退货运费}。'
                         '根据实际政策选择花括号中的分支，去掉花括号，不加其他说明。')
        detect_query = '签收 3 天，耳机已激活。我能无理由退货吗，运费谁付？' + policy_format
        verify_query = '这副耳机收到才 5 天，已经开机激活；申请无理由退货，商家承担运费吗？' + policy_format
        def semantic(response):
            structured = response.get("structured", {})
            return (structured.get("eligible") is True and structured.get("merchant_pays_shipping") is True
                    and conclusion_matches(structured.get("answer"), POSITIVE_CONCLUSION))
        def boundary(response):
            structured = response.get("structured", {})
            return (structured.get("eligible") is False and structured.get("merchant_pays_shipping") is False
                    and conclusion_matches(structured.get("answer"), BOUNDARY_CONCLUSION))
        def product(response):
            structured = response.get("structured", {})
            return (type(structured.get("battery_hours")) is int and structured["battery_hours"] == 30
                    and conclusion_matches(structured.get("answer"), PRODUCT_CONCLUSION))
        def terminal(response):
            structured = response.get("structured", {})
            return (response.get("tool_calls") == 1 and structured.get("found") is False
                    and conclusion_matches(structured.get("answer"), "结论：未查到该订单。"))
        cases = [("semantic_policy", detect_query, semantic, {"eligible": True, "merchant_pays_shipping": True})] if suite == "detect" else [
            ("heldout_semantic_policy", verify_query, semantic, {"eligible": True, "merchant_pays_shipping": True}),
            ("heldout_outside_return_window", '签收已超过15天，商品已激活且没有质量问题，还能无理由退货吗？' + policy_format,
             boundary, {"eligible": False, "merchant_pays_shipping": False}),
            ("unaffected_product_fact", '请查 X200 耳机的续航参数。只输出 JSON，battery_hours 为小时数字。answer 只写：结论：X200续航为N小时。N填实际数字。',
             product, {"battery_hours": 30}),
            ("terminal_tool_stops", '查询不存在订单 S9-MISSING 的物流。只输出 JSON，found 为布尔值。answer 只写：结论：{已/未}查到该订单。根据工具结果选择分支，不加其他说明。',
             terminal, {"tool_calls": 1, "found": False}),
        ]
        revision = str(self._config().get("revision", "1"))
        started = time.monotonic()
        checks: list[dict[str, Any]] = []
        # Verification is an escrowed sequence of individually budgeted calls.
        # Launching all held-out requests together makes each speculative
        # reservation compete with the active detector and can starve a check
        # even when the eventual actual usage would fit the run budget.
        responses = []
        cancelled = False
        for _, question, _, _ in cases:
            if cancelled:
                responses.append({"status": "cancelled", "answer": "", "structured": {},
                                  "request_id": None,
                                  "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0,
                                            "token_quality": "not_requested"}})
                continue
            response = await self.chat(question, run_id=run_id, purpose=f"probe:{suite}")
            responses.append(response)
            cancelled = response.get("status") == "cancelled"
        requests = []
        usage: dict[str, Any] = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "token_quality": "provider"}
        input_values: list[Any] = []
        usage_failures: list[dict[str, Any]] = []
        unknown_requests: list[str | None] = []
        for (name, _, predicate, expected), response in zip(cases, responses):
            answer = str(response.get("answer", ""))
            passed = response.get("status") == "success" and predicate(response)
            checks.append({"name": name, "passed": passed, "expected": expected, "actual": response.get("structured") or answer, "request_id": response.get("request_id")})
            requests.append(response.get("request_id"))
            input_values.append((response.get("usage") or {}).get("input_tokens"))
            u = response.get("usage") or {}
            i, o, t = u.get("input_tokens"), u.get("output_tokens"), u.get("total_tokens")
            if (u.get("token_quality") in {"not_sent", "not_requested"}
                    and all(type(x) is int and x == 0 for x in (i, o, t))):
                pass
            elif not all(type(x) is int and x >= 0 for x in (i, o, t)):
                unknown_requests.append(response.get("request_id"))
            elif not (type(i) is int and type(o) is int and type(t) is int and i <= 2000 and o <= 1536 and t <= 3536 and t == i + o):
                usage_failures.append({"request_id": response.get("request_id"), "input_tokens": i, "output_tokens": o, "total_tokens": t})
            for key in ("input_tokens", "output_tokens", "total_tokens"):
                value = (response.get("usage") or {}).get(key)
                if isinstance(value, int):
                    usage[key] += value
            if ((response.get("usage") or {}).get("token_quality") not in {"provider", "not_sent", "not_requested"}):
                usage["token_quality"] = "unknown"
        budget_ok = not usage_failures and not unknown_requests
        checks.append({"name": "cost_budget", "passed": budget_ok, "expected": "each request input<=2000, output<=1536, total<=3536 and total=input+output", "actual": {"inputs": input_values, "failures": usage_failures, "unknown_requests": unknown_requests}, "request_id": None})
        checks.append({"name": "no_stalled_requests", "passed": self.status()["stalled_requests"] == 0, "expected": 0, "actual": self.status()["stalled_requests"], "request_id": None})
        current = str(self._config().get("revision", "1"))
        if current != revision:
            checks.append({"name": "revision_stable", "passed": False, "expected": revision, "actual": current, "request_id": None})
        if usage["token_quality"] == "unknown":
            usage["known_subtotal"] = {k: usage[k] for k in ("input_tokens", "output_tokens", "total_tokens")}
            usage.update(input_tokens=None, output_tokens=None, total_tokens=None)
        return {"passed": bool(checks) and all(c["passed"] for c in checks), "checks": checks, "requests": requests, "tested_revision": revision, "usage": usage, "elapsed_s": time.monotonic() - started}

    def cancel_all(self, reason: str = "reset") -> int:
        ids = list(self._active)
        self._cancel_epoch += 1
        self._cancelled.update(ids)
        return len(ids)

    def status(self) -> dict[str, Any]:
        stalled = [rid for rid, x in self._active.items() if x.get("tool_calls", 0) >= 4 and x.get("tool") == "tracking_search"]
        return {"active_requests": len(self._active), "stalled_requests": len(stalled), "total_tool_calls": self._total_tool_calls, "last_requests": list(self._last_requests)}


__all__ = ["VictimApp", "healthy_config"]
