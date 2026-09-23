"""Strict schema types for the Section9 product API v1.

Workspace, Signal, Incident, manual InvestigationRun, Evidence, Hypothesis,
and the initial task-dispatch slice are wired to the local ProductRegistry.
Proposal/execution/validation contracts still exceed implemented behavior.
The API's current local-operator boundary is not multi-user or worker RBAC.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from urllib.parse import parse_qsl, urlsplit
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def _timestamp(value: str) -> str:
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


def _optional_timestamp(value: str | None) -> str | None:
    return None if value is None else _timestamp(value)


class V1Model(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class Revisioned(V1Model):
    id: str = Field(min_length=1, max_length=200)
    workspace_id: str = Field(min_length=1, max_length=200)
    revision: int = Field(ge=1)
    created_at: str
    updated_at: str

    _created_at = field_validator("created_at")(_timestamp)
    _updated_at = field_validator("updated_at")(_timestamp)


class DeploymentMode(StrEnum):
    LOCAL = "local"
    DEDICATED_CLOUD = "dedicated_cloud"
    SELF_HOSTED = "self_hosted"


class BudgetPolicy(V1Model):
    token_limit: int | None = Field(default=None, ge=0)
    validation_reserve_tokens: int = Field(default=0, ge=0)
    max_concurrency: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def reserve_fits_total(self) -> BudgetPolicy:
        if self.token_limit is not None and self.validation_reserve_tokens > self.token_limit:
            raise ValueError("validation reserve must not exceed the token limit")
        return self


class Workspace(Revisioned):
    name: str = Field(min_length=1, max_length=300)
    policy_revision: int = Field(ge=1)
    budget: BudgetPolicy
    deployment_mode: DeploymentMode

    @model_validator(mode="after")
    def id_is_its_scope(self) -> Workspace:
        if self.id != self.workspace_id:
            raise ValueError("workspace id and workspace scope must match")
        return self


class Application(Revisioned):
    name: str = Field(min_length=1, max_length=300)
    owner_id: str | None = Field(default=None, min_length=1, max_length=200)


class ConnectionState(StrEnum):
    PENDING = "pending"
    CONNECTED = "connected"
    DEGRADED = "degraded"
    PERMISSION_DENIED = "permission_denied"
    EXPIRED = "expired"
    DISCONNECTED = "disconnected"


class Connection(Revisioned):
    application_id: str = Field(min_length=1, max_length=200)
    provider: str = Field(min_length=1, max_length=200)
    endpoint: str | None = Field(default=None, max_length=2000)
    credential_ref: str | None = Field(default=None, max_length=500)
    capabilities: list[str] = Field(default_factory=list)
    status: ConnectionState
    checked_at: str | None = None
    latest_check: dict[str, Any] | None = None

    _checked_at = field_validator("checked_at")(_optional_timestamp)

    @field_validator("endpoint")
    @classmethod
    def endpoint_must_not_embed_credentials(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parsed = urlsplit(value)
        sensitive_query_keys = {"key", "api_key", "apikey", "token", "access_token", "secret", "password"}
        if parsed.username or parsed.password:
            raise ValueError("connection endpoint must not contain user information")
        if any(key.lower() in sensitive_query_keys for key, _ in parse_qsl(parsed.query)):
            raise ValueError("connection endpoint must not contain credential query parameters")
        return value


class ScopeBinding(Revisioned):
    application_id: str = Field(min_length=1, max_length=200)
    environment_id: str = Field(min_length=1, max_length=200)
    connection_id: str = Field(min_length=1, max_length=200)
    resource_type: str = Field(min_length=1, max_length=200)
    external_resource_id: str = Field(min_length=1, max_length=1000)
    read_scopes: list[str] = Field(default_factory=list)
    write_scopes: list[str] = Field(default_factory=list)
    status: Literal["pending", "confirmed", "permission_denied", "revoked"]
    checked_at: str | None = None
    latest_check: dict[str, Any] | None = None

    _checked_at = field_validator("checked_at")(_optional_timestamp)


class ScopeRef(V1Model):
    workspace_id: str = Field(min_length=1, max_length=200)
    application_id: str | None = Field(default=None, min_length=1, max_length=200)
    environment_id: str | None = Field(default=None, min_length=1, max_length=200)

    @model_validator(mode="after")
    def environment_requires_application(self) -> ScopeRef:
        if self.environment_id is not None and self.application_id is None:
            raise ValueError("environment_id requires application_id")
        return self


class Signal(Revisioned):
    application_id: str = Field(min_length=1, max_length=200)
    environment_id: str = Field(min_length=1, max_length=200)
    source_binding_id: str = Field(min_length=1, max_length=200)
    source_id: str | None = Field(default=None, min_length=1, max_length=1000)
    source_version: str | None = Field(default=None, min_length=1, max_length=300)
    deduplication_key: str = Field(min_length=1, max_length=500)
    signal_type: str = Field(min_length=1, max_length=200)
    source_kind: Literal["webhook", "poll", "manual", "chat"]
    occurred_at: str
    observed_at: str
    summary: str = Field(min_length=1, max_length=4000)
    status: Literal["new", "clustered", "suppressed", "dismissed"] = "new"

    _occurred_at = field_validator("occurred_at")(_timestamp)
    _observed_at = field_validator("observed_at")(_timestamp)


class IncidentState(StrEnum):
    OPEN = "open"
    INVESTIGATING = "investigating"
    NEEDS_INPUT = "needs_input"
    AWAITING_APPROVAL = "awaiting_approval"
    REMEDIATING = "remediating"
    VALIDATING = "validating"
    OBSERVING = "observing"
    RESOLVED = "resolved"
    DISMISSED = "dismissed"
    MERGED = "merged"


class Incident(Revisioned):
    application_id: str = Field(min_length=1, max_length=200)
    environment_id: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=500)
    severity: Literal["critical", "high", "medium", "low", "info"]
    signal_ids: list[str] = Field(default_factory=list)
    state: IncidentState
    outcome: Literal["resolved", "dismissed", "accepted_risk", "archived_unresolved", "merged"] | None = None
    merged_into_id: str | None = Field(default=None, min_length=1, max_length=200)
    assignee_id: str | None = Field(default=None, min_length=1, max_length=200)

    @model_validator(mode="after")
    def merged_incident_has_target(self) -> Incident:
        if self.state == IncidentState.MERGED:
            if self.outcome != "merged" or self.merged_into_id is None or self.assignee_id is not None:
                raise ValueError("merged incidents require a target, merged outcome, and no assignee")
        elif self.merged_into_id is not None:
            raise ValueError("only merged incidents may have a merge target")
        return self


class InvestigationRunState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    INCONCLUSIVE = "inconclusive"
    ABSTAINED = "abstained"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


class InvestigationRun(Revisioned):
    application_id: str = Field(min_length=1, max_length=200)
    environment_id: str = Field(min_length=1, max_length=200)
    incident_id: str = Field(min_length=1, max_length=200)
    input_snapshot_sha256: str = Field(pattern=r"^[0-9a-fA-F]{64}$")
    policy_revision: int = Field(ge=1)
    token_limit: int = Field(ge=0)
    validation_reserve_tokens: int = Field(default=0, ge=0)
    execution_mode: Literal["manual", "single", "swarm"] = "manual"
    state: InvestigationRunState
    result_type: Literal["proposal_ready", "root_cause_identified", "needs_data", "none"] | None = None
    started_at: str | None = None
    completed_at: str | None = None

    _started_at = field_validator("started_at")(_optional_timestamp)
    _completed_at = field_validator("completed_at")(_optional_timestamp)

    @model_validator(mode="after")
    def reserve_fits_run_budget(self) -> InvestigationRun:
        if self.validation_reserve_tokens > self.token_limit:
            raise ValueError("validation reserve must not exceed the run token limit")
        return self


class TaskState(StrEnum):
    OPEN = "open"
    CLAIMED = "claimed"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    BLOCKED = "blocked"


class Task(Revisioned):
    application_id: str = Field(min_length=1, max_length=200)
    environment_id: str = Field(min_length=1, max_length=200)
    incident_id: str = Field(min_length=1, max_length=200)
    run_id: str = Field(min_length=1, max_length=200)
    task_key: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,79}$")
    role: Literal["investigator", "challenger_seed", "challenger_review", "synthesizer"]
    capability: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=300)
    dependencies: list[str] = Field(default_factory=list)
    input_evidence_ids: list[str] = Field(default_factory=list)
    epoch: int = Field(ge=0)
    holder_id: str | None = Field(default=None, min_length=1, max_length=200)
    lease_deadline: str | None = None
    state: TaskState
    result: "TaskResult | None" = None
    failure_reason: str | None = Field(default=None, max_length=1000)
    attempt_history: list["TaskAttemptFailure"] = Field(default_factory=list)

    _lease_deadline = field_validator("lease_deadline")(_optional_timestamp)


class TaskSpec(V1Model):
    task_key: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,79}$")
    role: Literal["investigator", "challenger_seed", "challenger_review", "synthesizer"]
    capability: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=300)
    dependencies: list[str] = Field(default_factory=list, max_length=32)
    input_evidence_ids: list[str] = Field(default_factory=list, max_length=1000)

    @model_validator(mode="after")
    def role_capability_is_fixed(self) -> TaskSpec:
        expected = {
            "investigator": "evidence.investigate",
            "challenger_seed": "counterexample.prepare",
            "challenger_review": "counterexample.review",
            "synthesizer": "conclusion.synthesize",
        }[self.role]
        if self.capability != expected:
            raise ValueError(f"{self.role} requires capability {expected}")
        if len(set(self.dependencies)) != len(self.dependencies):
            raise ValueError("task dependencies must be unique")
        if len(set(self.input_evidence_ids)) != len(self.input_evidence_ids):
            raise ValueError("task evidence references must be unique")
        return self


class TaskGraphCreate(V1Model):
    environment_id: str = Field(min_length=1, max_length=200)
    expected_run_revision: int = Field(ge=1)
    mode: Literal["single", "swarm"]
    tasks: list[TaskSpec] = Field(min_length=2, max_length=32)

    @model_validator(mode="after")
    def graph_is_bounded_and_mode_safe(self) -> TaskGraphCreate:
        by_key = {item.task_key: item for item in self.tasks}
        if len(by_key) != len(self.tasks):
            raise ValueError("task keys must be unique")
        for item in self.tasks:
            if item.task_key in item.dependencies or any(key not in by_key for key in item.dependencies):
                raise ValueError("task dependencies must refer to other tasks in this graph")

        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(key: str) -> None:
            if key in visiting:
                raise ValueError("task graph must be acyclic")
            if key in visited:
                return
            visiting.add(key)
            for dependency in by_key[key].dependencies:
                visit(dependency)
            visiting.remove(key)
            visited.add(key)

        for key in by_key:
            visit(key)

        investigators = {item.task_key for item in self.tasks if item.role == "investigator"}
        seeds = [item for item in self.tasks if item.role == "challenger_seed"]
        reviews = [item for item in self.tasks if item.role == "challenger_review"]
        synthesizers = [item for item in self.tasks if item.role == "synthesizer"]
        if not investigators or len(synthesizers) != 1:
            raise ValueError("a task graph needs investigator tasks and exactly one synthesizer")
        synth = synthesizers[0]
        if self.mode == "single":
            if seeds or reviews:
                raise ValueError("single mode cannot include challenger tasks")
            if len(investigators) != 1:
                raise ValueError("single mode has exactly one investigator task")
            if not investigators.issubset(set(synth.dependencies)):
                raise ValueError("single synthesizer must depend on every investigator")
        else:
            if len(seeds) != 1 or len(reviews) != 1:
                raise ValueError("swarm mode requires one independent challenge seed and one review")
            seed, review = seeds[0], reviews[0]
            if seed.dependencies:
                raise ValueError("challenge seed must be independent of investigator outputs")
            if not investigators.issubset(set(review.dependencies)) or seed.task_key not in review.dependencies:
                raise ValueError("challenge review must depend on every investigator and the frozen seed")
            if review.task_key not in synth.dependencies or not investigators.issubset(set(synth.dependencies)):
                raise ValueError("swarm synthesizer must depend on investigators and challenge review")
        return self


class TaskHypothesis(V1Model):
    statement: str = Field(min_length=1, max_length=4000)
    confidence: float = Field(ge=0, le=1)
    support_evidence_ids: list[str] = Field(min_length=1, max_length=100)
    counterevidence_ids: list[str] = Field(default_factory=list, max_length=100)


class CounterexampleQuestion(V1Model):
    question: str = Field(min_length=1, max_length=1000)
    evidence_ids: list[str] = Field(min_length=1, max_length=100)
    failure_condition: str = Field(min_length=1, max_length=1000)


class TaskResult(V1Model):
    summary: str = Field(min_length=1, max_length=4000)
    evidence_ids: list[str] = Field(default_factory=list, max_length=1000)
    hypotheses: list[TaskHypothesis] = Field(default_factory=list, max_length=20)
    challenge_questions: list[CounterexampleQuestion] = Field(default_factory=list, max_length=20)
    review_verdict: Literal["supports", "challenges", "insufficient"] | None = None
    conclusion: Literal["root_cause_candidate", "needs_data", "abstain"] | None = None


class TaskAttemptFailure(V1Model):
    epoch: int = Field(ge=1)
    holder_id: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=1000)
    failed_at: str

    _failed_at = field_validator("failed_at")(_timestamp)


Task.model_rebuild()


class TaskGraph(Revisioned):
    application_id: str = Field(min_length=1, max_length=200)
    environment_id: str = Field(min_length=1, max_length=200)
    incident_id: str = Field(min_length=1, max_length=200)
    run_id: str = Field(min_length=1, max_length=200)
    mode: Literal["single", "swarm"]
    graph_sha256: str = Field(pattern=r"^[0-9a-fA-F]{64}$")
    state: Literal["ready", "running", "blocked", "complete"]
    tasks: list[Task] = Field(min_length=2, max_length=32)


class EvidenceClass(StrEnum):
    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"


class Evidence(Revisioned):
    application_id: str = Field(min_length=1, max_length=200)
    environment_id: str = Field(min_length=1, max_length=200)
    incident_id: str = Field(min_length=1, max_length=200)
    run_id: str = Field(min_length=1, max_length=200)
    source_binding_id: str = Field(min_length=1, max_length=200)
    source_version: str = Field(min_length=1, max_length=300)
    origin: str = Field(min_length=1, max_length=2000)
    source_occurred_at: str | None = None
    captured_at: str
    content_sha256: str = Field(pattern=r"^[0-9a-fA-F]{64}$")
    classification: EvidenceClass
    raw_blob_ref: str | None = Field(default=None, max_length=2000)
    sanitized_blob_ref: str | None = Field(default=None, max_length=2000)
    coverage_complete: bool

    _source_occurred_at = field_validator("source_occurred_at")(_optional_timestamp)
    _captured_at = field_validator("captured_at")(_timestamp)


class HypothesisState(StrEnum):
    PROPOSED = "proposed"
    SUPPORTED = "supported"
    REFUTED = "refuted"
    UNKNOWN = "unknown"
    WITHDRAWN = "withdrawn"


class Hypothesis(Revisioned):
    application_id: str = Field(min_length=1, max_length=200)
    environment_id: str = Field(min_length=1, max_length=200)
    incident_id: str = Field(min_length=1, max_length=200)
    run_id: str = Field(min_length=1, max_length=200)
    statement: str = Field(min_length=1, max_length=4000)
    confidence: float = Field(ge=0, le=1)
    support_evidence_ids: list[str] = Field(default_factory=list)
    counterevidence_ids: list[str] = Field(default_factory=list)
    state: HypothesisState
    source_task_id: str | None = Field(default=None, min_length=1, max_length=200)
    source_task_epoch: int | None = Field(default=None, ge=1)


class ProposalActionRef(V1Model):
    action_id: str = Field(min_length=1, max_length=200)
    schema_ref: str = Field(min_length=1, max_length=500)
    target_ref: str = Field(min_length=1, max_length=1000)
    payload_sha256: str = Field(pattern=r"^[0-9a-fA-F]{64}$")


class ProposalState(StrEnum):
    DRAFT = "draft"
    UNDER_REVIEW = "under_review"
    READY_FOR_APPROVAL = "ready_for_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"
    EXPIRED = "expired"


class ProposalVersion(Revisioned):
    application_id: str = Field(min_length=1, max_length=200)
    environment_id: str = Field(min_length=1, max_length=200)
    incident_id: str = Field(min_length=1, max_length=200)
    run_id: str = Field(min_length=1, max_length=200)
    proposal_id: str = Field(min_length=1, max_length=200)
    version: int = Field(ge=1)
    proposal_sha256: str = Field(pattern=r"^[0-9a-fA-F]{64}$")
    target_refs: list[str] = Field(min_length=1)
    actions: list[ProposalActionRef] = Field(min_length=1)
    risk_summary: str = Field(min_length=1, max_length=4000)
    rollback_conditions: list[str] = Field(default_factory=list)
    validation_plan_ref: str = Field(min_length=1, max_length=500)
    budget_token_limit: int = Field(ge=0)
    state: ProposalState


class ExecutionState(StrEnum):
    QUEUED = "queued"
    AUTHORIZED = "authorized"
    DISPATCH_COMMITTED = "dispatch_committed"
    RUNNING = "running"
    APPLIED = "applied"
    NOT_APPLIED = "not_applied"
    OUTCOME_UNKNOWN = "outcome_unknown"
    RECONCILING = "reconciling"
    MANUAL_REQUIRED = "manual_required"


class ResourceVersion(V1Model):
    resource_ref: str = Field(min_length=1, max_length=1000)
    version: str = Field(min_length=1, max_length=300)


class ExecutionAttempt(Revisioned):
    application_id: str = Field(min_length=1, max_length=200)
    environment_id: str = Field(min_length=1, max_length=200)
    incident_id: str = Field(min_length=1, max_length=200)
    run_id: str = Field(min_length=1, max_length=200)
    proposal_version_id: str = Field(min_length=1, max_length=200)
    proposal_sha256: str = Field(pattern=r"^[0-9a-fA-F]{64}$")
    idempotency_key: str = Field(min_length=1, max_length=200)
    resource_versions: list[ResourceVersion] = Field(min_length=1)
    state: ExecutionState
    external_operation_ref: str | None = Field(default=None, max_length=1000)
    result_sha256: str | None = Field(default=None, pattern=r"^[0-9a-fA-F]{64}$")
    started_at: str | None = None
    completed_at: str | None = None

    _started_at = field_validator("started_at")(_optional_timestamp)
    _completed_at = field_validator("completed_at")(_optional_timestamp)


class ValidationCheck(V1Model):
    name: str = Field(min_length=1, max_length=300)
    status: Literal["passed", "failed", "unknown", "not_run"]
    evidence_ids: list[str] = Field(default_factory=list)
    detail: str = Field(default="", max_length=4000)


class ValidationReport(Revisioned):
    application_id: str = Field(min_length=1, max_length=200)
    environment_id: str = Field(min_length=1, max_length=200)
    incident_id: str = Field(min_length=1, max_length=200)
    run_id: str = Field(min_length=1, max_length=200)
    proposal_version_id: str = Field(min_length=1, max_length=200)
    execution_attempt_id: str = Field(min_length=1, max_length=200)
    applied_version_sha256: str = Field(pattern=r"^[0-9a-fA-F]{64}$")
    contract_sha256: str = Field(pattern=r"^[0-9a-fA-F]{64}$")
    result: Literal["passed", "failed", "inconclusive", "blocked"]
    checks: list[ValidationCheck] = Field(min_length=1)
    evidence_ids: list[str] = Field(default_factory=list)
    completed_at: str

    _completed_at = field_validator("completed_at")(_timestamp)


class Error(V1Model):
    code: str = Field(min_length=1, max_length=100)
    message: str = Field(min_length=1, max_length=2000)
    retryable: bool
    scope_ref: ScopeRef | None = None
    request_id: str = Field(min_length=1, max_length=200)
    details: dict[str, Any] = Field(default_factory=dict)
    next_action: str | None = Field(default=None, max_length=1000)


class ErrorEnvelope(V1Model):
    error: Error


class AcceptedJob(V1Model):
    job_id: str = Field(min_length=1, max_length=200)
    state: Literal["queued", "running"]
    status_url: str = Field(min_length=1, max_length=2000)
    request_id: str = Field(min_length=1, max_length=200)
    accepted_at: str

    _accepted_at = field_validator("accepted_at")(_timestamp)


class CommandPreconditions(V1Model):
    idempotency_key: str = Field(min_length=1, max_length=200)
    expected_revision: int = Field(ge=1)
