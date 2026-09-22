import pytest

from s9.product.registry import ProductRegistry
from s9.product.service import ProductService


@pytest.mark.asyncio
@pytest.mark.parametrize("failed_sample", [None, 2])
async def test_observe_requires_stable_health_and_preserves_business_evidence(tmp_path, monkeypatch, failed_sample):
    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr("s9.product.service.asyncio.sleep", no_sleep)
    service = ProductService.__new__(ProductService)
    service.project_id = "support-agent"
    service.environment_id = "local-test"
    service.registry = ProductRegistry(tmp_path / "product.sqlite")

    class Runtime:
        def __init__(self):
            self.calls = 0

        async def probe(self, _project_id, _environment_id):
            self.calls += 1
            status = 503 if self.calls == failed_sample else 200
            return [{"status_code": status, "response": {"status": "ok" if status == 200 else "error"}},
                    {"status_code": 404, "response": {"detail": "not found"}}]

    service.runtime = Runtime()
    service._configuration_sha256 = lambda: "a" * 64
    service._source = lambda: {"commit": "candidate-revision", "clean": True}
    service._write_bundle = lambda *_args, **_kwargs: None
    incident = {
        "id": "incident-observe-evidence",
        "project_id": "support-agent",
        "environment_id": "local-test",
        "status": "observing",
        "execution": {"attempt_id": "attempt-1", "target_revision": "candidate-revision",
                      "configuration_sha256": "a" * 64},
        "source_binding_id": "binding-1",
        "evidence": [{"evidence_id": "verification-1", "kind": "verification_bundle", "status": "ready"}],
    }
    service.registry.put("incident", incident["id"], incident,
                         project_id="support-agent", environment_id="local-test")

    async def business_probe(incident_id):
        fresh = service.registry.get("incident", incident_id,
                                     project_id="support-agent", environment_id="local-test")
        fresh["evidence"].append({"evidence_id": "business-trace-1", "kind": "business_trace", "status": "ready"})
        fresh["langfuse"] = {"status": "ready", "observation_count": 1}
        service.registry.put("incident", incident_id, fresh,
                             project_id="support-agent", environment_id="local-test")
        return {"probe": {"status": "ready"}, "evidence": {"evidence_id": "business-trace-1"}}

    service.business_probe = business_probe
    result = await service.observe(incident["id"])

    assert result["recovery"]["status"] == ("recovered" if failed_sample is None else "unknown")
    assert {item["kind"] for item in result["incident"]["evidence"]} == {
        "verification_bundle", "runtime_observation_window", "business_trace",
    }
    window = next(item for item in result["incident"]["evidence"] if item["kind"] == "runtime_observation_window")
    assert window["value"]["sample_count"] == 5
    assert window["value"]["all_health_samples_passed"] is (failed_sample is None)
    assert len(result["recovery"]["evidence_ids"]) == 2
    assert result["incident"]["langfuse"] == {"status": "ready", "observation_count": 1}
