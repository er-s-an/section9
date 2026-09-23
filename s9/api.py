from __future__ import annotations

import asyncio
import gzip
import io
import json
import time
import uuid
import uvicorn
from contextlib import asynccontextmanager, nullcontext
from typing import Literal

from fastapi import Depends, FastAPI, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import Field

from s9 import config
from s9.contracts import (AgentStatusRequest, ChatRequest, ClaimRequest, ExecuteRequest, Injection,
                          MessageRequest, ModelRequest, PlanRequest, StrictModel, VerifyRequest)
from s9.core import Core
from s9.product.lifecycle import ProductBackup
from s9.product.registry import ProductError
from s9.product.v1_api import (
    product_error_response,
    router as product_v1_router,
    validation_error_response,
    v1_error_response,
)
from s9.store import ACTIVE, Rejected, Store, now


@asynccontextmanager
async def lifespan(app):
    from s9.model import ModelClient
    from s9.model_scheduler import ProviderGateway
    from s9.pairs.coordinator import PairCoordinator
    from s9.product.signal_monitor import SignalSyncScheduler
    from s9.product.task_runtime import InvestigationTaskRuntime
    app.state.core = Core()
    app.state.gateway = ProviderGateway()
    await app.state.core.model.close()
    app.state.core.model = ModelClient(app.state.core.store, app.state.core.telemetry, gateway=app.state.gateway)
    app.state.core.victim.model = app.state.core.model
    app.state.product_task_runtime = InvestigationTaskRuntime(
        app.state.core.product.registry, app.state.core.model.client,
    )
    from s9.product.demo import DemoWorkflow
    app.state.demo = DemoWorkflow(app.state.core.product, app.state.core.model.client)
    app.state.pairs = PairCoordinator(config.DATA / 'showcase', app.state.gateway, app.state.core.telemetry, app.state.core.identity)
    await app.state.pairs.boot()
    # The same authority, a separate restricted ingress. No second database,
    # supervisor, model gateway or application lifespan is started here.
    agent_server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=config.PORT + 2,
                                                lifespan="off", access_log=False, log_level="warning"))
    # Only the outer server owns OS signal handlers. The nested ingress shares
    # its lifecycle and must not replace SIGTERM/SIGINT handling.
    agent_server.capture_signals = nullcontext
    agent_task = asyncio.create_task(agent_server.serve())
    await app.state.core.start()
    app.state.signal_sync_scheduler = SignalSyncScheduler(app.state.core.product.registry)
    signal_sync_task = asyncio.create_task(app.state.signal_sync_scheduler.run_forever())
    yield
    await app.state.demo.close()
    app.state.signal_sync_scheduler.stop()
    signal_sync_task.cancel()
    try:
        await signal_sync_task
    except asyncio.CancelledError:
        pass
    await app.state.pairs.close()
    await app.state.core.stop()
    await app.state.gateway.close()
    agent_server.should_exit = True
    await agent_task


app = FastAPI(title="Section9 local laboratory", lifespan=lifespan)
app.include_router(product_v1_router)
from s9.product.demo_api import router as demo_router
app.include_router(demo_router)
from s9.pairs.api import router as pair_router  # noqa: E402
app.include_router(pair_router)


def core(request: Request) -> Core:
    primary = request.app.state.core
    if request.url.path.startswith('/agent/'):
        header = request.headers.get('authorization', '')
        if header.startswith('Bearer '):
            token = header[7:]
            try:
                primary.store.authenticate(token)
            except Rejected:
                pairs = getattr(request.app.state, 'pairs', None)
                if pairs:
                    bound, _ = pairs.authenticate(token)
                    request.state.bound_core = bound
                    return bound
    request.state.bound_core = primary
    return primary


