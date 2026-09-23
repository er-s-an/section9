import asyncio
import json

import pytest

from s9.victim import VictimApp


class SequencedModel:
    def __init__(self):
        self.active = 0
        self.max_active = 0
        self.calls = 0

    async def complete(self, messages, **kwargs):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        self.calls += 1
        try:
            await asyncio.sleep(0.01)
            question = messages[-1]["content"]
            if "超过15天" in question:
                answer = {"eligible": False, "merchant_pays_shipping": False,
                          "answer": "结论：本次不可以无理由退货；本次不适用商家承担退货运费。"}
            elif "X200" in question:
                answer = {"battery_hours": 30, "answer": "结论：X200续航为30小时。"}
            elif "S9-MISSING" in question:
                answer = {"found": False, "answer": "结论：未查到该订单。"}
            else:
                answer = {"eligible": True, "merchant_pays_shipping": True,
                          "answer": "结论：本次可以无理由退货；退货运费由商家承担。"}
            return {"content": json.dumps(answer, ensure_ascii=False),
                    "usage": {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120}}
        finally:
            self.active -= 1


@pytest.mark.asyncio
async def test_verify_keeps_only_one_budgeted_probe_in_flight():
    model = SequencedModel()
    victim = VictimApp(model, lambda: {"revision": "1", "generation": "1", "context_multiplier": 1,
                                       "prompt_version": "healthy", "max_output_tokens": 256,
                                       "retry_limit": 0, "retry_on_terminal": False},
                       lambda *args, **kwargs: asyncio.sleep(0, result=None))

    result = await victim.probe(suite="verify", run_id="run-cost")

    assert result["passed"] is True
    assert model.calls == 4
    assert model.max_active == 1
    assert all(check["passed"] for check in result["checks"])


@pytest.mark.asyncio
async def test_cancelled_verify_does_not_start_remaining_paid_probes():
    class CancelFirstModel:
        calls = 0

        async def complete(self, messages, **kwargs):
            self.calls += 1
            error = asyncio.CancelledError("cancelled before provider")
            error.provider_called = False
            raise error

    model = CancelFirstModel()
    victim = VictimApp(model, lambda: {"revision": "1", "generation": "1", "context_multiplier": 1,
                                       "prompt_version": "healthy", "max_output_tokens": 256,
                                       "retry_limit": 0, "retry_on_terminal": False},
                       lambda *args, **kwargs: asyncio.sleep(0, result=None))

    result = await victim.probe(suite="verify", run_id="run-cost")

    assert model.calls == 1
    assert result["passed"] is False
    assert all(check["passed"] is False for check in result["checks"] if check["name"].startswith("heldout_"))
    assert result["usage"]["token_quality"] == "provider"
