from integrations.support_agent.manifest import get_manifest
from s9.product.service import _matches_business_truth


def test_order_truth_accepts_both_seeded_read_only_tools():
    sample = next(item for item in get_manifest()["business_truth_samples"] if item["name"] == "order_truth")
    for tool in ("get_order_status", "track_shipment"):
        assert _matches_business_truth(sample, {"trace": [{"tool": tool, "args": {"order_id": 1001},
                                                           "result": {"status": "delivered"}}]})


def test_order_truth_rejects_wrong_tool_or_business_result():
    sample = next(item for item in get_manifest()["business_truth_samples"] if item["name"] == "order_truth")
    assert not _matches_business_truth(sample, {"trace": [{"tool": "initiate_refund", "args": {"order_id": 1001},
                                                           "result": {"status": "delivered"}}]})
    assert not _matches_business_truth(sample, {"trace": [{"tool": "track_shipment", "args": {"order_id": 1001},
                                                           "result": {"status": "shipped"}}]})


def test_order_truth_rejects_correct_status_from_wrong_order():
    sample = next(item for item in get_manifest()["business_truth_samples"] if item["name"] == "order_truth")
    assert not _matches_business_truth(sample, {"trace": [{"tool": "get_order_status", "args": {"order_id": 1002},
                                                           "result": {"status": "delivered"}}]})


def test_refund_guardrail_requires_ineligible_read_only_result():
    sample = next(item for item in get_manifest()["business_truth_samples"] if item["name"] == "refund_guardrail")
    assert _matches_business_truth(sample, {"trace": [{"tool": "check_refund_eligibility", "args": {"order_id": 1002},
                                                       "result": {"eligible": False}}]})
    assert not _matches_business_truth(sample, {"trace": [{"tool": "check_refund_eligibility", "args": {"order_id": 1002},
                                                          "result": {"eligible": True}}]})
    assert not _matches_business_truth(sample, {"trace": [{"tool": "check_refund_eligibility", "args": {"order_id": 1001},
                                                          "result": {"eligible": False}}]})


def test_business_probe_rejects_stopped_runtime_before_opening_incident(tmp_path):
    import asyncio
    from types import SimpleNamespace

    import pytest

    from integrations.support_agent.manifest import get_manifest
    from s9.product.registry import ProductError, ProductRegistry
    from s9.product.service import ProductService

    service = ProductService.__new__(ProductService)
    service.project_id = "support-agent"
    service.environment_id = "local-test"
    service.manifest = get_manifest()
    service.runtime = SimpleNamespace(status=lambda: {"status": "stopped"})
    service.registry = ProductRegistry(tmp_path / "product.sqlite")

    with pytest.raises(ProductError) as caught:
        asyncio.run(service.business_probe())
    assert caught.value.code == "EXTERNAL_RUNTIME_STOPPED"
    assert service.registry.list("incident", project_id="support-agent", environment_id="local-test") == []