@app.exception_handler(Rejected)
async def rejected(request, exc):
    if request.url.path.startswith("/agent/") and hasattr(request.state, "agent_id"):
        getattr(request.state, "bound_core", request.app.state.core).store.emit("agent.request_rejected", {"path": request.url.path, "code": exc.code,
            "summary": exc.message}, producer=request.state.agent_id)
    return JSONResponse({"error": {"code": exc.code, "message": exc.message}}, status_code=exc.status)


@app.exception_handler(ProductError)
async def product_error(request, exc):
    if request.url.path.startswith("/api/v1/"):
        return product_error_response(request, exc)
    return JSONResponse({"error": {"code": exc.code, "message": exc.message}}, status_code=exc.status)


@app.exception_handler(RequestValidationError)
async def request_validation_error(request, exc):
    if request.url.path.startswith("/api/v1/"):
        return validation_error_response(request, exc)
    return await request_validation_exception_handler(request, exc)


@app.middleware("http")
async def boundaries(request: Request, call_next):
    if request.url.path.startswith("/api/v1/"):
        request.state.request_id = uuid.uuid4().hex
    server_port = (request.scope.get("server") or (None, None))[1]
    if server_port == config.PORT + 2 and not request.url.path.startswith("/agent/"):
        return JSONResponse({"error": {"code": "ROLE_FORBIDDEN", "message": "工作进程端口不提供控制台、日志或文件访问"}}, status_code=403)
    if server_port == config.PORT and request.url.path.startswith("/agent/"):
        return JSONResponse({"error": {"code": "WRONG_INGRESS", "message": "Agent 需使用专属执行入口"}}, status_code=403)
    # The console is a trusted local operator surface, not a worker tool.
    # A role credential is never accepted on operator data/control routes.
    if request.url.path.startswith("/api/") and "telemetry" not in request.url.path:
        if request.headers.get("authorization"):
            if request.url.path.startswith("/api/v1/"):
                return v1_error_response(request, code="ROLE_FORBIDDEN",
                    message="Agent 身份不能访问操作者控制台", status_code=403)
            return JSONResponse({"error": {"code": "ROLE_FORBIDDEN", "message": "Agent 身份不能访问操作者控制台"}}, status_code=403)
        origin = request.headers.get("origin")
        if origin and origin not in {f"http://127.0.0.1:{config.PORT}", f"http://localhost:{config.PORT}"}:
            if request.url.path.startswith("/api/v1/"):
                return v1_error_response(request, code="ORIGIN_FORBIDDEN",
                    message="只接受本地控制台来源", status_code=403)
            return JSONResponse({"error": {"code": "ORIGIN_FORBIDDEN", "message": "只接受本地控制台来源"}}, status_code=403)
    response = await call_next(request)
    if request.url.path.startswith("/api/v1/"):
        response.headers["X-Request-ID"] = request.state.request_id
    return response


def agent(request: Request, c: Core = Depends(core)):
    header = request.headers.get("authorization", "")
    if not header.startswith("Bearer "):
        raise Rejected("UNAUTHENTICATED", "需要 Agent 身份", 401)
    identity = c.store.authenticate(header[7:])
    request.state.agent_id = identity["id"]
    return identity


class AutonomyRequest(StrictModel):
    level: Literal["L0", "L1", "L2"]


class CommunicationRequest(StrictModel):
    muted: bool


class PlanId(StrictModel):
    plan_id: str


class RunId(StrictModel):
    run_id: str


class Renew(StrictModel):
    task_id: str
    epoch: str


class Complete(Renew):
    result_summary: str = Field(max_length=3000)


class IncidentRequest(StrictModel):
    observation_ids: list[str] = Field(max_length=100)


class AttractRequest(StrictModel):
    enabled: bool


class ProductConnectRequest(StrictModel):
    project_id: str = Field(min_length=1, max_length=200)
    environment_id: str = Field(min_length=1, max_length=200)
    run_tests: bool = True


class ProductScopeRequest(StrictModel):
    project_id: str = Field(min_length=1, max_length=200)
    environment_id: str = Field(min_length=1, max_length=200)


