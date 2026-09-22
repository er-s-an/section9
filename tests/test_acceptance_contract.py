import asyncio
import json

from test_victim import StrictJSONDouble, make_app


class ContractDouble(StrictJSONDouble):
    def __init__(self, mode):
        super().__init__()
        self.mode = mode

    async def complete(self, messages, **kwargs):
        result = await super().complete(messages, **kwargs)
        body = json.loads(result["content"])
        if self.mode == "contradictory" and "续航" not in messages[-1]["content"] and "物流" not in messages[-1]["content"]:
            body["eligible"] = True
            body["merchant_pays_shipping"] = True
            body["answer"] = "可以退货，商家不承担运费。"
        if self.mode == "high_output":
            result["usage"]["output_tokens"] = 1537
            result["usage"]["total_tokens"] = result["usage"]["input_tokens"] + 1537
        if self.mode == "inconsistent":
            result["usage"]["total_tokens"] += 1
        result["content"] = json.dumps(body, ensure_ascii=False)
        return result


def test_contradictory_answer_fails_closed():
    app, _, _, _ = make_app(model=ContractDouble("contradictory"))
    result = asyncio.run(app.probe(suite="verify"))
    assert result["passed"] is False
    assert next(c for c in result["checks"] if c["name"] == "heldout_semantic_policy")["passed"] is False


def test_boundary_and_product_negation_cannot_hide_behind_fields():
    class NegatingDouble(StrictJSONDouble):
        async def complete(self, messages, **kwargs):
            result = await super().complete(messages, **kwargs)
            body = json.loads(result["content"])
            if "超过" in messages[-1]["content"]:
                body.update(eligible=False, merchant_pays_shipping=False, answer="虽然超过7天但仍可以无理由退货。")
            elif "续航" in messages[-1]["content"]:
                body.update(battery_hours=30, answer="不是30小时。")
            result["content"] = json.dumps(body, ensure_ascii=False)
            return result

    app, _, _, _ = make_app(model=NegatingDouble())
    verify = asyncio.run(app.probe(suite="verify"))
    assert verify["passed"] is False
    assert next(c for c in verify["checks"] if c["name"] == "unaffected_product_fact")["passed"] is False


def test_high_output_and_inconsistent_total_fail_cost_contract():
    for mode in ("high_output", "inconsistent"):
        app, _, _, _ = make_app(model=ContractDouble(mode))
        result = asyncio.run(app.probe(suite="verify"))
        assert result["passed"] is False
        assert next(c for c in result["checks"] if c["name"] == "cost_budget")["passed"] is False


def test_normal_answer_and_usage_contract_pass():
    app, _, _, _ = make_app(model=ContractDouble("normal"))
    result = asyncio.run(app.probe(suite="verify"))
    assert result["passed"] is True
