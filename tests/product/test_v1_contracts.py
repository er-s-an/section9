import pytest
from pydantic import ValidationError

from s9.product.v1_contracts import (
    AcceptedJob,
    BudgetPolicy,
    Connection,
    ErrorEnvelope,
    ExecutionAttempt,
    ExecutionState,
    Incident,
    IncidentState,
    InvestigationRun,
    InvestigationRunState,
    ProposalActionRef,
    ProposalState,
    ProposalVersion,
    ResourceVersion,
    ScopeRef,
    TaskGraphCreate,
    Workspace,
    DeploymentMode,
)


NOW = "2026-09-23T00:00:00Z"
HASH = "a" * 64


def _revisioned(**overrides):
    return {
        "id": "object_01",
        "workspace_id": "workspace_01",
        "revision": 1,
        "created_at": NOW,
        "updated_at": NOW,
        **overrides,
    }


def test_workspace_scope_and_budget_are_explicit_and_strict():
    workspace = Workspace(
        **_revisioned(id="workspace_01", workspace_id="workspace_01", name="Local", policy_revision=3,
            budget=BudgetPolicy(token_limit=1000,
            validation_reserve_tokens=100, max_concurrency=2), deployment_mode=DeploymentMode.LOCAL),
    )
    assert workspace.workspace_id == "workspace_01"
    with pytest.raises(ValidationError):
        BudgetPolicy(token_limit=50, validation_reserve_tokens=100)
    with pytest.raises(ValidationError):
        ScopeRef(workspace_id="workspace_01", environment_id="prod")
    with pytest.raises(ValidationError):
        Workspace(**_revisioned(id="workspace_01", workspace_id="workspace_01", name="Local",
            policy_revision=3, budget=BudgetPolicy(),
            deployment_mode=DeploymentMode.LOCAL, api_key="not-a-contract-field"))


def test_incident_and_investigation_run_have_separate_identities_and_terminal_state():
    incident = Incident(
        **_revisioned(id="incident_01", application_id="app_01", environment_id="prod", title="Latency",
            severity="high", signal_ids=["signal_01"], state=IncidentState.INVESTIGATING),
    )
    run = InvestigationRun(
        **_revisioned(id="run_01", application_id="app_01", environment_id="prod",
            incident_id=incident.id, input_snapshot_sha256=HASH, policy_revision=3, token_limit=1000,
            validation_reserve_tokens=100, state=InvestigationRunState.FAILED),
    )
    assert incident.id != run.id
    assert run.incident_id == incident.id
    assert run.state is InvestigationRunState.FAILED
    with pytest.raises(ValidationError):
        InvestigationRun(**_revisioned(id="run_02", application_id="app_01", environment_id="prod",
            incident_id=incident.id, input_snapshot_sha256=HASH, policy_revision=3, token_limit=50,
            validation_reserve_tokens=100, state=InvestigationRunState.RUNNING))


def test_proposal_version_is_frozen_and_execution_binds_the_exact_content():
    proposal = ProposalVersion(
        **_revisioned(id="proposal_version_01", application_id="app_01", incident_id="incident_01",
            environment_id="prod", run_id="run_01", proposal_id="proposal_01", version=1, proposal_sha256=HASH,
            target_refs=["service:checkout"], actions=[ProposalActionRef(action_id="action_01",
                schema_ref="section9://actions/config-patch/v1", target_ref="service:checkout",
                payload_sha256=HASH)], risk_summary="Restart may cause a brief interruption.",
            rollback_conditions=["Health check fails"], validation_plan_ref="validation-plan_01",
            budget_token_limit=500, state=ProposalState.READY_FOR_APPROVAL),
    )
    with pytest.raises(ValidationError):
        proposal.version = 2

    attempt = ExecutionAttempt(
        **_revisioned(id="execution_01", application_id="app_01", incident_id="incident_01",
            environment_id="prod", run_id="run_02", proposal_version_id=proposal.id,
            proposal_sha256=proposal.proposal_sha256,
            idempotency_key="idem_01", resource_versions=[ResourceVersion(resource_ref="service:checkout",
                version="sha256:" + HASH)], state=ExecutionState.QUEUED),
    )
    assert attempt.proposal_version_id == proposal.id
    assert attempt.proposal_sha256 == proposal.proposal_sha256