class ProductBusinessProbeRequest(ProductScopeRequest):
    sample: str = Field(default="order_truth", pattern="^(order_truth|refund_guardrail)$")


class ProductIncidentRequest(ProductScopeRequest):
    incident_id: str | None = Field(default=None, max_length=200)


class ProductApprovalRequest(StrictModel):
    project_id: str = Field(min_length=1, max_length=200)
    environment_id: str = Field(min_length=1, max_length=200)
    incident_id: str = Field(min_length=1, max_length=200)


class ProductExecuteRequest(ProductScopeRequest):
    incident_id: str = Field(min_length=1, max_length=200)
    approval_id: str = Field(min_length=1, max_length=200)
    idempotency_key: str = Field(min_length=1, max_length=200)


@app.get("/api/state")
async def state(c: Core = Depends(core)):
    return c.snapshot()


@app.get("/api/health")
async def health(c: Core = Depends(core)):
    return {"status": "running", "identity": c.identity, "dependencies": c.snapshot()["dependencies"]}


@app.get("/api/events")
async def events(request: Request, after: int = 0, c: Core = Depends(core)):
    try:
        after = max(after, int(request.headers.get("last-event-id", "0")))
    except ValueError:
        pass
    async def stream():
        cursor = after
        while not await request.is_disconnected():
            found = c.store.events(after=cursor, limit=100)
            for event in found:
                cursor = int(event["sequence"])
                yield f"id: {cursor}\nevent: update\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
            if not found:
                yield ": heartbeat\n\n"
            await asyncio.sleep(.5)
    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/chat")
async def chat(item: ChatRequest, c: Core = Depends(core)):
    return await c.victim.chat(item.message)


@app.post("/api/inject")
async def inject(item: Injection, c: Core = Depends(core)):
    with c.store.tx() as db:
        if c.store.meta(db, "muted") and c.store.meta(db, "memory_enabled"):
            raise Rejected("UNSUPPORTED_CONDITION", "暂不支持 Playbook 与禁言同时开启；请关闭其中一项", 422)
    return await c.inject(item.scenario, item.condition, item.seed)


@app.post("/api/evaluate")
async def evaluate(item: Injection, c: Core = Depends(core)):
    if c.store.current_config()["generation"] == "0":
        raise Rejected("NOT_READY", "服务未准备就绪")
    return await c.inject(item.scenario, item.condition, item.seed, environment="evaluation")


@app.post("/api/reset")
async def reset(c: Core = Depends(core)):
    return await c.reset()


@app.put("/api/autonomy")
async def autonomy(item: AutonomyRequest, c: Core = Depends(core)):
    return c.store.set_autonomy(item.level)


@app.put("/api/communication")
async def communication(item: CommunicationRequest, c: Core = Depends(core)):
    return c.store.set_muted(item.muted)


@app.put("/api/memory")
async def memory_mode(item: AttractRequest, c: Core = Depends(core)):
    with c.store.tx() as db:
        c.store.set_meta(db, "memory_enabled", item.enabled)
        c.store.event(db, "policy.memory", {"enabled": item.enabled, "summary": "下轮使用 Playbook" if item.enabled else "下轮关闭 Playbook"}, producer="operator")
    return {"enabled": item.enabled}


@app.post("/api/approve")
async def approve(item: PlanId, c: Core = Depends(core)):
    return c.store.approve(item.plan_id)


@app.post("/api/agents/{aid}/pause")
async def pause(aid: str, c: Core = Depends(core)):
    return await c.pause(aid, True)


@app.post("/api/agents/{aid}/resume")
async def resume(aid: str, c: Core = Depends(core)):
    return await c.pause(aid, False)


