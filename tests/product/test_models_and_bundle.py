import hashlib
import json

import pytest
from pydantic import ValidationError

from s9.product import ArtifactBundleWriter, EvidenceRef, ProbeResult, ProjectManifest, redact_secrets


SCOPE = {"project_id": "p", "environment_id": "dev", "incident_id": "i"}
PROJECT_SCOPE = {"project_id": "p", "environment_id": "dev"}
HASH = "a" * 64


def test_scope_hash_and_rfc3339_are_strict():
    with pytest.raises(ValidationError):
        EvidenceRef(**SCOPE, evidence_id="e", kind="log", uri="x", captured_at="2026-09-23T01:00:00", content_sha256=HASH)
    with pytest.raises(ValidationError):
        EvidenceRef(**SCOPE, evidence_id="e", kind="log", uri="x", captured_at="2026-09-23T01:00:00Z", content_sha256="bad")
    assert EvidenceRef(**SCOPE, evidence_id="e", kind="log", uri="x", captured_at="2026-09-23T01:00:00Z", content_sha256=HASH).content_sha256 == HASH


def test_unknown_like_states_are_not_ready():
    result = ProbeResult(**SCOPE, probe_id="p1", capability_id="c1", probed_at="2026-09-23T01:00:00Z", status="unknown")
    assert result.is_ready is False


def test_extra_fields_are_rejected():
    with pytest.raises(ValidationError):
        ProjectManifest(**PROJECT_SCOPE, manifest_id="m", name="n", created_at="2026-09-23T01:00:00Z", schema_version="1", leaked="x")


def test_redaction_is_recursive_but_preserves_non_secret_content_hashes():
    value = redact_secrets({"nested": {"api_key": "placeholder", "content_sha256": HASH,
                                        "token_budget": 16000, "total_tokens": 42, "prompt_tokens": 13,
                                        "access_token": "placeholder"},
                            "model_usage": {"budget_tokens": 16000,
                                            "known_tokens": {"order_truth": 42, "refund_guardrail": 13},
                                            "known_total_tokens": 55,
                                            "per_probe_reservation_tokens": 5000},
                            "ok": "kept"})
    assert value == {"nested": {"api_key": "[REDACTED]", "content_sha256": HASH,
                                  "token_budget": 16000, "total_tokens": 42, "prompt_tokens": 13,
                                  "access_token": "[REDACTED]"},
                     "model_usage": {"budget_tokens": 16000,
                                     "known_tokens": {"order_truth": 42, "refund_guardrail": 13},
                                     "known_total_tokens": 55,
                                     "per_probe_reservation_tokens": 5000},
                     "ok": "kept"}


def test_bundle_writes_redacted_json_and_manifest_sha256(tmp_path):
    record = {"project_id": "p", "token": "do-not-write", "nested": {"password": "x"}}
    target = ArtifactBundleWriter(tmp_path).write([record], bundle_name="evidence")
    raw = target.read_bytes()
    assert b"do-not-write" not in raw and b"[REDACTED]" in raw
    checksum = (tmp_path / "evidence.sha256").read_text().split()[0]
    assert checksum == hashlib.sha256(raw).hexdigest()
    assert json.loads(raw)["records"][0]["nested"]["password"] == "[REDACTED]"
