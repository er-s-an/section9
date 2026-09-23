"""Initial local HTTP surface for the Workspace v1 product registry.

The parent application applies its loopback/local-origin operator boundary to
these routes. This module deliberately exposes setup records as pending; it
does not perform external connection validation or imply data access.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import ipaddress
import re
from typing import Annotated, Literal
from uuid import uuid4

import httpx
from fastapi import APIRouter, Header, Query, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import Field, model_validator

from s9.product.registry import ProductError, ProductRegistry
from s9.product.connection_checks import (
    check_github_binding,
    check_langfuse_binding,
    read_github_issues,
    read_langfuse_observations,
)
from s9.product.credentials import CredentialUnavailable, ProductCredentialBroker
from s9.product.task_runtime import InvestigationTaskRuntime
from s9.product.v1_contracts import (
    BudgetPolicy, DeploymentMode, HypothesisState, IncidentState, TaskResult, V1Model,
)


router = APIRouter(prefix="/api/v1", tags=["product-v1"])


class WorkspaceCreate(V1Model):
    name: str = Field(min_length=1, max_length=300)
    budget: BudgetPolicy = Field(default_factory=BudgetPolicy)
    deployment_mode: DeploymentMode = Field(default=DeploymentMode.LOCAL, strict=False)


class WorkspaceBudgetUpdate(V1Model):
    expected_revision: int = Field(ge=1)
    budget: BudgetPolicy


class ApplicationCreate(V1Model):
    name: str = Field(min_length=1, max_length=300)
    owner_id: str | None = Field(default=None, min_length=1, max_length=200)


class ConnectionCreate(V1Model):
    provider: str = Field(min_length=1, max_length=200)
    endpoint: str | None = Field(default=None, max_length=2000)
    credential_ref: str | None = Field(default=None, max_length=500)
    capabilities: list[str] = Field(default_factory=list)


class ScopeBindingCreate(V1Model):
    environment_id: str = Field(min_length=1, max_length=200)
    connection_id: str = Field(min_length=1, max_length=200)
    resource_type: str = Field(min_length=1, max_length=200)
    external_resource_id: str = Field(min_length=1, max_length=1000)
    read_scopes: list[str] = Field(default_factory=list)
    write_scopes: list[str] = Field(default_factory=list)


class VerifyScopeBinding(V1Model):
    environment_id: str = Field(min_length=1, max_length=200)
    expected_connection_revision: int = Field(ge=1)
    expected_binding_revision: int = Field(ge=1)


class SignalInput(V1Model):
    source_id: str | None = Field(default=None, min_length=1, max_length=1000)
    source_version: str | None = Field(default=None, min_length=1, max_length=300)
    deduplication_key: str = Field(min_length=1, max_length=500)
    signal_type: str = Field(min_length=1, max_length=200)
    source_kind: Literal["webhook", "poll", "manual", "chat"]
    occurred_at: str
    observed_at: str
    summary: str = Field(min_length=1, max_length=4000)


class SignalIngest(V1Model):
    environment_id: str = Field(min_length=1, max_length=200)
    source_binding_id: str = Field(min_length=1, max_length=200)
    items: list[SignalInput] = Field(max_length=1000)


class SignalReference(V1Model):
    signal_id: str = Field(min_length=1, max_length=200)
    expected_revision: int = Field(ge=1)


class IncidentCreate(V1Model):
    environment_id: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=500)
    severity: Literal["critical", "high", "medium", "low", "info"]
    signals: list[SignalReference] = Field(min_length=1, max_length=100)


class IncidentTransition(V1Model):
    environment_id: str = Field(min_length=1, max_length=200)
    expected_revision: int = Field(ge=1)
    target_state: Literal[
        "open", "investigating", "needs_input", "awaiting_approval",
        "remediating", "validating", "observing", "resolved", "dismissed",
    ]
    reason: str = Field(min_length=1, max_length=1000)


class IncidentSignalAttachment(V1Model):
    environment_id: str = Field(min_length=1, max_length=200)
    expected_incident_revision: int = Field(ge=1)
    signals: list[SignalReference] = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def signal_ids_are_unique(self):
        ids = [item.signal_id for item in self.signals]
        if len(set(ids)) != len(ids):
            raise ValueError("signal IDs must be unique")
        return self


class IncidentMerge(V1Model):
    environment_id: str = Field(min_length=1, max_length=200)
    target_incident_id: str = Field(min_length=1, max_length=200)
    expected_incident_revision: int = Field(ge=1)
    expected_target_revision: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=1000)


class IncidentAssignment(V1Model):
    environment_id: str = Field(min_length=1, max_length=200)
    expected_revision: int = Field(ge=1)
    action: Literal["claim", "release"]
    reason: str = Field(min_length=1, max_length=1000)


class IncidentSeverityUpdate(V1Model):
    environment_id: str = Field(min_length=1, max_length=200)
    expected_revision: int = Field(ge=1)
    severity: Literal["critical", "high", "medium", "low", "info"]
    reason: str = Field(min_length=1, max_length=1000)


class IncidentSplit(V1Model):
    environment_id: str = Field(min_length=1, max_length=200)
    expected_incident_revision: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=500)
    severity: Literal["critical", "high", "medium", "low", "info"]
    signals: list[SignalReference] = Field(min_length=1, max_length=99)
    reason: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def signal_ids_are_unique(self):
        ids = [item.signal_id for item in self.signals]
        if len(set(ids)) != len(ids):
            raise ValueError("signal IDs must be unique")
        return self


class ManualInvestigationRunCreate(V1Model):
    environment_id: str = Field(min_length=1, max_length=200)
    expected_incident_revision: int = Field(ge=1)


class InvestigationRunCreate(V1Model):
    environment_id: str = Field(min_length=1, max_length=200)
    expected_incident_revision: int = Field(ge=1)
    mode: Literal["single", "swarm"]


class InvestigationRunExecute(V1Model):
    environment_id: str = Field(min_length=1, max_length=200)


class InvestigationTaskClaim(V1Model):
    environment_id: str = Field(min_length=1, max_length=200)
    expected_revision: int = Field(ge=1)
    worker_id: str = Field(min_length=1, max_length=200)
    capabilities: list[str] = Field(min_length=1, max_length=32)
    lease_seconds: int = Field(default=60, ge=10, le=300)

    @model_validator(mode="after")
    def capabilities_are_unique(self):
        if len(set(self.capabilities)) != len(self.capabilities):
            raise ValueError("worker capabilities must be unique")
        return self


class InvestigationWorkerCreate(V1Model):
    environment_id: str = Field(min_length=1, max_length=200)
    worker_id: str = Field(min_length=1, max_length=200)
    capabilities: list[str] = Field(min_length=1, max_length=4)

    @model_validator(mode="after")
    def capabilities_are_unique(self):
        if len(set(self.capabilities)) != len(self.capabilities):
            raise ValueError("worker capabilities must be unique")
        return self


class InvestigationTaskRenew(V1Model):
    environment_id: str = Field(min_length=1, max_length=200)
    expected_revision: int = Field(ge=1)
    worker_id: str = Field(min_length=1, max_length=200)
    epoch: int = Field(ge=1)
    lease_seconds: int = Field(default=60, ge=10, le=300)


class InvestigationTaskFinish(V1Model):
    environment_id: str = Field(min_length=1, max_length=200)
    expected_revision: int = Field(ge=1)
    worker_id: str = Field(min_length=1, max_length=200)
    epoch: int = Field(ge=1)
    result: TaskResult | None = None
    failure_reason: str | None = Field(default=None, min_length=1, max_length=1000)

    @model_validator(mode="after")
    def require_one_outcome(self):
        if (self.result is None) == (self.failure_reason is None):
            raise ValueError("submit exactly one task result or failure reason")
        return self


class InvestigationTaskRetry(V1Model):
    environment_id: str = Field(min_length=1, max_length=200)
    expected_revision: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=1000)


class HypothesisCreate(V1Model):
    environment_id: str = Field(min_length=1, max_length=200)
    expected_run_revision: int = Field(ge=1)
    statement: str = Field(min_length=1, max_length=4000)
    confidence: float = Field(ge=0, le=1)
    support_evidence_ids: list[str] = Field(default_factory=list, max_length=100)
    counterevidence_ids: list[str] = Field(default_factory=list, max_length=100)


class HypothesisDecision(V1Model):
    environment_id: str = Field(min_length=1, max_length=200)
    expected_revision: int = Field(ge=1)
    target_state: Literal["supported", "refuted", "unknown", "withdrawn"]
    confidence: float = Field(ge=0, le=1)
    support_evidence_ids: list[str] = Field(default_factory=list, max_length=100)
    counterevidence_ids: list[str] = Field(default_factory=list, max_length=100)
    reason: str = Field(min_length=1, max_length=1000)


class ManualInvestigationFinish(V1Model):
    environment_id: str = Field(min_length=1, max_length=200)
    expected_revision: int = Field(ge=1)
    result_type: Literal["root_cause_identified", "needs_data", "none"]
    reason: str = Field(min_length=1, max_length=1000)


class LangfuseSignalImport(V1Model):
    environment_id: str = Field(min_length=1, max_length=200)
    from_start_time: str = Field(min_length=1, max_length=40)
    to_start_time: str = Field(min_length=1, max_length=40)
    cursor: str | None = Field(default=None, max_length=2000)
    page_size: int = Field(default=100, ge=1, le=1000)
    max_pages: int = Field(default=5, ge=1, le=10)

    @model_validator(mode="after")
    def validate_bounded_window(self):
        def parse(value: str) -> datetime:
            candidate = value[:-1] + "+00:00" if value.endswith("Z") else value
            parsed = datetime.fromisoformat(candidate)
            if parsed.tzinfo is None:
                raise ValueError("Langfuse import timestamps must include a timezone")
            return parsed

        try:
            start, end = parse(self.from_start_time), parse(self.to_start_time)
        except ValueError as exc:
            raise ValueError("Langfuse import timestamps must be RFC3339") from exc
        duration = end - start
        if duration.total_seconds() <= 0 or duration.total_seconds() > 31 * 24 * 60 * 60:
            raise ValueError("Langfuse import window must be positive and no longer than 31 days")
        return self


class GitHubSignalImport(V1Model):
    environment_id: str = Field(min_length=1, max_length=200)
    from_updated_at: str = Field(min_length=1, max_length=40)
    to_updated_at: str = Field(min_length=1, max_length=40)
    cursor: str | None = Field(default=None, pattern=r"^page:[1-9][0-9]{0,5}$")
    page_size: int = Field(default=100, ge=1, le=100)
    max_pages: int = Field(default=5, ge=1, le=10)

    @model_validator(mode="after")
    def validate_bounded_window(self):
        def parse(value: str) -> datetime:
            candidate = value[:-1] + "+00:00" if value.endswith("Z") else value
            parsed = datetime.fromisoformat(candidate)
            if parsed.tzinfo is None:
                raise ValueError("GitHub import timestamps must include a timezone")
            return parsed

        try:
            start, end = parse(self.from_updated_at), parse(self.to_updated_at)
        except ValueError as exc:
            raise ValueError("GitHub import timestamps must be RFC3339") from exc
        duration = end - start
        if duration.total_seconds() <= 0 or duration.total_seconds() > 31 * 24 * 60 * 60:
            raise ValueError("GitHub import window must be positive and no longer than 31 days")
        return self


class SignalSyncMonitorConfigure(V1Model):
    environment_id: str = Field(min_length=1, max_length=200)
    enabled: bool
    interval_seconds: int = Field(ge=60, le=86_400)
    expected_revision: int = Field(ge=0)


def _broker(request: Request) -> ProductCredentialBroker:
    broker = getattr(request.app.state, "product_credentials", None)
    return broker if broker is not None else ProductCredentialBroker()


def _registry(request: Request) -> ProductRegistry:
    core = getattr(request.app.state, "core", None)
    product = getattr(core, "product", None)
    registry = getattr(product, "registry", None)
    if registry is None:
        raise ProductError("PRODUCT_REGISTRY_UNAVAILABLE", "产品注册表尚未初始化", 503)
    return registry


def _task_runtime(request: Request) -> InvestigationTaskRuntime:
    runtime = getattr(request.app.state, "product_task_runtime", None)
    if runtime is None:
        raise ProductError("TASK_RUNTIME_UNAVAILABLE", "本地模型执行器尚未初始化", 503)
    return runtime


def _require_local_operator(request: Request) -> None:
    if getattr(request.app.state, "product_test_local_operator", False):
        return
    host = request.client.host if request.client is not None else ""
    try:
        is_loopback = ipaddress.ip_address(host.split("%", 1)[0]).is_loopback
    except ValueError:
        is_loopback = False
    if not is_loopback:
        raise ProductError("LOCAL_OPERATOR_REQUIRED", "Worker 管理只允许本机操作台访问", 403)


def _authenticate_task_worker(
    request: Request, workspace_id: str, application_id: str,
    environment_id: str, claimed_worker_id: str,
) -> dict:
    authorization = request.headers.get("authorization", "")
    scheme, separator, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not separator or not token.strip():
        raise ProductError("WORKER_CREDENTIAL_REQUIRED", "此 Worker 操作需要 Bearer 凭据", 401)
    identity = _registry(request).authenticate_investigation_worker(
        token.strip(), workspace_id, application_id, environment_id,
    )
    if identity["worker_id"] != claimed_worker_id:
        raise ProductError("WORKER_IDENTITY_MISMATCH", "请求中的 Worker 身份与凭据不匹配", 403)
    return identity


def _require(item: dict | None, code: str, message: str) -> dict:
    if item is None:
        raise ProductError(code, message, 404)
    return item


def v1_error_response(
    request: Request,
    *,
    code: str,
    message: str,
    status_code: int,
    retryable: bool = False,
    details: dict | None = None,
    next_action: str = "Check the error code and local operator access.",
) -> JSONResponse:
    return JSONResponse({"error": {
        "code": code,
        "message": message,
        "retryable": retryable,
        "scope_ref": None,
        "request_id": getattr(request.state, "request_id", uuid4().hex),
        "details": details or {},
        "next_action": next_action,
    }}, status_code=status_code)


def product_error_response(request: Request, exc: ProductError) -> JSONResponse:
    if exc.status == 409:
        next_action = "Reload the resource; use a new idempotency key only for a new command."
    elif exc.status == 422:
        next_action = "Correct the request fields and submit a valid command."
    elif exc.status == 404:
        next_action = "Check the workspace, application, environment, and resource identifiers."
    elif exc.status >= 500:
        next_action = "Check local service readiness, then retry safely."
    else:
        next_action = "Check the error code and local operator access."
    return v1_error_response(request, code=exc.code, message=exc.message, status_code=exc.status,
                             retryable=exc.status >= 500, next_action=next_action)


def validation_error_response(request: Request, exc: RequestValidationError) -> JSONResponse:
    fields = [{"location": [str(part) for part in item.get("loc", ())], "code": item.get("type", "invalid")}
              for item in exc.errors()]
    return v1_error_response(request, code="INVALID_REQUEST", message="请求字段不符合产品 API v1 契约",
        status_code=422, details={"fields": fields}, next_action="Correct the listed fields and submit a valid command.")


@router.get("/workspaces")
async def workspaces(request: Request):
    return {"items": _registry(request).list_workspaces()}


@router.post("/workspaces", status_code=status.HTTP_201_CREATED)
async def create_workspace(
    item: WorkspaceCreate,
    request: Request,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=200)],
):
    return _registry(request).create_workspace(
        item.name,
        idempotency_key=idempotency_key,
        budget=item.budget,
        deployment_mode=item.deployment_mode,
    )


@router.get("/workspaces/{workspace_id}")
async def workspace(workspace_id: str, request: Request):
    return _require(_registry(request).get_workspace(workspace_id), "WORKSPACE_NOT_FOUND", "工作区不存在")


@router.put("/workspaces/{workspace_id}/budget")
async def update_workspace_budget(
    workspace_id: str,
    item: WorkspaceBudgetUpdate,
    request: Request,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=200)],
):
    return _registry(request).update_workspace_budget(
        workspace_id, item.budget, expected_revision=item.expected_revision,
        idempotency_key=idempotency_key,
    )


@router.get("/workspaces/{workspace_id}/applications")
async def applications(workspace_id: str, request: Request):
    return {"items": _registry(request).list_applications(workspace_id)}


@router.post("/workspaces/{workspace_id}/applications", status_code=status.HTTP_201_CREATED)
async def create_application(
    workspace_id: str,
    item: ApplicationCreate,
    request: Request,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=200)],
):
    return _registry(request).create_application(
        workspace_id, item.name, idempotency_key=idempotency_key, owner_id=item.owner_id,
    )


@router.get("/workspaces/{workspace_id}/applications/{application_id}")
async def application(workspace_id: str, application_id: str, request: Request):
    return _require(
        _registry(request).get_application(workspace_id, application_id),
        "APPLICATION_NOT_FOUND", "应用不属于该工作区或不存在",
    )


@router.get("/workspaces/{workspace_id}/applications/{application_id}/connections")
async def connections(workspace_id: str, application_id: str, request: Request):
    return {"items": _registry(request).list_connections(workspace_id, application_id)}


@router.post(
    "/workspaces/{workspace_id}/applications/{application_id}/connections",
    status_code=status.HTTP_201_CREATED,
)
async def create_connection(
    workspace_id: str,
    application_id: str,
    item: ConnectionCreate,
    request: Request,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=200)],
):
    return _registry(request).create_connection(
        workspace_id,
        application_id,
        item.provider,
        idempotency_key=idempotency_key,
        endpoint=item.endpoint,
        credential_ref=item.credential_ref,
        capabilities=item.capabilities,
    )


@router.get("/workspaces/{workspace_id}/applications/{application_id}/connections/{connection_id}")
async def connection(workspace_id: str, application_id: str, connection_id: str, request: Request):
    return _require(
        _registry(request).get_connection(workspace_id, application_id, connection_id),
        "CONNECTION_NOT_FOUND", "连接不属于该应用或不存在",
    )


@router.get("/workspaces/{workspace_id}/applications/{application_id}/scope-bindings")
async def scope_bindings(
    workspace_id: str,
    application_id: str,
    environment_id: Annotated[str, Query(min_length=1, max_length=200)],
    request: Request,
):
    return {"items": _registry(request).list_scope_bindings(workspace_id, application_id, environment_id)}


@router.post(
    "/workspaces/{workspace_id}/applications/{application_id}/scope-bindings",
    status_code=status.HTTP_201_CREATED,
)
async def create_scope_binding(
    workspace_id: str,
    application_id: str,
    item: ScopeBindingCreate,
    request: Request,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=200)],
):
    return _registry(request).create_scope_binding(
        workspace_id,
        application_id,
        item.environment_id,
        item.connection_id,
        item.resource_type,
        item.external_resource_id,
        idempotency_key=idempotency_key,
        read_scopes=item.read_scopes,
        write_scopes=item.write_scopes,
    )


@router.post(
    "/workspaces/{workspace_id}/applications/{application_id}/connections/{connection_id}/scope-bindings/{binding_id}/verify",
)
async def verify_read_scope_binding(
    workspace_id: str,
    application_id: str,
    connection_id: str,
    binding_id: str,
    item: VerifyScopeBinding,
    request: Request,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=200)],
):
    registry = _registry(request)
    connection_item = _require(
        registry.get_connection(workspace_id, application_id, connection_id),
        "CONNECTION_NOT_FOUND", "连接不属于该应用或不存在",
    )
    binding = _require(
        registry.get_scope_binding(workspace_id, application_id,
            item.environment_id, binding_id),
        "SCOPE_BINDING_NOT_FOUND", "资源范围不属于该连接或不存在",
    )
    if binding.get("connection_id") != connection_id:
        raise ProductError("SCOPE_BINDING_NOT_FOUND", "资源范围不属于该连接或不存在", 404)
    provider = str(connection_item.get("provider", "")).casefold()
    if provider == "langfuse" and binding.get("resource_type") != "langfuse_project":
        raise ProductError("RESOURCE_TYPE_MISMATCH", "Langfuse 只读验证需要 langfuse_project 资源范围", 422)
    if provider == "github" and binding.get("resource_type") != "github_repository":
        raise ProductError("RESOURCE_TYPE_MISMATCH", "GitHub 只读验证需要 github_repository 资源范围", 422)
    if provider not in {"langfuse", "github"}:
        raise ProductError("CONNECTOR_NOT_AVAILABLE", "当前尚无该服务商的只读验证适配器", 422)
    if connection_item.get("status") == "disconnected" or binding.get("status") == "revoked":
        raise ProductError("CONNECTION_REVOKED", "已撤销或断开的连接不能执行验证", 409)
    request_parameters = item.model_dump(mode="json")
    replay = registry.replay_connection_check(
        workspace_id,
        application_id,
        connection_id,
        binding_id,
        expected_connection_revision=item.expected_connection_revision,
        expected_binding_revision=item.expected_binding_revision,
        idempotency_key=idempotency_key,
        request_parameters=request_parameters,
    )
    if replay is not None:
        return replay
    if connection_item["revision"] != item.expected_connection_revision:
        raise ProductError("STALE_CONNECTION_REVISION", "连接已变化，请刷新后重新验证", 409)
    if binding["revision"] != item.expected_binding_revision:
        raise ProductError("STALE_SCOPE_BINDING_REVISION", "资源范围已变化，请刷新后重新验证", 409)
    try:
        if provider == "langfuse":
            credentials = _broker(request).langfuse(connection_item.get("credential_ref"))
            check = await check_langfuse_binding(connection_item, binding, credentials)
        else:
            credentials = _broker(request).github(connection_item.get("credential_ref"))
            check = await check_github_binding(connection_item, binding, credentials)
    except CredentialUnavailable as exc:
        raise ProductError(exc.code, exc.message, 424) from exc
    except ValueError as exc:
        raise ProductError("UNSAFE_CONNECTION_ENDPOINT", str(exc), 422) from exc
    except (httpx.TimeoutException, httpx.RequestError) as exc:
        detail = ("Langfuse 读取超时或服务暂不可达" if provider == "langfuse"
                  else "GitHub 读取超时或服务暂不可达")
        check = {
            "checked_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "outcome": "unavailable", "connection_status": "degraded", "binding_status": "pending",
            "http_status": None, "observed_count": 0, "watermark": None,
            "coverage": {"from_start_time": None, "to_start_time": None, "complete": False},
            "scope_confirmed": False, "detail": detail,
            "error_type": type(exc).__name__,
        }
    return registry.record_connection_check(
        workspace_id,
        application_id,
        connection_id,
        binding_id,
        expected_connection_revision=item.expected_connection_revision,
        expected_binding_revision=item.expected_binding_revision,
        idempotency_key=idempotency_key,
        request_parameters=request_parameters,
        check=check,
    )


@router.get("/workspaces/{workspace_id}/applications/{application_id}/signals")
async def signals(
    workspace_id: str,
    application_id: str,
    request: Request,
    environment_id: Annotated[str, Query(min_length=1, max_length=200)],
    signal_status: Annotated[str | None, Query(alias="status", pattern="^(new|clustered|suppressed|dismissed)$")] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
):
    return {"items": _registry(request).list_signals(
        workspace_id, application_id, environment_id, status=signal_status, limit=limit,
    )}


@router.get("/workspaces/{workspace_id}/applications/{application_id}/signal-import-checkpoints")
async def signal_import_checkpoints(
    workspace_id: str,
    application_id: str,
    request: Request,
    environment_id: Annotated[str, Query(min_length=1, max_length=200)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
):
    return {"items": _registry(request).list_signal_import_checkpoints(
        workspace_id, application_id, environment_id, limit=limit,
    )}


@router.get("/workspaces/{workspace_id}/applications/{application_id}/signal-monitors")
async def signal_sync_monitors(
    workspace_id: str,
    application_id: str,
    request: Request,
    environment_id: Annotated[str, Query(min_length=1, max_length=200)],
):
    return {"items": _registry(request).list_signal_sync_monitors(
        workspace_id, application_id, environment_id,
    )}


@router.get("/workspaces/{workspace_id}/applications/{application_id}/signal-monitors/{monitor_id}/runs")
async def signal_sync_monitor_runs(
    workspace_id: str,
    application_id: str,
    monitor_id: str,
    request: Request,
    environment_id: Annotated[str, Query(min_length=1, max_length=200)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
):
    monitor = next((item for item in _registry(request).list_signal_sync_monitors(
        workspace_id, application_id, environment_id,
    ) if item["id"] == monitor_id), None)
    if monitor is None:
        raise ProductError("SIGNAL_MONITOR_NOT_FOUND", "信号监护不属于该应用和环境", 404)
    return {"items": _registry(request).list_signal_sync_runs(
        workspace_id, application_id, monitor_id, limit=limit,
    )}


@router.put("/workspaces/{workspace_id}/applications/{application_id}/scope-bindings/{binding_id}/signal-monitor")
async def configure_signal_sync_monitor(
    workspace_id: str,
    application_id: str,
    binding_id: str,
    item: SignalSyncMonitorConfigure,
    request: Request,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=200)],
):
    return _registry(request).configure_signal_sync_monitor(
        workspace_id, application_id, item.environment_id, binding_id,
        enabled=item.enabled, interval_seconds=item.interval_seconds,
        expected_revision=item.expected_revision, idempotency_key=idempotency_key,
    )


@router.post(
    "/workspaces/{workspace_id}/applications/{application_id}/connections/{connection_id}"
    "/scope-bindings/{binding_id}/signals/import",
    status_code=status.HTTP_201_CREATED,
)
async def import_langfuse_signals(
    workspace_id: str,
    application_id: str,
    connection_id: str,
    binding_id: str,
    item: LangfuseSignalImport,
    request: Request,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=200)],
):
    registry = _registry(request)
    connection = _require(registry.get_connection(workspace_id, application_id, connection_id),
                          "CONNECTION_NOT_FOUND", "连接不属于该应用或不存在")
    binding = _require(registry.get_scope_binding(workspace_id, application_id,
        item.environment_id, binding_id), "SCOPE_BINDING_NOT_FOUND", "资源范围不属于该应用和环境或不存在")
    if binding.get("connection_id") != connection_id:
        raise ProductError("SCOPE_BINDING_NOT_FOUND", "资源范围不属于该连接或不存在", 404)
    if connection.get("provider", "").casefold() != "langfuse" or binding.get("resource_type") != "langfuse_project":
        raise ProductError("CONNECTOR_NOT_AVAILABLE", "当前只支持从 Langfuse 项目导入观察信号", 422)
    latest_check = binding.get("latest_check") if isinstance(binding.get("latest_check"), dict) else {}
    if (connection.get("status") != "connected" or binding.get("status") != "confirmed"
            or latest_check.get("scope_confirmed") is not True):
        raise ProductError("SOURCE_SCOPE_UNVERIFIED", "请先完成该 Langfuse 项目的只读认证与范围验证", 409)
    idempotency_payload = {
        "workspace_id": workspace_id,
        "application_id": application_id,
        "environment_id": item.environment_id,
        "connection_id": connection_id,
        "binding_id": binding_id,
        "request": item.model_dump(mode="json"),
    }
    replay = registry.replay_signal_ingest(
        workspace_id, application_id, idempotency_key=idempotency_key,
        request_payload=idempotency_payload, command_name="signal.import",
    )
    if replay is not None:
        return replay
    current_checkpoint = registry.get_signal_import_checkpoint(
        workspace_id, application_id, item.environment_id, binding_id,
        item.from_start_time, item.to_start_time,
    )
    checkpoint_cursor = current_checkpoint.get("next_cursor") if current_checkpoint else None
    if item.cursor is not None and (current_checkpoint is None or item.cursor != checkpoint_cursor):
        raise ProductError("STALE_SIGNAL_IMPORT_CURSOR", "继续读取游标已过期，请刷新读取状态", 409)
    current_cursor = item.cursor if item.cursor is not None else checkpoint_cursor
    expected_checkpoint_revision = current_checkpoint["revision"] if current_checkpoint else 0
    try:
        credentials = _broker(request).langfuse(connection.get("credential_ref"))
    except CredentialUnavailable as exc:
        raise ProductError(exc.code, exc.message, 424) from exc
    seen_cursors = {current_cursor} if current_cursor else set()
    raw_rows: list[dict] = []
    pages_read = 0
    continuation_cursor: str | None = current_cursor
    coverage_error: str | None = None
    complete = False
    provider_failure: dict | None = None

    while pages_read < item.max_pages and len(raw_rows) < 1000:
        page_limit = min(item.page_size, 1000 - len(raw_rows))
        try:
            page = await read_langfuse_observations(
                connection, binding, credentials,
                from_start_time=item.from_start_time,
                to_start_time=item.to_start_time,
                cursor=current_cursor,
                limit=page_limit,
            )
        except (httpx.TimeoutException, httpx.RequestError):
            provider_failure = {"http_status": None, "availability": "unavailable"}
            coverage_error = "provider_unavailable"
            continuation_cursor = current_cursor
            break
        availability = page.get("availability")
        if availability == "scope_mismatch":
            raise ProductError("SOURCE_SCOPE_MISMATCH", "Langfuse 返回或凭据项目与登记范围不一致", 403)
        if page.get("http_status") != 200:
            provider_failure = page
            if pages_read == 0:
                if availability == "permission_denied":
                    raise ProductError("SOURCE_PERMISSION_DENIED", "Langfuse 凭据无权读取该项目", 403)
                if availability == "rate_limited":
                    raise ProductError("SOURCE_RATE_LIMITED", "Langfuse 暂时限制了读取请求", 429)
                raise ProductError("SOURCE_UNAVAILABLE", "Langfuse 未返回可读取的观察记录", 503)
            coverage_error = str(availability or "provider_unavailable")
            continuation_cursor = current_cursor
            break

        rows = page.get("rows", []) if isinstance(page.get("rows"), list) else []
        raw_rows.extend(row for row in rows if isinstance(row, dict))
        pages_read += 1
        page_coverage = page.get("coverage") if isinstance(page.get("coverage"), dict) else {}
        next_cursor = page_coverage.get("next_cursor")
        if not next_cursor:
            complete = page_coverage.get("complete") is True
            continuation_cursor = None
            break
        next_cursor = str(next_cursor)
        if next_cursor in seen_cursors:
            coverage_error = "cursor_repeated"
            continuation_cursor = None
            break
        seen_cursors.add(next_cursor)
        current_cursor = next_cursor
        continuation_cursor = next_cursor

    observed_at = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    signal_items: list[dict] = []
    invalid_rows = 0
    for row in raw_rows:
        source_id = row.get("id")
        occurred_at = row.get("timestamp")
        if not isinstance(source_id, str) or not source_id or not isinstance(occurred_at, str):
            invalid_rows += 1
            continue
        try:
            source_time = datetime.fromisoformat(
                occurred_at[:-1] + "+00:00" if occurred_at.endswith("Z") else occurred_at
            )
            if source_time.tzinfo is None:
                raise ValueError("timezone required")
        except ValueError:
            invalid_rows += 1
            continue
        source_version = occurred_at
        digest = hashlib.sha256(f"{binding_id}\0{source_id}\0{source_version}".encode()).hexdigest()
        kind = re.sub(r"[^A-Za-z0-9_.:-]", "", str(row.get("type") or "observation"))[:80] or "observation"
        signal_items.append({
            "source_id": source_id,
            "source_version": source_version,
            "deduplication_key": f"langfuse-observation:{digest}",
            "signal_type": "langfuse_observation",
            "source_kind": "poll",
            "occurred_at": occurred_at,
            "observed_at": observed_at,
            "summary": f"Langfuse {kind} observation · 待人工判断业务含义",
        })

    coverage = {
        "from_start_time": item.from_start_time,
        "to_start_time": item.to_start_time,
        "complete": complete and invalid_rows == 0,
        "next_cursor": continuation_cursor,
        "continuation_required": bool(continuation_cursor),
        "pages_read": pages_read,
        "rows_seen": len(raw_rows),
        "invalid_rows": invalid_rows,
        "error": coverage_error,
        "provider_availability": provider_failure.get("availability") if provider_failure else None,
    }
    return registry.ingest_signals(
        workspace_id, application_id, item.environment_id, binding_id, signal_items,
        idempotency_key=idempotency_key, coverage=coverage,
        command_name="signal.import", idempotency_payload=idempotency_payload,
        checkpoint={
            "from_start_time": item.from_start_time,
            "to_start_time": item.to_start_time,
            "expected_revision": expected_checkpoint_revision,
            "next_cursor": continuation_cursor,
            "complete": coverage["complete"],
        },
    )


@router.post(
    "/workspaces/{workspace_id}/applications/{application_id}/connections/{connection_id}"
    "/scope-bindings/{binding_id}/signals/import/github",
    status_code=status.HTTP_201_CREATED,
)
async def import_github_issue_signals(
    workspace_id: str,
    application_id: str,
    connection_id: str,
    binding_id: str,
    item: GitHubSignalImport,
    request: Request,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=200)],
):
    registry = _registry(request)
    connection = _require(registry.get_connection(workspace_id, application_id, connection_id),
                          "CONNECTION_NOT_FOUND", "连接不属于该应用或不存在")
    binding = _require(registry.get_scope_binding(workspace_id, application_id,
        item.environment_id, binding_id), "SCOPE_BINDING_NOT_FOUND", "资源范围不属于该应用和环境或不存在")
    if binding.get("connection_id") != connection_id:
        raise ProductError("SCOPE_BINDING_NOT_FOUND", "资源范围不属于该连接或不存在", 404)
    if connection.get("provider", "").casefold() != "github" or binding.get("resource_type") != "github_repository":
        raise ProductError("CONNECTOR_NOT_AVAILABLE", "当前只支持从 GitHub 仓库导入 issue 和 pull request 信号", 422)
    latest_check = binding.get("latest_check") if isinstance(binding.get("latest_check"), dict) else {}
    if (connection.get("status") != "connected" or binding.get("status") != "confirmed"
            or latest_check.get("scope_confirmed") is not True):
        raise ProductError("SOURCE_SCOPE_UNVERIFIED", "请先完成该 GitHub 仓库的只读认证与范围验证", 409)

    request_item = item.model_dump(mode="json")
    payload = {
        "workspace_id": workspace_id,
        "application_id": application_id,
        "environment_id": item.environment_id,
        "connection_id": connection_id,
        "binding_id": binding_id,
        "request": request_item,
    }
    replay = registry.replay_signal_ingest(
        workspace_id, application_id, idempotency_key=idempotency_key,
        request_payload=payload, command_name="signal.import.github",
    )
    if replay is not None:
        return replay

    start = datetime.fromisoformat(item.from_updated_at[:-1] + "+00:00"
        if item.from_updated_at.endswith("Z") else item.from_updated_at)
    end = datetime.fromisoformat(item.to_updated_at[:-1] + "+00:00"
        if item.to_updated_at.endswith("Z") else item.to_updated_at)
    checkpoint = registry.get_signal_import_checkpoint(
        workspace_id, application_id, item.environment_id, binding_id,
        item.from_updated_at, item.to_updated_at,
    )
    checkpoint_cursor = checkpoint.get("next_cursor") if checkpoint else None
    if item.cursor is not None and (checkpoint is None or item.cursor != checkpoint_cursor):
        raise ProductError("STALE_SIGNAL_IMPORT_CURSOR", "继续读取游标已过期，请刷新读取状态", 409)
    current_cursor = item.cursor if item.cursor is not None else checkpoint_cursor
    try:
        page_number = int(current_cursor.removeprefix("page:")) if current_cursor else 1
    except (AttributeError, ValueError):
        raise ProductError("INVALID_SIGNAL_IMPORT_CURSOR", "GitHub 读取游标无效，请重新开始该窗口", 422) from None
    expected_checkpoint_revision = int(checkpoint["revision"]) if checkpoint else 0
    try:
        credentials = _broker(request).github(connection.get("credential_ref"))
    except CredentialUnavailable as exc:
        raise ProductError(exc.code, exc.message, 424) from exc

    raw_rows: list[dict] = []
    pages_read = 0
    has_next = False
    complete = False
    coverage_error: str | None = None
    last_updated_at = (checkpoint.get("coverage") or {}).get("last_updated_at") if checkpoint else None
    invalid_rows = 0
    while pages_read < item.max_pages and len(raw_rows) < 1000:
        try:
            page = await read_github_issues(
                connection, binding, credentials, updated_since=item.from_updated_at,
                page=page_number, per_page=item.page_size,
            )
        except (httpx.TimeoutException, httpx.RequestError):
            coverage_error = "provider_unavailable"
            has_next = True
            break
        availability = page.get("availability")
        if page.get("http_status") != 200:
            if pages_read == 0:
                if availability in {"permission_denied", "not_found_or_denied"}:
                    raise ProductError("SOURCE_PERMISSION_DENIED", "GitHub 凭据无权读取该仓库 issue", 403)
                if availability == "rate_limited":
                    raise ProductError("SOURCE_RATE_LIMITED", "GitHub 暂时限制了读取请求", 429)
                raise ProductError("SOURCE_UNAVAILABLE", "GitHub 未返回可读取的 issue 页面", 503)
            coverage_error = str(availability or "provider_unavailable")
            has_next = True
            break

        rows = page.get("rows", []) if isinstance(page.get("rows"), list) else []
        pages_read += 1
        has_next = page.get("has_next") is True
        previous = last_updated_at
        stop_at_window_end = False
        for row in rows:
            updated_at = row.get("updated_at")
            number = row.get("number")
            if not isinstance(updated_at, str) or not isinstance(number, int) or number < 1:
                invalid_rows += 1
                continue
            try:
                observed_time = datetime.fromisoformat(updated_at[:-1] + "+00:00"
                    if updated_at.endswith("Z") else updated_at)
                if observed_time.tzinfo is None:
                    raise ValueError("timezone required")
            except ValueError:
                invalid_rows += 1
                continue
            if previous is not None:
                previous_time = datetime.fromisoformat(previous[:-1] + "+00:00"
                    if previous.endswith("Z") else previous)
                if observed_time < previous_time:
                    coverage_error = "source_order_changed"
                    has_next = True
                    break
            previous = updated_at
            last_updated_at = updated_at
            if observed_time > end:
                stop_at_window_end = True
                has_next = False
                break
            if observed_time < start:
                continue
            source_id = str(number)
            source_version = updated_at
            digest = hashlib.sha256(f"{binding_id}\0{source_id}\0{source_version}".encode()).hexdigest()
            is_pull_request = isinstance(row.get("pull_request"), dict)
            kind = "pull request" if is_pull_request else "issue"
            raw_rows.append({
                "source_id": source_id,
                "source_version": source_version,
                "deduplication_key": f"github-{kind.replace(' ', '-')}:" + digest,
                "signal_type": f"github_{kind.replace(' ', '_')}",
                "source_kind": "poll",
                "occurred_at": updated_at,
                "observed_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                "summary": f"GitHub {kind} updated · 待人工判断业务含义",
            })
        if coverage_error == "source_order_changed" or stop_at_window_end:
            complete = coverage_error is None
            break
        if not has_next:
            complete = True
            break
        page_number += 1

    next_cursor = f"page:{page_number}" if has_next and coverage_error != "source_order_changed" else None
    coverage = {
        "from_start_time": item.from_updated_at,
        "to_start_time": item.to_updated_at,
        "complete": complete and invalid_rows == 0,
        "next_cursor": next_cursor,
        "continuation_required": bool(next_cursor),
        "pages_read": pages_read,
        "rows_seen": len(raw_rows) + invalid_rows,
        "invalid_rows": invalid_rows,
        "error": coverage_error,
        "consistency": "github_updated_at_ascending_best_effort",
        "last_updated_at": last_updated_at,
    }
    return registry.ingest_signals(
        workspace_id, application_id, item.environment_id, binding_id, raw_rows,
        idempotency_key=idempotency_key, coverage=coverage,
        command_name="signal.import.github", idempotency_payload=payload,
        checkpoint={
            "from_start_time": item.from_updated_at,
            "to_start_time": item.to_updated_at,
            "expected_revision": expected_checkpoint_revision,
            "next_cursor": next_cursor,
            "complete": coverage["complete"],
        },
    )


@router.get(
    "/workspaces/{workspace_id}/applications/{application_id}/scope-bindings/{binding_id}/signals/import-state",
)
async def langfuse_signal_import_state(
    workspace_id: str,
    application_id: str,
    binding_id: str,
    request: Request,
    environment_id: Annotated[str, Query(min_length=1, max_length=200)],
    from_start_time: Annotated[str, Query(min_length=1, max_length=40)],
    to_start_time: Annotated[str, Query(min_length=1, max_length=40)],
):
    binding = _require(_registry(request).get_scope_binding(
        workspace_id, application_id, environment_id, binding_id,
    ), "SCOPE_BINDING_NOT_FOUND", "资源范围不属于该应用和环境或不存在")
    if binding.get("resource_type") not in {"langfuse_project", "github_repository"}:
        raise ProductError("CONNECTOR_NOT_AVAILABLE", "当前仅支持 Langfuse 与 GitHub 信号导入状态", 422)
    checkpoint = _registry(request).get_signal_import_checkpoint(
        workspace_id, application_id, environment_id, binding_id, from_start_time, to_start_time,
    )
    return {"checkpoint": checkpoint}


@router.post("/workspaces/{workspace_id}/applications/{application_id}/signals", status_code=status.HTTP_201_CREATED)
async def ingest_signals(
    workspace_id: str,
    application_id: str,
    item: SignalIngest,
    request: Request,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=200)],
):
    return _registry(request).ingest_signals(
        workspace_id, application_id, item.environment_id, item.source_binding_id,
        [signal.model_dump(mode="json") for signal in item.items],
        idempotency_key=idempotency_key,
    )


@router.get("/workspaces/{workspace_id}/applications/{application_id}/incidents")
async def incidents(
    workspace_id: str,
    application_id: str,
    request: Request,
    environment_id: Annotated[str, Query(min_length=1, max_length=200)],
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
):
    return {"items": _registry(request).list_incidents(
        workspace_id, application_id, environment_id, limit=limit,
    )}


@router.post("/workspaces/{workspace_id}/applications/{application_id}/incidents", status_code=status.HTTP_201_CREATED)
async def create_incident(
    workspace_id: str,
    application_id: str,
    item: IncidentCreate,
    request: Request,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=200)],
):
    return _registry(request).create_incident(
        workspace_id, application_id, item.environment_id, item.title, item.severity,
        [signal.model_dump(mode="json") for signal in item.signals],
        idempotency_key=idempotency_key,
    )


@router.get("/workspaces/{workspace_id}/applications/{application_id}/incidents/{incident_id}")
async def incident_detail(
    workspace_id: str,
    application_id: str,
    incident_id: str,
    request: Request,
    environment_id: Annotated[str, Query(min_length=1, max_length=200)],
):
    return _require(_registry(request).get_incident(
        workspace_id, application_id, environment_id, incident_id,
    ), "INCIDENT_NOT_FOUND", "事故不属于该应用和环境或不存在")


@router.post(
    "/workspaces/{workspace_id}/applications/{application_id}/incidents/{incident_id}/transitions",
)
async def transition_incident(
    workspace_id: str,
    application_id: str,
    incident_id: str,
    item: IncidentTransition,
    request: Request,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=200)],
):
    return _registry(request).transition_incident(
        workspace_id, application_id, item.environment_id, incident_id,
        IncidentState(item.target_state), item.reason, expected_revision=item.expected_revision,
        idempotency_key=idempotency_key,
    )


@router.post(
    "/workspaces/{workspace_id}/applications/{application_id}/incidents/{incident_id}/signals",
)
async def attach_signals_to_incident(
    workspace_id: str,
    application_id: str,
    incident_id: str,
    item: IncidentSignalAttachment,
    request: Request,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=200)],
):
    return _registry(request).attach_signals_to_incident(
        workspace_id, application_id, item.environment_id, incident_id,
        [signal.model_dump(mode="json") for signal in item.signals], item.reason,
        expected_incident_revision=item.expected_incident_revision,
        idempotency_key=idempotency_key,
    )


@router.post(
    "/workspaces/{workspace_id}/applications/{application_id}/incidents/{incident_id}/merge",
)
async def merge_incidents(
    workspace_id: str,
    application_id: str,
    incident_id: str,
    item: IncidentMerge,
    request: Request,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=200)],
):
    return _registry(request).merge_incidents(
        workspace_id, application_id, item.environment_id, incident_id, item.target_incident_id,
        item.reason, expected_incident_revision=item.expected_incident_revision,
        expected_target_revision=item.expected_target_revision, idempotency_key=idempotency_key,
    )


@router.post(
    "/workspaces/{workspace_id}/applications/{application_id}/incidents/{incident_id}/assignment",
)
async def update_incident_assignment(
    workspace_id: str,
    application_id: str,
    incident_id: str,
    item: IncidentAssignment,
    request: Request,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=200)],
):
    return _registry(request).set_incident_claim(
        workspace_id, application_id, item.environment_id, incident_id,
        item.action == "claim", item.reason,
        expected_revision=item.expected_revision, idempotency_key=idempotency_key,
    )


@router.post(
    "/workspaces/{workspace_id}/applications/{application_id}/incidents/{incident_id}/severity",
)
async def update_incident_severity(
    workspace_id: str,
    application_id: str,
    incident_id: str,
    item: IncidentSeverityUpdate,
    request: Request,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=200)],
):
    return _registry(request).update_incident_severity(
        workspace_id, application_id, item.environment_id, incident_id, item.severity, item.reason,
        expected_revision=item.expected_revision, idempotency_key=idempotency_key,
    )


@router.post(
    "/workspaces/{workspace_id}/applications/{application_id}/incidents/{incident_id}/split",
)
async def split_incident(
    workspace_id: str,
    application_id: str,
    incident_id: str,
    item: IncidentSplit,
    request: Request,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=200)],
):
    return _registry(request).split_incident(
        workspace_id, application_id, item.environment_id, incident_id, item.title, item.severity,
        [signal.model_dump(mode="json") for signal in item.signals], item.reason,
        expected_incident_revision=item.expected_incident_revision, idempotency_key=idempotency_key,
    )


@router.post(
    "/workspaces/{workspace_id}/applications/{application_id}/incidents/{incident_id}/investigation-runs/manual",
    status_code=status.HTTP_201_CREATED,
)
async def create_manual_investigation_run(
    workspace_id: str,
    application_id: str,
    incident_id: str,
    item: ManualInvestigationRunCreate,
    request: Request,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=200)],
):
    return _registry(request).create_manual_investigation_run(
        workspace_id, application_id, item.environment_id, incident_id,
        expected_incident_revision=item.expected_incident_revision, idempotency_key=idempotency_key,
    )


@router.post(
    "/workspaces/{workspace_id}/applications/{application_id}/incidents/{incident_id}/investigation-runs",
    status_code=status.HTTP_201_CREATED,
)
async def create_investigation_run(
    workspace_id: str,
    application_id: str,
    incident_id: str,
    item: InvestigationRunCreate,
    request: Request,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=200)],
):
    return _registry(request).create_investigation_run(
        workspace_id, application_id, item.environment_id, incident_id,
        expected_incident_revision=item.expected_incident_revision,
        idempotency_key=idempotency_key, execution_mode=item.mode,
    )


@router.get("/workspaces/{workspace_id}/applications/{application_id}/investigation-runs")
async def investigation_runs(
    workspace_id: str,
    application_id: str,
    request: Request,
    environment_id: Annotated[str, Query(min_length=1, max_length=200)],
    incident_id: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
):
    return {"items": _registry(request).list_investigation_runs(
        workspace_id, application_id, environment_id, incident_id=incident_id, limit=limit,
    )}


@router.get("/workspaces/{workspace_id}/applications/{application_id}/investigation-runs/{run_id}")
async def investigation_run_detail(
    workspace_id: str,
    application_id: str,
    run_id: str,
    request: Request,
    environment_id: Annotated[str, Query(min_length=1, max_length=200)],
):
    return _require(_registry(request).get_investigation_run_detail(
        workspace_id, application_id, environment_id, run_id,
    ), "INVESTIGATION_RUN_NOT_FOUND", "调查运行不属于该应用和环境或不存在")


@router.post("/workspaces/{workspace_id}/applications/{application_id}/investigation-runs/{run_id}/execute")
async def execute_investigation_run(
    workspace_id: str,
    application_id: str,
    run_id: str,
    item: InvestigationRunExecute,
    request: Request,
):
    return await _task_runtime(request).execute(
        workspace_id, application_id, item.environment_id, run_id,
    )


@router.get("/workspaces/{workspace_id}/applications/{application_id}/workers")
async def investigation_workers(
    workspace_id: str,
    application_id: str,
    request: Request,
    environment_id: Annotated[str, Query(min_length=1, max_length=200)],
):
    _require_local_operator(request)
    return {"items": _registry(request).list_investigation_workers(
        workspace_id, application_id, environment_id,
    )}


@router.post("/workspaces/{workspace_id}/applications/{application_id}/workers", status_code=status.HTTP_201_CREATED)
async def create_investigation_worker(
    workspace_id: str,
    application_id: str,
    item: InvestigationWorkerCreate,
    request: Request,
):
    _require_local_operator(request)
    created = _registry(request).create_investigation_worker(
        workspace_id, application_id, item.environment_id, item.worker_id, item.capabilities,
    )
    return JSONResponse(created, status_code=status.HTTP_201_CREATED,
        headers={"Cache-Control": "no-store"})


@router.post("/workspaces/{workspace_id}/applications/{application_id}/workers/{worker_id}/revoke")
async def revoke_investigation_worker(
    workspace_id: str,
    application_id: str,
    worker_id: str,
    request: Request,
    environment_id: Annotated[str, Query(min_length=1, max_length=200)],
):
    _require_local_operator(request)
    return _registry(request).revoke_investigation_worker(
        workspace_id, application_id, environment_id, worker_id,
    )


@router.post(
    "/workspaces/{workspace_id}/applications/{application_id}/investigation-runs/{run_id}/tasks/{task_id}/claim",
)
async def claim_investigation_task(
    workspace_id: str,
    application_id: str,
    run_id: str,
    task_id: str,
    item: InvestigationTaskClaim,
    request: Request,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=200)],
):
    identity = _authenticate_task_worker(
        request, workspace_id, application_id, item.environment_id, item.worker_id,
    )
    granted = sorted(set(identity["capabilities"]).intersection(item.capabilities))
    return _registry(request).claim_investigation_task(
        workspace_id, application_id, item.environment_id, run_id, task_id,
        identity["worker_id"], granted, expected_revision=item.expected_revision,
        lease_seconds=item.lease_seconds, idempotency_key=idempotency_key,
    )


@router.post(
    "/workspaces/{workspace_id}/applications/{application_id}/investigation-runs/{run_id}/tasks/{task_id}/renew",
)
async def renew_investigation_task_lease(
    workspace_id: str,
    application_id: str,
    run_id: str,
    task_id: str,
    item: InvestigationTaskRenew,
    request: Request,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=200)],
):
    identity = _authenticate_task_worker(
        request, workspace_id, application_id, item.environment_id, item.worker_id,
    )
    return _registry(request).renew_investigation_task_lease(
        workspace_id, application_id, item.environment_id, run_id, task_id,
        identity["worker_id"], item.epoch, expected_revision=item.expected_revision,
        lease_seconds=item.lease_seconds, idempotency_key=idempotency_key,
    )


@router.get(
    "/workspaces/{workspace_id}/applications/{application_id}/investigation-runs/{run_id}/tasks/{task_id}/context",
)
async def investigation_task_context(
    workspace_id: str,
    application_id: str,
    run_id: str,
    task_id: str,
    request: Request,
    environment_id: Annotated[str, Query(min_length=1, max_length=200)],
    worker_id: Annotated[str, Query(min_length=1, max_length=200)],
    epoch: Annotated[int, Query(ge=1)],
):
    identity = _authenticate_task_worker(
        request, workspace_id, application_id, environment_id, worker_id,
    )
    return _require(_registry(request).get_investigation_task_context(
        workspace_id, application_id, environment_id, run_id, task_id, identity["worker_id"], epoch,
    ), "INVESTIGATION_TASK_NOT_FOUND", "任务不属于该调查或不存在")


@router.post(
    "/workspaces/{workspace_id}/applications/{application_id}/investigation-runs/{run_id}/tasks/{task_id}/finish",
)
async def finish_investigation_task(
    workspace_id: str,
    application_id: str,
    run_id: str,
    task_id: str,
    item: InvestigationTaskFinish,
    request: Request,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=200)],
):
    identity = _authenticate_task_worker(
        request, workspace_id, application_id, item.environment_id, item.worker_id,
    )
    return _registry(request).finish_investigation_task(
        workspace_id, application_id, item.environment_id, run_id, task_id,
        identity["worker_id"], item.epoch,
        item.result.model_dump(mode="json") if item.result is not None else None,
        item.failure_reason, expected_revision=item.expected_revision,
        idempotency_key=idempotency_key,
    )


@router.post(
    "/workspaces/{workspace_id}/applications/{application_id}/investigation-runs/{run_id}/tasks/{task_id}/retry",
)
async def retry_investigation_task(
    workspace_id: str,
    application_id: str,
    run_id: str,
    task_id: str,
    item: InvestigationTaskRetry,
    request: Request,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=200)],
):
    return _registry(request).retry_investigation_task(
        workspace_id, application_id, item.environment_id, run_id, task_id, item.reason,
        expected_revision=item.expected_revision, idempotency_key=idempotency_key,
    )


@router.post(
    "/workspaces/{workspace_id}/applications/{application_id}/investigation-runs/{run_id}/hypotheses",
    status_code=status.HTTP_201_CREATED,
)
async def create_hypothesis(
    workspace_id: str,
    application_id: str,
    run_id: str,
    item: HypothesisCreate,
    request: Request,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=200)],
):
    return _registry(request).create_hypothesis(
        workspace_id, application_id, item.environment_id, run_id, item.statement, item.confidence,
        item.support_evidence_ids, item.counterevidence_ids,
        expected_run_revision=item.expected_run_revision, idempotency_key=idempotency_key,
    )


@router.post(
    "/workspaces/{workspace_id}/applications/{application_id}/investigation-runs/{run_id}"
    "/hypotheses/{hypothesis_id}/decision",
)
async def decide_hypothesis(
    workspace_id: str,
    application_id: str,
    run_id: str,
    hypothesis_id: str,
    item: HypothesisDecision,
    request: Request,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=200)],
):
    return _registry(request).decide_hypothesis(
        workspace_id, application_id, item.environment_id, run_id, hypothesis_id,
        HypothesisState(item.target_state), item.confidence,
        item.support_evidence_ids, item.counterevidence_ids, item.reason,
        expected_revision=item.expected_revision, idempotency_key=idempotency_key,
    )


@router.post("/workspaces/{workspace_id}/applications/{application_id}/investigation-runs/{run_id}/finish")
async def finish_manual_investigation_run(
    workspace_id: str,
    application_id: str,
    run_id: str,
    item: ManualInvestigationFinish,
    request: Request,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=200)],
):
    return _registry(request).finish_manual_investigation_run(
        workspace_id, application_id, item.environment_id, run_id, item.result_type, item.reason,
        expected_revision=item.expected_revision, idempotency_key=idempotency_key,
    )


@router.get("/workspaces/{workspace_id}/events")
async def workspace_event_page(
    workspace_id: str,
    request: Request,
    after: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
    application_id: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
):
    items = _registry(request).workspace_events(
        workspace_id, after=after, limit=limit, application_id=application_id,
    )
    next_after = int(items[-1]["sequence"]) if items else after
    return {"items": items, "next_after": next_after}