@app.post("/api/agents/join")
async def join(c: Core = Depends(core)):
    result = c.spawn("cost")
    with c.store.tx() as db:
        rid = c.store.meta(db, "current_run")
        run = c.store.get(db, "runs", rid) if rid else None
        if run and "cost_high" in run["symptoms"] and run["status"] in ACTIVE:
            existing = db.execute("SELECT data FROM tasks WHERE run_id=? AND json_extract(data,'$.kind')='cost'", (rid,)).fetchone()
            if not existing:
                task = c.store._new_task(db, rid, "cost")
                c.store.event(db, "task.discovered", {"task_id": task["id"], "capability": "cost", "summary": "能力目录发现新成本专家，发布可认领任务"}, run_id=rid)
    return result


@app.post("/api/demo/fencing")
async def fencing(c: Core = Depends(core)):
    return await c.inject("prompt", fencing=True)


@app.put("/api/attract")
async def attract(item: AttractRequest, c: Core = Depends(core)):
    return await c.set_attract(item.enabled)


@app.get("/api/runs")
async def runs(c: Core = Depends(core)):
    return {"items": c.store.runs()}


@app.get("/api/runs/{rid}")
async def run_detail(rid: str, c: Core = Depends(core)):
    return c.run_detail(rid)


@app.get("/api/rca/{rid}")
async def rca(rid: str, c: Core = Depends(core)):
    return c.rca(rid)


@app.get("/api/scoreboard")
async def scoreboard(version: str | None = None, c: Core = Depends(core)):
    return c.scoreboard(version)


@app.get("/api/playbooks")
async def playbooks(c: Core = Depends(core)):
    return {"items": c.playbooks(), "status": c.memory.status()}


def _collaboration_event_history(c: Core, state: dict) -> list[dict]:
    events = state.get("events") if isinstance(state.get("events"), list) else []
    sequences = [int(event["sequence"]) for event in events
                 if isinstance(event, dict) and str(event.get("sequence", "")).isdigit()]
    latest = max(sequences, default=0)
    # The live operator snapshot is intentionally a short window. Collaboration
    # capabilities need durable proof across resets, so inspect a bounded
    # 5,000-event audit window internally without returning raw events to the UI.
    return c.store.events(after=max(0, latest - 5000), limit=5000)


@app.get("/api/product/snapshot")
async def product_snapshot(c: Core = Depends(core)):
    state = c.snapshot()
    history = _collaboration_event_history(c, state)
    return {**c.product.snapshot(), "collaboration": _product_collaboration(state, history)}


