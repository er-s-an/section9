from __future__ import annotations

from datetime import datetime
from enum import StrEnum
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    @field_validator("project_id", "environment_id", "incident_id", check_fields=False)
    @classmethod
    def non_empty_scope(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("scope identifiers must not be empty")
        return value


class EvidenceStatus(StrEnum):
    READY = "ready"
    UNKNOWN = "unknown"
    PERMISSION_DENIED = "permission_denied"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    UNSUPPORTED = "unsupported"


StatusValue = Literal["ready", "unknown", "permission_denied", "degraded", "unavailable", "unsupported"]


def _rfc3339(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("timestamp must be a non-empty RFC3339 string")
    candidate = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise ValueError("timestamp must be RFC3339") from exc
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return value


def _sha256(value: str) -> str:
    if not re.fullmatch(r"[0-9a-fA-F]{64}", value):
        raise ValueError("sha256 must be exactly 64 hexadecimal characters")
    return value.lower()


class ProjectScoped(StrictModel):
    project_id: str = Field(min_length=1, max_length=200)
    environment_id: str = Field(min_length=1, max_length=200)


class Scoped(ProjectScoped):
    incident_id: str = Field(min_length=1, max_length=200)


class ProjectManifest(ProjectScoped):
    manifest_id: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=300)
    created_at: str
    schema_version: str = Field(min_length=1, max_length=40)
    source_bindings: list[str] = Field(default_factory=list)

    _timestamp = field_validator("created_at")(_rfc3339)


class SourceBinding(ProjectScoped):
    binding_id: str = Field(min_length=1, max_length=200)
    source_type: str = Field(min_length=1, max_length=100)
    locator: str = Field(min_length=1, max_length=2000)
    observed_at: str
    content_sha256: str
    status: StatusValue = "ready"

    _timestamp = field_validator("observed_at")(_rfc3339)
    _hash = field_validator("content_sha256")(_sha256)

    def as_dict(self) -> dict[str, str]:
        return self.model_dump(mode="json")


class EvidenceRef(Scoped):
    evidence_id: str = Field(min_length=1, max_length=200)
    kind: str = Field(min_length=1, max_length=100)
    uri: str = Field(min_length=1, max_length=2000)
    captured_at: str
    content_sha256: str
    status: StatusValue = "ready"

    _timestamp = field_validator("captured_at")(_rfc3339)
    _hash = field_validator("content_sha256")(_sha256)


class ConnectionCapability(ProjectScoped):
    capability_id: str = Field(min_length=1, max_length=200)
    provider: str = Field(min_length=1, max_length=200)
    capability: str = Field(min_length=1, max_length=200)
    checked_at: str
    status: StatusValue
    detail: str = Field(default="", max_length=2000)

    _timestamp = field_validator("checked_at")(_rfc3339)

    @property
    def is_ready(self) -> bool:
        return self.status == "ready"


class ProbeResult(Scoped):
    probe_id: str = Field(min_length=1, max_length=200)
    capability_id: str = Field(min_length=1, max_length=200)
    probed_at: str
    status: StatusValue
    response_sha256: str | None = None
    detail: str = Field(default="", max_length=2000)

    _timestamp = field_validator("probed_at")(_rfc3339)
    _hash = field_validator("response_sha256")(_sha256)

    @property
    def is_ready(self) -> bool:
        return self.status == "ready"


class CandidateChange(Scoped):
    change_id: str = Field(min_length=1, max_length=200)
    proposed_at: str
    summary: str = Field(min_length=1, max_length=4000)
    base_ref: str = Field(min_length=1, max_length=300)
    candidate_ref: str = Field(min_length=1, max_length=300)
    patch_sha256: str
    changed_paths: list[str] = Field(default_factory=list)
    protected_paths_unchanged: bool
    verification_contract_sha256: str
    evidence_ids: list[str] = Field(default_factory=list)
    status: Literal["proposed", "rejected", "approved", "applied"] = "proposed"

    _timestamp = field_validator("proposed_at")(_rfc3339)
    _hash = field_validator("patch_sha256")(_sha256)
    _contract_hash = field_validator("verification_contract_sha256")(_sha256)


class VerificationReceipt(Scoped):
    receipt_id: str = Field(min_length=1, max_length=200)
    verified_at: str
    status: StatusValue
    evidence_ids: list[str] = Field(default_factory=list)
    candidate_id: str = Field(min_length=1, max_length=200)
    configuration_sha256: str
    base_ref: str = Field(min_length=1, max_length=300)
    candidate_ref: str = Field(min_length=1, max_length=300)
    original_failed: bool
    candidate_passed: bool
    no_regressions: bool
    checks: list[dict[str, Any]] = Field(default_factory=list)
    result_sha256: str | None = None
    detail: str = Field(default="", max_length=4000)

    _timestamp = field_validator("verified_at")(_rfc3339)
    _hash = field_validator("result_sha256")(_sha256)
    _configuration_hash = field_validator("configuration_sha256")(_sha256)

    @property
    def is_ready(self) -> bool:
        return self.status is EvidenceStatus.READY


class Approval(Scoped):
    approval_id: str = Field(min_length=1, max_length=200)
    approver: str = Field(min_length=1, max_length=300)
    approved_at: str
    decision: Literal["approved", "rejected"]
    change_id: str = Field(min_length=1, max_length=200)
    verification_receipt_id: str = Field(min_length=1, max_length=200)
    target_revision: str = Field(min_length=1, max_length=300)
    policy_version: str = Field(min_length=1, max_length=100)
    configuration_sha256: str
    expires_at: str

    _timestamp = field_validator("approved_at")(_rfc3339)
    _expiry = field_validator("expires_at")(_rfc3339)
    _configuration_hash = field_validator("configuration_sha256")(_sha256)


class ExecutionAttempt(Scoped):
    attempt_id: str = Field(min_length=1, max_length=200)
    change_id: str = Field(min_length=1, max_length=200)
    started_at: str
    status: Literal["planned", "running", "succeeded", "failed", "unknown", "permission_denied"]
    idempotency_key: str = Field(min_length=1, max_length=200)
    target_revision: str = Field(min_length=1, max_length=300)
    configuration_sha256: str
    external_operation_id: str | None = None
    result_sha256: str | None = None
    detail: str = Field(default="", max_length=4000)

    _timestamp = field_validator("started_at")(_rfc3339)
    _hash = field_validator("result_sha256")(_sha256)
    _configuration_hash = field_validator("configuration_sha256")(_sha256)


class RecoveryReceipt(Scoped):
    receipt_id: str = Field(min_length=1, max_length=200)
    attempt_id: str = Field(min_length=1, max_length=200)
    recovered_at: str
    status: Literal["recovered", "not_recovered", "unknown", "permission_denied", "degraded"]
    source_revision: str = Field(min_length=1, max_length=300)
    configuration_sha256: str
    evidence_ids: list[str] = Field(default_factory=list)
    source_binding_id: str = Field(min_length=1, max_length=200)
    observation_window_seconds: float = Field(ge=0)
    business_probe_passed: bool | None = None
    detail: str = Field(default="", max_length=4000)

    _timestamp = field_validator("recovered_at")(_rfc3339)
    _configuration_hash = field_validator("configuration_sha256")(_sha256)