def test_error_and_async_job_shapes_are_machine_readable():
    error = ErrorEnvelope.model_validate({"error": {"code": "RUN_TERMINAL", "message": "Run has ended",
        "retryable": False, "scope_ref": {"workspace_id": "workspace_01", "application_id": "app_01",
        "environment_id": "prod"}, "request_id": "req_01", "details": {}, "next_action": "Create a new run"}})
    job = AcceptedJob(job_id="job_01", state="queued", status_url="/api/v1/jobs/job_01",
        request_id="req_01", accepted_at=NOW)
    assert error.error.code == "RUN_TERMINAL"
    assert job.status_url.endswith(job.job_id)
    with pytest.raises(ValidationError):
        AcceptedJob(job_id="job_01", state="completed", status_url="/job", request_id="req_01", accepted_at=NOW)


def test_public_timestamps_require_timezone_and_objects_reject_extra_fields():
    with pytest.raises(ValidationError):
        AcceptedJob(job_id="job_01", state="queued", status_url="/job", request_id="req_01",
            accepted_at="2026-09-23T00:00:00")
    with pytest.raises(ValidationError):
        ScopeRef(workspace_id="workspace_01", application_id="app_01", internal_actor="spoofed")


def test_connection_endpoint_rejects_embedded_credentials():
    with pytest.raises(ValidationError):
        Connection(**_revisioned(application_id="app_01", provider="GitHub",
            endpoint="https://user:pass@example.invalid/api", credential_ref="keychain://section9/gh",
            capabilities=[], status="pending"))
    with pytest.raises(ValidationError):
        Connection(**_revisioned(application_id="app_01", provider="GitHub",
            endpoint="https://example.invalid/api?access_token=fake", credential_ref="keychain://section9/gh",
            capabilities=[], status="pending"))


def test_swarm_task_graph_requires_independent_seed_and_acyclic_dependencies():
    tasks = [
        {"task_key": "investigate", "role": "investigator", "capability": "evidence.investigate",
         "title": "Investigate", "dependencies": [], "input_evidence_ids": ["ev_1"]},
        {"task_key": "seed", "role": "challenger_seed", "capability": "counterexample.prepare",
         "title": "Seed", "dependencies": [], "input_evidence_ids": ["ev_1"]},
        {"task_key": "review", "role": "challenger_review", "capability": "counterexample.review",
         "title": "Review", "dependencies": ["investigate", "seed"], "input_evidence_ids": ["ev_1"]},
        {"task_key": "synthesize", "role": "synthesizer", "capability": "conclusion.synthesize",
         "title": "Synthesize", "dependencies": ["investigate", "review"], "input_evidence_ids": ["ev_1"]},
    ]
    graph = TaskGraphCreate(environment_id="prod", expected_run_revision=1, mode="swarm", tasks=tasks)
    assert graph.mode == "swarm"

    seed_sees_conclusion = [dict(item) for item in tasks]
    seed_sees_conclusion[1] = {**seed_sees_conclusion[1], "dependencies": ["investigate"]}
    with pytest.raises(ValidationError):
        TaskGraphCreate(environment_id="prod", expected_run_revision=1,
            mode="swarm", tasks=seed_sees_conclusion)

    cycle = [dict(item) for item in tasks]
    cycle[0] = {**cycle[0], "dependencies": ["review"]}
    with pytest.raises(ValidationError):
        TaskGraphCreate(environment_id="prod", expected_run_revision=1, mode="swarm", tasks=cycle)