def _product_collaboration(state: dict, event_history: list[dict] | None = None) -> dict:
    agents = state.get("agents") if isinstance(state.get("agents"), list) else []
    runs = state.get("runs") if isinstance(state.get("runs"), list) else []
    events = event_history if event_history is not None else state.get("events", [])
    if not isinstance(events, list):
        events = []
    event_types = {event.get("event_type") for event in events}
    stale_fence_runs = {event.get("run_id") for event in events
                        if event.get("event_type") in {"action.rejected", "grant.rejected"}
                        and (event.get("payload") or {}).get("code") == "FENCE_STALE"}
    resumed_runs = {event.get("run_id") for event in events if event.get("event_type") == "agent.resumed"}
    joined_expert = any(agent.get("role") == "cost" and agent.get("status") not in {"offline", "failed"}
                        for agent in agents)
    delivered_message = any(event.get("event_type") == "dialog.received"
                            and all((event.get("payload") or {}).get(field) is not None
                                    for field in ("generation", "task_epoch", "transport_epoch"))
                            for event in events)
    muted_drop = any(event.get("event_type") == "dialog.dropped"
                     and (event.get("payload") or {}).get("reason") == "MUTED" for event in events)
    reused_playbook = any(run.get("reuseapproved") is True and run.get("memory_used_id")
                          for run in runs)
    handoff_observed = bool(stale_fence_runs & resumed_runs)
    local = {
        "capability_discovery": {"status": "ready" if agents else "unknown", "detail": "Section9 本地能力目录来自持久 Agent 注册与心跳", "evidence": "GET /api/state"},
        "expert_join": {"status": "ready" if joined_expert or "agent.joined" in event_types else "unknown",
                        "detail": "已观察到本地专家加入" if joined_expert or "agent.joined" in event_types else "入口存在，尚无专家加入证据",
                        "evidence": "POST /api/agents/join"},
        "structured_messages": {"status": "ready" if delivered_message else "unknown",
                                "detail": "已观察到带租约与作用域的真实消息" if delivered_message else "消息协议存在，尚无满足契约的交付记录",
                                "evidence": "dialog.received"},
        "handoff": {"status": "ready" if handoff_observed else "unknown",
                    "detail": "已观察到过期执行者被 fence 拒绝并完成接管" if handoff_observed else "接管逻辑已实现，尚无完整 fence 拒绝与恢复记录",
                    "evidence": "FENCE_STALE + agent.resumed"},
        "communication_isolation": {"status": "ready" if muted_drop else "unknown",
                                     "detail": "已观察到禁言时服务端丢弃消息" if muted_drop else "禁言策略已实现，尚无服务端隔离事件",
                                     "evidence": "dialog.dropped / MUTED"},
        "experience_reuse": {"status": "ready" if reused_playbook else "unknown",
                              "detail": "已有经批准的经验复用运行" if reused_playbook else
                              "经验资产已加载，但尚无运行满足复用批准与回写条件",
                              "evidence": "run.memory_used_id + run.reuseapproved"},
        "contradiction_review": {"status": "unknown", "detail": "当前本地实现保留独立验收，但没有宣称已形成通用反证目录", "evidence": "not_observed"},
    }
    official = {
        "native_remote_session": {"status": "unsupported", "detail": "未配置官方远端会话身份；未伪造会话", "authorization": "required"},
        "official_capability_discovery": {"status": "unsupported", "detail": "官方 Hub/A2A 目录适配未启用", "authorization": "required"},
        "official_experience_exchange": {"status": "unsupported", "detail": "recipe/A2A 发布与接收未启用；本地经验不冒充远端收据", "authorization": "required"},
    }
    return {"scope": {"project_id": "section9-local", "environment_id": "local"}, "local": local, "official_remote": official,
            "agents": [{"id": a.get("id"), "role": a.get("role"), "status": a.get("status"), "capabilities": a.get("capabilities", [])} for a in agents]}


@app.get("/api/product/projects")
async def product_projects(c: Core = Depends(core)):
    manifest = c.product.manifest
    return {"items": [{"id": manifest["project_id"], "name": manifest["name"], "environment_id": manifest["environment_id"],
                       "source": {"repo_url": manifest["repo_url"], "commit": manifest["commit"], "license": manifest["license"]}}]}


@app.post("/api/product/connect")
async def product_connect(item: ProductConnectRequest, c: Core = Depends(core)):
    if (item.project_id, item.environment_id) != (c.product.project_id, c.product.environment_id):
        raise ProductError("SCOPE_FORBIDDEN", "当前操作者未选择该项目或环境", 403)
    return await c.product.connect(run_tests=item.run_tests)


@app.post("/api/product/business-probe")
async def product_business_probe(item: ProductBusinessProbeRequest, c: Core = Depends(core)):
    if (item.project_id, item.environment_id) != (c.product.project_id, c.product.environment_id):
        raise ProductError("SCOPE_FORBIDDEN", "当前操作者未选择该项目或环境", 403)
    return await c.product.business_probe(sample_name=item.sample)


@app.post("/api/product/regression")
async def product_regression(item: ProductScopeRequest, c: Core = Depends(core)):
    if (item.project_id, item.environment_id) != (c.product.project_id, c.product.environment_id):
        raise ProductError("SCOPE_FORBIDDEN", "当前操作者未选择该项目或环境", 403)
    return await c.product.run_regression()


