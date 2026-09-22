from __future__ import annotations

import asyncio
import gzip
import io
import json
import uvicorn
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import Depends, FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import Field

from s9 import config
from s9.contracts import (AgentStatusRequest, ChatRequest, ClaimRequest, ExecuteRequest, Injection,
                          MessageRequest, ModelRequest, PlanRequest, StrictModel)
from s9.core import Core
from s9.store import ACTIVE, Rejected, Store, now


@asynccontextmanager
async def lifespan(app):
    app.state.core = Core()
    # The same authority, a separate restricted ingress. No second database,
    # supervisor, model gateway or application lifespan is started here.
    agent_server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=config.PORT + 2,
                                                lifespan="off", access_log=False, log_level="warning"))
    agent_task = asyncio.create_task(agent_server.serve())
    await app.state.core.start()
    yield
    await app.state.core.stop()
    agent_server.should_exit = True
    await agent_task


app = FastAPI(title="Section9 local laboratory", lifespan=lifespan)


def core(request: Request) -> Core:
    return request.app.state.core


@app.exception_handler(Rejected)
async def rejected(request, exc):
    if request.url.path.startswith("/agent/") and hasattr(request.state, "agent_id"):
        request.app.state.core.store.emit("agent.request_rejected", {"path": request.url.path, "code": exc.code,
            "summary": exc.message}, producer=request.state.agent_id)
    return JSONResponse({"error": {"code": exc.code, "message": exc.message}}, status_code=exc.status)


@app.middleware("http")
async def boundaries(request: Request, call_next):
    server_port = (request.scope.get("server") or (None, None))[1]
    if server_port == config.PORT + 2 and not request.url.path.startswith("/agent/"):
        return JSONResponse({"error": {"code": "ROLE_FORBIDDEN", "message": "工作进程端口不提供控制台、日志或文件访问"}}, status_code=403)
    if server_port == config.PORT and request.url.path.startswith("/agent/"):
        return JSONResponse({"error": {"code": "WRONG_INGRESS", "message": "Agent 需使用专属执行入口"}}, status_code=403)
    # The console is a trusted local operator surface, not a worker tool.
    # A role credential is never accepted on operator data/control routes.
    if request.url.path.startswith("/api/") and "telemetry" not in request.url.path:
        if request.headers.get("authorization"):
            return JSONResponse({"error": {"code": "ROLE_FORBIDDEN", "message": "Agent 身份不能访问操作者控制台"}}, status_code=403)
        origin = request.headers.get("origin")
        if origin and origin not in {f"http://127.0.0.1:{config.PORT}", f"http://localhost:{config.PORT}"}:
            return JSONResponse({"error": {"code": "ORIGIN_FORBIDDEN", "message": "只接受本地控制台来源"}}, status_code=403)
    return await call_next(request)


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


@app.get("/api/state")
async def state(c: Core = Depends(core)):
    return c.snapshot()


@app.get("/api/health")
async def health(c: Core = Depends(core)):
    return {"status": "running", "dependencies": c.snapshot()["dependencies"]}


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
async def scoreboard(c: Core = Depends(core)):
    return c.scoreboard()


@app.get("/api/playbooks")
async def playbooks(c: Core = Depends(core)):
    return {"items": c.playbooks(), "status": c.memory.status()}


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
        c.store._valid_task(db, c.store.get(db, "agents", a["id"]), task,
                            c.store.get(db, "runs", item.run_id), task["epoch"])
    try:
        return await c.model.complete(item.messages, run_id=item.run_id, purpose=item.purpose, max_tokens=item.max_tokens)
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
async def verify(item: RunId, a=Depends(agent), c: Core = Depends(core)):
    return await c.verify(a, item.run_id)


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


@app.get("/")
async def index():
    if not (dist / "index.html").exists():
        return JSONResponse({"error": {"code": "FRONTEND_NOT_BUILT", "message": "请执行启动脚本构建前端"}}, status_code=503)
    return FileResponse(dist / "index.html", headers={"Cache-Control": "no-cache"})
