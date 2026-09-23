from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, Request
from pydantic import Field

from s9.product.v1_contracts import V1Model
from s9.product.v1_api import _require_local_operator

router = APIRouter(prefix="/api/v1/demo", dependencies=[Depends(_require_local_operator)])


class BusinessTask(V1Model):
    message: str = Field(min_length=1, max_length=4000)


class Analyze(V1Model):
    mode: Literal["single", "swarm"] = "swarm"
    evidence_ids: list[str] | None = Field(default=None, max_length=100)


class Review(V1Model):
    proposal_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    decision: Literal["approved", "rejected"]
    reason: str = Field(min_length=1, max_length=1000)


@router.get("/status")
async def status(request: Request):
    service = request.app.state.demo
    return {"application": "客服助手", "environment": "本地演示业务数据", "native": service.native.status(),
            "tasks": [{k: j.get(k) for k in ("id", "message", "state", "created_at")} for j in reversed(service.jobs())]}


@router.post("/tasks", status_code=202)
async def start(item: BusinessTask, request: Request,
                idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=200)]):
    return await request.app.state.demo.start(item.message.strip(), idempotency_key)


@router.get("/tasks/{task_id}")
async def task(task_id: str, request: Request):
    return request.app.state.demo.detail(task_id)


@router.post("/tasks/{task_id}/investigate", status_code=202)
async def investigate(task_id: str, item: Analyze, request: Request):
    return await request.app.state.demo.analyze(task_id, item.mode, item.evidence_ids)


@router.post("/tasks/{task_id}/revise", status_code=202)
async def revise(task_id: str, request: Request):
    return await request.app.state.demo.revise(task_id)


@router.post("/tasks/{task_id}/members/{role}/pause")
async def pause_member(task_id: str, role: Literal['investigator', 'reviewer'], request: Request):
    service = request.app.state.demo
    job = service.get(task_id)
    from s9.product.registry import ProductError
    if not job.get('runs', {}).get('swarm') or job['analysis_state'] != 'running':
        raise ProductError('NO_ACTIVE_INVESTIGATION', '请在协作调查期间演练接力', 409)
    await service.native.pause(service.wid, job['runs']['swarm'], role)
    return service.detail(task_id)


@router.post("/tasks/{task_id}/review")
async def review(task_id: str, item: Review, request: Request):
    return request.app.state.demo.review(task_id, item.proposal_sha256, item.decision, item.reason)