@app.get("/api/product/incidents")
async def product_incidents(project_id: str, environment_id: str, c: Core = Depends(core)):
    if (project_id, environment_id) != (c.product.project_id, c.product.environment_id):
        raise ProductError("SCOPE_FORBIDDEN", "当前操作者未选择该项目或环境", 403)
    return {"items": c.product.registry.list("incident", project_id=project_id, environment_id=environment_id)}


@app.get("/api/product/incidents/{incident_id}")
async def product_incident(incident_id: str, project_id: str, environment_id: str, c: Core = Depends(core)):
    if (project_id, environment_id) != (c.product.project_id, c.product.environment_id):
        raise ProductError("SCOPE_FORBIDDEN", "当前操作者未选择该项目或环境", 403)
    incident = c.product.registry.get("incident", incident_id, project_id=project_id, environment_id=environment_id)
    if not incident:
        raise ProductError("NOT_FOUND", "未找到外部项目事故", 404)
    return {**incident, "events": c.product.registry.events(project_id=project_id, environment_id=environment_id, incident_id=incident_id)}


@app.post("/api/product/approve")
async def product_approve(item: ProductApprovalRequest, c: Core = Depends(core)):
    if (item.project_id, item.environment_id) != (c.product.project_id, c.product.environment_id):
        raise ProductError("SCOPE_FORBIDDEN", "当前操作者未选择该项目或环境", 403)
    return c.product.approve(item.incident_id)


@app.post("/api/product/execute")
async def product_execute(item: ProductExecuteRequest, c: Core = Depends(core)):
    if (item.project_id, item.environment_id) != (c.product.project_id, c.product.environment_id):
        raise ProductError("SCOPE_FORBIDDEN", "当前操作者未选择该项目或环境", 403)
    return await c.product.execute(item.incident_id, item.approval_id, item.idempotency_key)


@app.post("/api/product/observe")
async def product_observe(item: ProductIncidentRequest, c: Core = Depends(core)):
    if (item.project_id, item.environment_id) != (c.product.project_id, c.product.environment_id):
        raise ProductError("SCOPE_FORBIDDEN", "当前操作者未选择该项目或环境", 403)
    if not item.incident_id:
        raise ProductError("INCIDENT_REQUIRED", "需要明确事故作用域", 422)
    return await c.product.observe(item.incident_id)


@app.post("/api/product/stop")
async def product_stop(item: ProductScopeRequest, c: Core = Depends(core)):
    if (item.project_id, item.environment_id) != (c.product.project_id, c.product.environment_id):
        raise ProductError("SCOPE_FORBIDDEN", "当前操作者未选择该项目或环境", 403)
    return c.product.stop()


@app.get("/api/product/capabilities")
async def product_capabilities(c: Core = Depends(core)):
    state = c.snapshot()
    return _product_collaboration(state, _collaboration_event_history(c, state))


@app.post("/api/product/collaboration/join")
async def product_collaboration_join(c: Core = Depends(core)):
    return await join(c)


@app.post("/api/product/backup")
async def product_backup(item: ProductScopeRequest, c: Core = Depends(core)):
    if (item.project_id, item.environment_id) != (c.product.project_id, c.product.environment_id):
        raise ProductError("SCOPE_FORBIDDEN", "当前操作者未选择该项目或环境", 403)
    stamp = str(time.time_ns())
    target = config.ROOT / "artifacts" / "external-support-agent" / "backups" / f"backup_{stamp}"
    result = await asyncio.to_thread(ProductBackup(
        config.DATA / "product.sqlite", config.DATA / "product-artifacts",
        {"support-agent": c.product.runtime.data_path},
    ).create, target)
    c.product.registry.event("product.backup.created", {"backup_dir": str(target), "schema_version": result["manifest"]["schema_version"]},
                             project_id=c.product.project_id, environment_id=c.product.environment_id)
    return result


@app.get("/agent/context")
async def context(a=Depends(agent), c: Core = Depends(core)):
    return c.context(a)


@app.post("/agent/heartbeat")
async def heartbeat(item: AgentStatusRequest, a=Depends(agent), c: Core = Depends(core)):
    return c.store.heartbeat(a["id"], **item.model_dump())


