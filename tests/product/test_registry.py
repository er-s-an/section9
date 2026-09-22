from s9.product.registry import ProductRegistry


def test_scope_list_includes_scoped_records_and_updates_serialized_timestamp(tmp_path):
    registry = ProductRegistry(tmp_path / "product.sqlite")
    first = registry.put("approval", "a1", {"decision": "approved"}, project_id="p", environment_id="dev", incident_id="i1")
    registry.put("approval", "a2", {"decision": "approved"}, project_id="p", environment_id="dev", incident_id="i2")
    registry.put("approval", "a3", {"decision": "approved"}, project_id="other", environment_id="dev", incident_id="i3")

    scoped_items = registry.list("approval", project_id="p", environment_id="dev")
    assert {item["id"] for item in scoped_items} == {"a1", "a2"}
    assert all(item["updated_at"] for item in scoped_items)
    assert registry.list("approval", project_id="p", environment_id="dev", incident_id="i1") == [first]

    updated = registry.put("approval", "a1", {"decision": "revoked"}, project_id="p", environment_id="dev", incident_id="i1")
    assert updated["updated_at"] >= first["updated_at"]
    assert registry.get("approval", "a1", project_id="p", environment_id="dev", incident_id="i1")["decision"] == "revoked"


def test_configuration_binding_changes_without_including_credentials(monkeypatch):
    from s9 import config
    from s9.product.service import ProductService

    service = ProductService.__new__(ProductService)
    service.adapter_enabled = True
    service.adapter_metadata = {"adapter_commit": "adapter", "package_version": "1.0"}
    monkeypatch.setattr(config, "MODEL", "model-a")
    monkeypatch.setattr(config, "MODEL_URL", "https://model.example/v1/chat/completions")
    monkeypatch.setattr(config, "MODEL_KEY", "configured-value-one")
    monkeypatch.setattr(config, "MODEL_CREDENTIAL_REVISION", "rotation-1")
    initial = service._configuration_sha256()

    monkeypatch.setattr(config, "MODEL_KEY", "configured-value-two")
    assert service._configuration_sha256() == initial
    monkeypatch.setattr(config, "MODEL", "model-b")
    assert service._configuration_sha256() != initial
    monkeypatch.setattr(config, "MODEL", "model-a")
    monkeypatch.setattr(config, "MODEL_CREDENTIAL_REVISION", "rotation-2")
    assert service._configuration_sha256() != initial


def test_versioned_credential_binding_fails_closed_when_missing(monkeypatch):
    import pytest
    from s9 import config
    from s9.product.registry import ProductError
    from s9.product.service import ProductService

    monkeypatch.setattr(config, "MODEL_KEY", "configured")
    for revision in ("", "unversioned", "unknown", "none"):
        monkeypatch.setattr(config, "MODEL_CREDENTIAL_REVISION", revision)
        with pytest.raises(ProductError, match="版本未标记") as caught:
            ProductService._require_versioned_credential_binding()
        assert caught.value.code == "CONFIGURATION_BINDING_UNVERSIONED"
    monkeypatch.setattr(config, "MODEL_CREDENTIAL_REVISION", "rotation-3")
    ProductService._require_versioned_credential_binding()


def test_approval_request_rejects_caller_supplied_identity():
    import pytest
    from pydantic import ValidationError
    from s9.api import ProductApprovalRequest

    with pytest.raises(ValidationError):
        ProductApprovalRequest.model_validate({
            "project_id": "support-agent", "environment_id": "local-test", "incident_id": "incident-1",
            "approver": "spoofed-human",
        })


def test_execution_audit_event_matches_outcome():
    from s9.product.service import _execution_event_type

    assert _execution_event_type(True) == "execution.succeeded"
    assert _execution_event_type(False) == "execution.failed"
