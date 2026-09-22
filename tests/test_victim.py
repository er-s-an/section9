import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from s9.victim import VictimApp, healthy_config


class StrictJSONDouble:
    """Component-only provider double: returns typed JSON from received prompt."""
    def __init__(self, usage=True, delay=0):
        self.calls = []
        self.usage = usage
        self.delay = delay

    async def complete(self, messages, **kwargs):
        if self.delay:
            await asyncio.sleep(self.delay)
        self.calls.append(messages)
        system, question = messages[0]["content"], messages[-1]["content"]
        prompt_section = system.split("工具证据：", 1)[0]
        healthy = "激活后仍可退" in prompt_section and "唯一权威来源" not in prompt_section
        if "续航" in question:
            body = {"battery_hours": 30, "answer": "X200 续航 30 小时。"}
        elif "物流" in question:
            body = {"answer": "该订单物流暂时无法查询。"}
        else:
            body = {"eligible": healthy, "merchant_pays_shipping": healthy, "answer": "按政策处理。"}
        out = {"content": json.dumps(body, ensure_ascii=False), "elapsed_s": 0.01, "model": "component-double", "trace_id": None}
        if self.usage:
            n = len(system) // 4
            out["usage"] = {"input_tokens": n, "output_tokens": 8, "total_tokens": n + 8}
        return out


def make_app(cfg=None, model=None):
    config = dict(healthy_config())
    config.update(cfg or {})
    events = []
    async def emit(kind, payload, **kwargs): events.append((kind, payload))
    model = model or StrictJSONDouble()
    return VictimApp(model, lambda: config, emit), model, events, config


def names(result):
    return {c["name"] for c in result["checks"]}


def test_healthy_probe_requires_typed_return_and_shipping_truth():
    app, _, _, _ = make_app()
    result = asyncio.run(app.probe(suite="detect"))
    check = next(c for c in result["checks"] if c["name"] == "semantic_policy")
    assert result["passed"] is True
    assert check["actual"]["eligible"] is True
    assert check["actual"]["merchant_pays_shipping"] is True


def test_bad_prompt_fails_typed_semantics():
    app, _, _, _ = make_app({"prompt_version": "degraded"})
    result = asyncio.run(app.probe(suite="detect"))
    assert result["passed"] is False
    assert next(c for c in result["checks"] if c["name"] == "semantic_policy")["passed"] is False


def test_verify_is_independent_and_checks_product_and_terminal_request():
    app, model, _, _ = make_app()
    result = asyncio.run(app.probe(suite="verify"))
    assert result["passed"] is True
    assert names(result) >= {"heldout_semantic_policy", "unaffected_product_fact", "terminal_tool_stops"}
    assert any("续航" in call[-1]["content"] for call in model.calls)
    assert any("S9-MISSING" in call[-1]["content"] for call in model.calls)


def test_only_fixing_prompt_does_not_pass_when_terminal_retry_is_broken(monkeypatch):
    async def fast_sleep(_): return None
    monkeypatch.setattr("s9.victim.asyncio.sleep", fast_sleep)
    app, _, _, _ = make_app({"prompt_version": "healthy", "retry_on_terminal": True})
    result = asyncio.run(app.probe(suite="verify"))
    assert result["passed"] is False
    assert next(c for c in result["checks"] if c["name"] == "terminal_tool_stops")["passed"] is False


def test_cancelled_request_cannot_accept_late_model_result(monkeypatch):
    app, _, _, _ = make_app(model=StrictJSONDouble(delay=0.15))

    async def run():
        task = asyncio.create_task(app.chat("已激活商品能退货吗？"))
        await asyncio.sleep(0.48)
        assert app.cancel_all("reset") == 1
        result = await task
        assert result["status"] == "cancelled"
    asyncio.run(run())


def test_missing_provider_input_usage_is_unknown_and_cost_fails():
    app, _, _, _ = make_app(model=StrictJSONDouble(usage=False))
    result = asyncio.run(app.probe(suite="verify"))
    assert result["usage"]["token_quality"] == "unknown"
    assert next(c for c in result["checks"] if c["name"] == "cost_budget")["passed"] is False


def test_provider_failure_preserves_unknown_usage_as_null():
    class FailingProvider:
        async def complete(self, messages, **kwargs):
            raise RuntimeError("remote connection blocked")

    app, _, _, _ = make_app(model=FailingProvider())
    result = asyncio.run(app.chat("请回答一个普通产品问题"))
    assert result["status"] == "error"
    assert result["usage"]["token_quality"] == "unknown"
    assert result["usage"]["input_tokens"] is None
    assert result["usage"]["output_tokens"] is None
    assert result["usage"]["total_tokens"] is None


def test_verify_cases_are_not_the_detect_question():
    detect_app, detect_model, _, _ = make_app()
    verify_app, verify_model, _, _ = make_app()
    asyncio.run(detect_app.probe(suite="detect"))
    asyncio.run(verify_app.probe(suite="verify"))
    assert detect_model.calls[0][-1]["content"] != verify_model.calls[0][-1]["content"]