@app.post("/agent/incidents")
async def incidents(item: IncidentRequest, a=Depends(agent), c: Core = Depends(core)):
    if a["role"] != "sentry":
        raise Rejected("ROLE_FORBIDDEN", "只有哨兵可按观测立案", 403)
    return c.store.open_incident(item.observation_ids)


@app.post("/agent/claim")
async def claim(item: ClaimRequest, a=Depends(agent), c: Core = Depends(core)):
    return c.store.claim(a["id"], item.task_id)


@app.post("/agent/renew")
async def renew(item: Renew, a=Depends(agent), c: Core = Depends(core)):
    return c.store.renew(a["id"], item.task_id, item.epoch)


@app.post("/agent/task/complete")
async def complete(item: Complete, a=Depends(agent), c: Core = Depends(core)):
    return c.store.complete_task(a["id"], item.task_id, item.epoch, item.result_summary)


@app.post("/agent/message")
async def message(item: MessageRequest, a=Depends(agent), c: Core = Depends(core)):
    return c.store.message(a["id"], item.model_dump())


@app.post("/agent/model")
async def model(item: ModelRequest, a=Depends(agent), c: Core = Depends(core)):
    allowed = {"diagnoser": {"diagnose"}, "fixer": {"repair"}, "single": {"single", "review"}, "cost": {"cost"}}
    if item.purpose not in allowed.get(a["role"], set()):
        raise Rejected("ROLE_FORBIDDEN", "角色无权调用此推理工具", 403)
    with c.store.tx() as db:
        claimed = [json.loads(row[0]) for row in db.execute("SELECT data FROM tasks WHERE run_id=?", (item.run_id,))]
        task = next((t for t in claimed if t["holder"] == a["id"] and t["status"] == "claimed"), None)
        if not task:
            raise Rejected("LEASE_REQUIRED", "推理前须自主认领有效任务", 403)
        current_agent = c.store.get(db, "agents", a["id"])
        run = c.store.get(db, "runs", item.run_id)
        c.store._valid_task(db, current_agent, task, run, task["epoch"])
        authority = {
            "agent_id": current_agent["id"], "instance_id": current_agent["instance_id"],
            "role": current_agent["role"], "task_id": task["id"], "task_epoch": task["epoch"],
            "run_id": run["id"], "generation": task["generation"],
            "policy_revision": str(c.store.meta(db, "autonomy_revision")),
            "transport_epoch": str(c.store.meta(db, "transport_epoch")),
            "config_revision": str(c.store.current_config(db)["revision"]),
            "contract_hash": run["contract_hash"],
        }
    try:
        result = await c.model.complete(item.messages, run_id=item.run_id, purpose=item.purpose,
                                        max_tokens=item.max_tokens, generation=task["generation"],
                                        authority=authority)
        try:
            with c.store.tx() as db:
                current_agent = c.store.get(db, "agents", a["id"])
                if current_agent["instance_id"] != a["instance_id"]:
                    raise Rejected("INSTANCE_STALE", "模型返回时工作进程身份已失效")
                c.store._valid_task(db, current_agent, c.store.get(db, "tasks", task["id"]),
                                   c.store.get(db, "runs", item.run_id), task["epoch"])
        except Rejected as exc:
            c.store.emit("model.source_stale", {"code": exc.code, "task_id": task["id"],
                         "summary": "模型返回时来源租约已失效；真实消耗保留，结果不交付"}, run_id=item.run_id, producer=a["id"])
            raise
        return result
    except Rejected:
        raise
    except Exception as exc:
        raise Rejected("MODEL_FAILURE", str(exc)[:300], 503) from None


@app.post("/agent/plan")
async def plan(item: PlanRequest, a=Depends(agent), c: Core = Depends(core)):
    return c.store.create_plan(a["id"], item.model_dump())


@app.post("/agent/grant")
async def grant(item: PlanId, a=Depends(agent), c: Core = Depends(core)):
    result = c.store.grant(a["id"], item.plan_id)
    if result.get("pause_before_execute"):
        c.job(c.freeze_after_grant(a["id"]))
    return result


@app.post("/agent/execute")
async def execute(item: ExecuteRequest, a=Depends(agent), c: Core = Depends(core)):
    return c.store.execute(a["id"], item.plan_id, item.grant_id, item.idempotency_key)


@app.post("/agent/verify")
async def verify(item: VerifyRequest, a=Depends(agent), c: Core = Depends(core)):
    return await c.verify(a, item.model_dump())


@app.post("/api/telemetry/v1/traces")
async def telemetry(request: Request, c: Core = Depends(core)):
    # Untrusted trace payloads are evidence only: never execute or change policy.
    raw = await request.body()
    if len(raw) > 4_000_000:
        return Response(status_code=413)
    try:
        if request.headers.get("content-encoding", "").lower() == "gzip":
            with gzip.GzipFile(fileobj=io.BytesIO(raw)) as compressed:
                raw = compressed.read(4_000_001)
            if len(raw) > 4_000_000:
                return Response(status_code=413)
        from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
        from google.protobuf.json_format import ParseDict
        data = ExportTraceServiceRequest()
        if "json" in request.headers.get("content-type", ""):
            ParseDict(json.loads(raw), data)
        else:
            data.ParseFromString(raw)
        for resource in data.resource_spans:
            for scope in resource.scope_spans:
                for span in scope.spans:
                    attrs = {a.key: a.value.string_value or a.value.int_value or a.value.double_value for a in span.attributes}
                    rid = attrs.get("langfuse.observation.metadata.run_id")
                    if rid == "interactive":
                        rid = None
                    destination = c.store
                    pair_id = attrs.get('langfuse.observation.metadata.pair_id')
                    arm = attrs.get('langfuse.observation.metadata.arm')
                    if pair_id and pair_id != 'console':
                        pairs = getattr(request.app.state, 'pairs', None)
                        destination = pairs.telemetry_store(pair_id, rid, arm) if pairs else None
                        if destination is None:
                            continue
                    if rid and not destination.run(rid):
                        destination = c.evaluation_store()
                        if not destination or not destination.run(rid):
                            attract_path = config.ROOT / "data/attract/section9.sqlite"
                            if not attract_path.exists():
                                continue
                            destination = Store.__new__(Store)
                            destination.path = attract_path
                            if not destination.run(rid):
                                continue
                    destination.emit("telemetry.span_received", {"trace_id": span.trace_id.hex(), "span_id": span.span_id.hex(), "name": span.name,
                        "duration_ms": (span.end_time_unix_nano-span.start_time_unix_nano)/1e6,
                        "attributes": attrs, "summary": "Collector 独立路径收到完整 span"}, run_id=rid, producer="collector")
        c.last_collector_at = now()
        c.collector_ingest_error = None
        return Response(content=b"", media_type="application/x-protobuf")
    except Exception as exc:
        c.collector_ingest_error = type(exc).__name__
        c.store.emit("telemetry.ingest_rejected", {"code": type(exc).__name__, "summary": "Collector 数据未被控制端接收"}, producer="collector")
        return Response(status_code=400)


dist = config.ROOT / "frontend" / "dist"
if dist.exists():
    app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")
    if (dist / "vendor").exists():
        app.mount("/vendor", StaticFiles(directory=dist / "vendor"), name="vendor")


@app.get("/demo")
@app.get("/")
@app.get("/showcase/swarm")
@app.get("/showcase/baseline")
async def index():
    if not (dist / "index.html").exists():
        return JSONResponse({"error": {"code": "FRONTEND_NOT_BUILT", "message": "请执行启动脚本构建前端"}}, status_code=503)
    return FileResponse(dist / "index.html", headers={"Cache-Control": "no-cache"})
