from __future__ import annotations

import asyncio
import json
import os
import signal
import statistics
import subprocess
import sys
import sysconfig
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

from s9 import config
from s9.model import ModelClient
from s9.provenance import capture_identity
from s9.store import ACTIVE, DEFAULT_CONFIG, Rejected, Store, digest, now
from s9.telemetry import Telemetry
from s9.victim import VictimApp


class Core:
    def __init__(self):
        self.identity = capture_identity()
        self.implementation_hash = digest({p.name: p.read_text() for p in sorted((config.ROOT / "s9").glob("*.py"))})
        self.store = Store(config.DATA / "section9.sqlite")
        self.telemetry = Telemetry()
        self.model = ModelClient(self.store, self.telemetry)
        self.victim = VictimApp(self.model, self.store.current_config, self.emit)
        from s9.memory import MemoryStore
        self.memory = MemoryStore(config.DATA / "memory")
        self.evaluation_memory = MemoryStore(config.DATA / "evaluation-memory")
        # The evaluation retrieval snapshot stays frozen. Successful outcomes
        # are written to a separate real journal, never to the next trial's input.
        self.evaluation_results = MemoryStore(config.DATA / "evaluation-results")
        self.memory_candidates = {}
        self.children: dict[str, subprocess.Popen] = {}
        self.jobs: set[asyncio.Task] = set()
        self.job_generations = {}
        self.worker_restarts = {}
        self.pending_detection: set[str] = set()
        self.probe_schedule = {}
        self.verifying: set[str] = set()
        self.progress = {}
        self.dependency_cache = {}
        self.last_collector_at = None
        self.collector_ingest_error = None
        self.attract_process = None
        self.attract_cycles = 0
        self.attract_active = False
        self.attract_status = {}
        self.runtime = config.DATA / "runtime"
        self.runtime.mkdir(parents=True, exist_ok=True)

    def job(self, coroutine, generation=None):
        task = asyncio.create_task(coroutine)
        self.jobs.add(task)
        self.job_generations[task] = generation
        task.add_done_callback(self.jobs.discard)
        task.add_done_callback(lambda done: self.job_generations.pop(done, None))
        return task

    async def start(self):
        # A process restart cannot safely retain leases or an unfinished proof.
        with self.store.tx() as db:
            current = self.store.meta(db, "current_run")
        if current and self.store.run(current)["status"] in ACTIVE:
            self.store.fail_run(current, "SERVER_RESTARTED")
            self.store.reset()
        for agent_id in ["sentry", "diagnoser", "fixer-a", "fixer-b", "verifier", "single"]:
            self.spawn(agent_id)
        self.job(self.watchdog())
        self.job(self.dependencies_loop())
        if os.getenv("S9_ENVIRONMENT") == "attract":
            self.job(self.attract_loop())
        self.store.emit("system.started", {"summary": "Section9 服务与独立角色进程已启动", "model": config.MODEL})

    def spawn(self, agent_id):
        child = self.children.get(agent_id)
        if child and child.poll() is None:
            return {"id": agent_id, "already_running": True}
        token = self.store.register(agent_id)
        # Never inherit model/telemetry credentials into worker processes.
        env = {k: os.environ[k] for k in ["PATH", "HOME", "LANG", "TMPDIR", "SYSTEMROOT"] if k in os.environ}
        env.update(S9_AGENT_TOKEN=token, PYTHONUNBUFFERED="1")
        env.update(PYTHONDONTWRITEBYTECODE="1", PYTHONNOUSERSITE="1",
                   PYTHONPATH=os.pathsep.join([str(config.ROOT), sysconfig.get_path("purelib")]))
        log = (self.runtime / f"{agent_id}.log").open("ab")
        from s9.sandbox import worker_command
        command = worker_command(sys.executable, config.ROOT, config.PORT + 2,
                                 ["--id", agent_id, "--base-url", f"http://127.0.0.1:{config.PORT + 2}"])
        # Seatbelt execvp rejects the venv symlink; PYTHONPATH above keeps the
        # dependency runtime while the approved real interpreter is executed.
        command[3] = str(Path(sys.executable).resolve())
        child = subprocess.Popen(command,
                                 cwd=config.ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        log.close()
        self.children[agent_id] = child
        self.store.emit("agent.process_started", {"pid": child.pid, "summary": "独立工作进程启动"}, producer=agent_id)
        return {"id": agent_id, "pid": child.pid}

    async def stop(self):
        for job in list(self.jobs):
            job.cancel()
        await asyncio.gather(*list(self.jobs), return_exceptions=True)
        for child in self.children.values():
            if child.poll() is None:
                child.send_signal(signal.SIGCONT)
                child.terminate()
        for child in self.children.values():
            try:
                child.wait(timeout=3)
            except subprocess.TimeoutExpired:
                child.kill()
        if self.attract_process and self.attract_process.poll() is None:
            self.attract_process.terminate()
        await self.model.close()
        await asyncio.to_thread(self.telemetry.flush)

    async def emit(self, event_type, payload, *, run_id=None, producer="victim"):
        # Late telemetry is retained as evidence but never promoted into a new incident.
        event = self.store.emit(event_type, payload, run_id=run_id, producer=producer)
        if event_type == "request.progress" and run_id:
            rid = payload["request_id"]
            key = (payload.get("tool_call_hash"), payload.get("state_digest"))
            prev = self.progress.get(rid, (None, 0))
            count = prev[1] + 1 if prev[0] == key else 1
            self.progress[rid] = (key, count)
            if count == 4:
                self.store.emit("observation.liveness", {"signal": "tool_stalled", "summary": "独立活性路径发现同工具、同状态重复 4 次",
                    "request_id": rid, "tool_call_hash": key[0], "state_digest": key[1], "steps": count,
                    "evidence_id": event["event_id"]}, run_id=run_id, producer="liveness")
        return event

    async def inject(self, scenario, condition="swarm", seed=42, fencing=False, environment=None):
        run = self.store.inject(scenario, condition, config.MODEL, seed, config.RUN_TOKEN_BUDGET)
        with self.store.tx() as db:
            item = self.store.get(db, "runs", run["id"])
            item["manifest"]["environment"] = environment or os.getenv("S9_ENVIRONMENT", "demo")
            item["manifest"]["model_timeout_s"] = config.MODEL_TIMEOUT
            item["manifest"]["worker_completion_cap"] = 1600
            item["manifest"]["implementation_hash"] = self.implementation_hash
            item["manifest"]["source_identity"] = self.identity
            item["manifest"]["source_identity_hash"] = digest(self.identity)
            item["manifest"]["probe_schedule"] = {"interval_s": 30, "max_detection_rounds": 3, "scope": "active_incident"}
            item["manifest"]["victim_config"] = self.store.current_config(db)
            memory_source = self.evaluation_memory if item["manifest"]["environment"] == "evaluation" else self.memory
            item["manifest"]["memory_snapshot_hash"] = digest(memory_source.list_playbooks()) if "memory" in condition else None
            if fencing:
                item["fencing_demo"] = True
            self.store.save(db, "runs", item)
        self.pending_detection.add(run["id"])
        self.probe_schedule[run["id"]] = {"last_at": time.monotonic(), "rounds": 1}
        if scenario in {"loop", "composite"}:
            self.job(self.victim.chat("查询不存在订单 S9-MISSING 的物流", run_id=run["id"], purpose="loop-workload"), generation=run["generation"])
        self.job(self.detect(run["id"]), generation=run["generation"])
        return {"run_id": run["id"], "revision": run["injected_revision"]}

    async def detect(self, run_id):
        try:
            result = await self.victim.probe(suite="detect", run_id=run_id)
            self.store.emit("probe.completed", {"suite": "detect", "result": result, "summary": "检测集请求完成，保留原始业务结果"}, run_id=run_id, producer="probe")
            mapping = {"semantic_policy": "quality_mismatch", "cost_budget": "cost_high", "no_stalled_requests": "tool_stalled"}
            for check in result["checks"]:
                if not check["passed"] and check["name"] in mapping:
                    # Missing model output is a dependency failure, never semantic proof.
                    if check["name"] == "semantic_policy" and not check.get("actual"):
                        continue
                    if check["name"] == "cost_budget" and not check.get("actual", {}).get("failures"):
                        continue
                    self.store.emit("observation.probe", {"signal": mapping[check["name"]], "summary": "业务探针发现 " + check["name"], **check}, run_id=run_id, producer="probe")
            if not any(e["payload"].get("signal") for e in self.store.events(run_id=run_id, limit=1000)):
                self.store.fail_run(run_id, "NO_CONFIRMED_ANOMALY_OR_MODEL_FAILURE")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.store.emit("probe.failed", {"summary": "探针未完成", "error": type(exc).__name__}, run_id=run_id, producer="probe")
            self.store.fail_run(run_id, "DETECTION_FAILED")
        finally:
            self.pending_detection.discard(run_id)

    async def watchdog(self):
        while True:
            await asyncio.sleep(1)
            with self.store.tx() as db:
                rid = self.store.meta(db, "current_run")
            run = self.store.run(rid) if rid else None
            schedule = self.probe_schedule.get(rid)
            if run and run["status"] in ACTIVE - {"verifying"} and schedule and schedule["rounds"] < 3 and rid not in self.pending_detection and time.monotonic() - schedule["last_at"] > 30:
                schedule.update(last_at=time.monotonic(), rounds=schedule["rounds"] + 1)
                self.pending_detection.add(rid)
                self.job(self.detect(rid), generation=run["generation"])
            if run and run["status"] in ACTIVE and time.monotonic() - run["start_mono"] > config.RUN_TIMEOUT:
                self.store.fail_run(rid, "RUN_TIMEOUT")
                self.victim.cancel_all("timeout")
                await self.model.cancel_generation(run["generation"])
            if run and run.get("fencing_paused") and not run.get("fencing_resumed") and (run.get("last_action") or run["status"] not in ACTIVE):
                agent_id = run["fencing_original"]
                await self.pause(agent_id, False)
                with self.store.tx() as db:
                    run = self.store.get(db, "runs", rid)
                    run["fencing_resumed"] = True
                    self.store.save(db, "runs", run)
            for aid, child in list(self.children.items()):
                if child.poll() is not None:
                    with self.store.tx() as db:
                        a = self.store.get(db, "agents", aid)
                        if a["status"] != "offline":
                            a.update(status="offline", detail=f"进程退出 ({child.returncode})")
                            self.store.save(db, "agents", a)
                            self.store.event(db, "agent.offline", {"summary": a["detail"]}, producer=aid, run_id=rid)
                    # Terminate the affected incident explicitly; bounded restart
                    # supplies a new instance for the next incident.
                    if run and run["status"] in ACTIVE:
                        self.store.fail_run(rid, "WORKER_EXIT:" + aid)
                        await self.model.cancel_generation(run["generation"])
                    recent = [t for t in self.worker_restarts.get(aid, []) if time.monotonic() - t < 60]
                    if len(recent) < 2:
                        self.worker_restarts[aid] = recent + [time.monotonic()]
                        try:
                            self.spawn(aid)
                        except Exception as exc:
                            self.store.emit("agent.restart_failed", {"summary": "工作进程重启失败", "error": type(exc).__name__}, producer=aid)

    async def pause(self, aid, paused):
        item = self.store.pause(aid, paused)
        child = self.children.get(aid)
        if child and child.poll() is None:
            child.send_signal(signal.SIGSTOP if paused else signal.SIGCONT)
            self.store.emit("agent.process_signal", {"signal": "SIGSTOP" if paused else "SIGCONT", "pid": child.pid,
                                                       "summary": "操作系统暂停进程" if paused else "操作系统恢复进程"}, producer=aid)
        return item

    async def freeze_after_grant(self, aid):
        # Let the worker receive and retain the original grant before SIGSTOP.
        await asyncio.sleep(.6)
        with self.store.tx() as db:
            a = self.store.get(db, "agents", aid)
        if a and a["paused"]:
            await self.pause(aid, True)

    async def reset(self):
        old_generation = self.store.current_config()["generation"]
        result = self.store.reset()  # invalidate before cancelling queued or in-flight I/O
        result["checks"]["cancelled_requests"] = self.victim.cancel_all("reset")
        old_jobs = [task for task, generation in self.job_generations.items() if generation == old_generation]
        for task in old_jobs:
            task.cancel()
        result["model_cancellation"] = await self.model.cancel_generation(old_generation)
        if old_jobs:
            await asyncio.gather(*old_jobs, return_exceptions=True)
        result["checks"]["model_requests_released"] = not result["model_cancellation"]["remaining"]
        self.pending_detection.clear()
        self.probe_schedule.clear()
        self.progress.clear()
        for child in self.children.values():
            if child.poll() is None:
                child.send_signal(signal.SIGCONT)
        return result

    def context(self, agent):
        with self.store.tx() as db:
            aid, role = agent["id"], agent["role"]
            agent = self.store.get(db, "agents", aid)
            config_view = self.store.current_config(db)
            rid = self.store.meta(db, "current_run")
            run = self.store.get(db, "runs", rid) if rid else None
            muted = self.store.meta(db, "muted")
            epoch = str(self.store.meta(db, "transport_epoch"))
            tasks, messages = [], []
            if run and run["status"] in ACTIVE:
                for row in db.execute("SELECT data FROM tasks WHERE run_id=?", (rid,)):
                    t = json.loads(row[0])
                    if t["kind"] not in agent["capabilities"]:
                        continue
                    if t["status"] == "claimed" and t["lease_deadline"] <= time.time():
                        t["status"] = "available"
                    tasks.append({k: t[k] for k in ["id", "kind", "status", "epoch", "holder"]})
                if not muted:
                    for row in db.execute("SELECT data FROM messages WHERE run_id=?", (rid,)):
                        msg = json.loads(row[0])
                        task = self.store.get(db, "tasks", msg.get("task_id"))
                        sender = self.store.get(db, "agents", msg.get("sender"))
                        if (msg.get("transport_epoch") == epoch and msg.get("generation") == run["generation"]
                                and task and task["run_id"] == rid and task["epoch"] == msg.get("task_epoch")
                                and sender and sender["instance_id"] == msg.get("instance_id")):
                            messages.append(msg)
            projected = {"revision": config_view["revision"], "generation": config_view["generation"]}
            if role in {"diagnoser", "single"}:
                projected.update(prompt_version=config_view["prompt_version"], known_good_prompt="healthy",
                                 release_history=[{"version": "healthy", "source": "system.v1.4.2.md", "policy": "7天无理由，激活仍可退，商家承担运费"},
                                                  {"version": "degraded", "source": "system.v1.4.3.b1.md", "policy": "激活不退，人工审核"}])
            if role in {"fixer", "single", "cost"}:
                projected.update({k: config_view[k] for k in ["context_multiplier", "max_output_tokens", "retry_limit", "retry_on_terminal"]})
                projected["known_good"] = {k: DEFAULT_CONFIG[k] for k in ["context_multiplier", "max_output_tokens", "retry_limit", "retry_on_terminal"]}
            if role == "verifier" and run:
                projected["acceptance_contract"] = run["contract"]
        events = self.store.events(run_id=rid, limit=2000) if rid else []
        observations = [{"id": e["event_id"], "type": e["event_type"], "summary": e["payload"].get("summary", ""), "payload": e["payload"]}
                        for e in events if e["event_type"].startswith("observation.")]
        candidate = None
        if run and "memory" in run["condition"] and not muted and rid not in self.pending_detection and role in {"fixer", "single"}:
            source = self.evaluation_memory if run["manifest"]["environment"] == "evaluation" else self.memory
            candidate = source.match(run["symptoms"], config_view)
            if candidate and (self.memory_candidates.get(run["id"]) or {}).get("id") != candidate["id"]:
                self.memory_candidates[run["id"]] = candidate
                self.store.emit("memory.selected", {"playbook_id": candidate["id"], "source": candidate["source"], "summary": "检索到 Playbook 候选，仍须模型决策和独立验收"}, run_id=run["id"], producer=aid)
        # No peer plan/rationale or task result is a back channel when muted.
        own_plan = run.get("plan") if run and not muted and (run.get("plan") or {}).get("holder") == aid and run["plan"].get("transport_epoch") == epoch else None
        last_action = None
        if run and run.get("last_action") and role in {"verifier", "single"}:
            last_action = {"id": run["last_action"]["id"], "revision": run["last_action"]["after_revision"]}
        return {"id": aid, "role": role, "paused": agent["paused"], "generation": config_view["generation"],
                "instance_id": agent["instance_id"], "transport_epoch": epoch,
                "config": projected, "observations": observations, "muted": muted, "detection_pending": rid in self.pending_detection,
                "incident": {k: run[k] for k in ["id", "run_id", "status", "opened_at", "condition"]} if run else None,
                "tasks": tasks, "messages": messages[-30:], "plan": own_plan, "last_action": last_action, "memory": candidate}

    async def verify(self, agent, run_id):
        if agent["role"] not in {"verifier", "single"}:
            raise Rejected("ROLE_FORBIDDEN", "角色不能发起独立验收", 403)
        if run_id in self.verifying:
            raise Rejected("VERIFY_RUNNING", "验收正在进行")
        run = self.store.run(run_id)
        if not run or run["status"] not in ACTIVE or not run["last_action"]:
            raise Rejected("NO_APPLIED_ACTION", "尚无可验收的配置修改")
        self.verifying.add(run_id)
        try:
            result = await self.victim.probe(suite="verify", run_id=run_id)
            result = self.store.verification(agent["id"], run_id, result)
            if result["passed"]:
                run = self.store.run(run_id)
                candidate = self.memory_candidates.get(run_id)
                if candidate and digest(candidate["actions"]) == digest(run["last_action"]["actions"]):
                    run["memory_used_id"], run["reuseapproved"] = candidate["id"], True
                    with self.store.tx() as db:
                        self.store.save(db, "runs", run)
                try:
                    destination = self.evaluation_results if run["manifest"]["environment"] == "evaluation" else self.memory
                    receipt = await asyncio.to_thread(destination.record_success, run, run["last_action"]["actions"])
                    self.store.emit("memory.recorded", {"summary": "已验证结果回写本地 GEP 资产", "receipt": receipt}, run_id=run_id, producer="memory")
                except Exception as exc:
                    self.store.emit("memory.failed", {"summary": "本地 GEP 回写失败", "error": str(exc)[:300]}, run_id=run_id, producer="memory")
            return result
        finally:
            self.verifying.discard(run_id)

    async def dependencies_loop(self):
        async with httpx.AsyncClient(timeout=3, trust_env=False) as client:
            while True:
                for name, url in [("langfuse", "http://127.0.0.1:9030/api/public/health"), ("collector", "http://127.0.0.1:9133/")]:
                    try:
                        response = await client.get(url)
                        good = response.status_code == 200
                        entry = {"status": "available" if good else "degraded", "detail": f"HTTP {response.status_code}", "checked_at": now()}
                    except Exception as exc:
                        entry = {"status": "degraded", "detail": type(exc).__name__, "checked_at": now()}
                    if name == "langfuse":
                        entry["url"] = "http://127.0.0.1:9030"
                    if name == "collector":
                        entry["last_received_at"] = self.last_collector_at
                    before = self.dependency_cache.get(name)
                    self.dependency_cache[name] = entry
                    if before and before["status"] != entry["status"]:
                        self.store.emit("dependency.changed", {"name": name, **entry, "summary": name + " " + entry["status"]})
                if self.attract_process and self.attract_process.poll() is None:
                    try:
                        response = await client.get("http://127.0.0.1:9020/api/state")
                        self.attract_status = response.json().get("attract", {})
                    except (httpx.HTTPError, ValueError):
                        self.attract_status = {"detail": "导览正在启动或暂时不可达"}
                await asyncio.sleep(8)

    def snapshot(self):
        runs = self.store.runs()
        with self.store.tx() as db:
            rid = self.store.meta(db, "current_run")
            state = {k: self.store.meta(db, k) for k in ["generation", "autonomy", "muted", "attract"]}
            state["memory_enabled"] = self.store.meta(db, "memory_enabled", False)
            agents = [json.loads(r[0]) for r in db.execute("SELECT data FROM agents")]
            maximum = db.execute("SELECT max(sequence) FROM events").fetchone()[0] or 0
        incident = next((r for r in runs if r["id"] == rid), None)
        dependencies = {k: dict(v) for k, v in self.dependency_cache.items()}
        if self.collector_ingest_error:
            dependencies["collector"] = {**dependencies.get("collector", {}), "status": "degraded",
                "detail": "控制端接收失败: " + self.collector_ingest_error, "last_received_at": self.last_collector_at}
        for agent in agents:
            agent.pop("instance_id", None)
            if agent.get("heartbeat_at") and not agent["paused"]:
                age = (datetime.now(timezone.utc) - datetime.fromisoformat(agent["heartbeat_at"])).total_seconds()
                if age > 8:
                    agent.update(status="offline", detail=f"心跳失联 {age:.0f} 秒")
        return {**state, "generation": str(state["generation"]), "monitoring": True, "config": self.store.current_config(),
                "agents": agents, "incident": incident, "runs": runs[:30], "victim": self.victim.status(),
                "probe_policy": {"interval_s": 30, "max_rounds_per_incident": 3, "idle_calls": 0},
                "metrics": {"total": len(runs), "success": sum(r["status"] == "resolved" for r in runs),
                            "failures": sum(r["status"] in {"failed", "reset"} for r in runs), "usage_tokens": sum(r["usage_tokens"] for r in runs)},
                "dependencies": {"model": self.model.status(), **dependencies,
                    "evomap": {"status": "local", "detail": "Section9 自建本地协作；官方 GEP SDK 资产", "memory": self.memory.status()},
                    "jev": {"status": "disabled", "detail": "P2：无真实压缩产物，未启用"}},
                "playbooks": self.memory.list_playbooks(), "events": self.store.events(after=max(0, maximum-100), limit=100),
                "attract": {"enabled": os.getenv("S9_ENVIRONMENT") == "attract" or bool(self.attract_process and self.attract_process.poll() is None),
                            "cycle": self.attract_status.get("cycle", self.attract_cycles),
                            "auto_running": self.attract_status.get("auto_running", self.attract_active),
                            "url": "http://127.0.0.1:9020", "isolation": "separate_process_database_memory"},
                "model": config.MODEL, "version": "0.1.0", "environment": os.getenv("S9_ENVIRONMENT", "demo")}

    def run_detail(self, rid):
        run = self.store.run(rid)
        source = self.store
        if not run and self.evaluation_store():
            source = self.evaluation_store()
            run = source.run(rid)
        if not run:
            raise Rejected("NOT_FOUND", "未找到运行记录", 404)
        with source.tx() as db:
            actions = [json.loads(r[0]) for r in db.execute("SELECT data FROM actions WHERE run_id=?", (rid,))]
        return {**run, "events": source.events(run_id=rid, limit=5000), "actions": actions, "usage": source.usage_records(rid)}

    def playbooks(self):
        with self.store.tx() as db:
            selections = [json.loads(row[0]) for row in db.execute("SELECT data FROM events WHERE json_extract(data,'$.event_type')='memory.selected'")]
        items = self.memory.list_playbooks()
        for item in items:
            item["hit_count"] = len({e["run_id"] for e in selections if e["payload"].get("playbook_id") == item["id"]})
        return items

    def evaluation_store(self):
        if os.getenv("S9_ENVIRONMENT") == "evaluation":
            return self.store
        path = config.ROOT / "data" / "evaluation" / "section9.sqlite"
        if not path.exists():
            return None
        store = Store.__new__(Store)
        store.path = path
        return store  # Used only for operator reads, never for agent context.

    def rca(self, rid):
        run = self.run_detail(rid)
        messages = [e for e in run["events"] if e["event_type"] == "dialog.received"]
        return {"run_id": rid, "title": "小智客服事故复盘", "status": run["status"],
                "summary": {"symptoms": run["symptoms"], "failure_reason": run["failure_reason"],
                            "model_reasoning": [e["payload"] for e in messages]}, "evidence": run["events"],
                "actions": run["actions"], "verification": run["verification"], "manifest": run["manifest"],
                "limitations": ["推理使用远程 EvoMap API；业务应用与协作服务运行于本机", "本地受信任操作者环境，非多租户安全沙箱"]}

    def scoreboard(self, version=None):
        rows = []
        source = self.evaluation_store()
        records = source.runs(10000) if source else []
        hash_fields = ["backend_source_hash", "frontend_build_hash", "dependency_lock_hash", "fixture_hash", "acceptance_contract_hash"]
        def version_of(identity):
            if not identity or any(not identity.get(k) for k in hash_fields):
                return "legacy"
            return digest({k: identity[k] for k in hash_fields})
        current_version = version_of(self.identity)
        selected_version = version or current_version
        versions = {current_version: {"id": current_version, "label": "当前运行版本 " + current_version[:10], "n": 0}}
        for record in records:
            key = version_of(record["manifest"].get("source_identity"))
            versions.setdefault(key, {"id": key, "label": "历史证据 · 缺少完整来源绑定" if key == "legacy" else "历史版本 " + key[:10], "n": 0})["n"] += 1
        records = [r for r in records if version_of(r["manifest"].get("source_identity")) == selected_version]
        conditions = [("single", "单 Agent"), ("muted", "蜂群禁言 · 通信依赖测试"), ("swarm", "蜂群 · LLM 协作"), ("memory", "蜂群 + Playbook"), ("memory_jev", "蜂群 + Playbook + Jev（未启用）")]
        if any(r["condition"] == "single_memory" for r in records):
            conditions.append(("single_memory", "单 Agent + Playbook（补充对照）"))
        for condition, label in conditions:
            samples = [r for r in records if r["condition"] == condition and r["status"] not in ACTIVE and r["manifest"].get("environment") == "evaluation"]
            n = len(samples)
            cells = []
            for scenario in ["prompt", "cost", "loop", "composite"]:
                subset = [r for r in samples if r["scenario"] == scenario]
                count = len(subset)
                cells.append({"scenario": scenario, "n": count,
                              "failures": sum(r["status"] != "resolved" for r in subset) if count else None,
                              "success_rate": sum(r["status"] == "resolved" for r in subset)/count if count else None,
                              "median_s": round(statistics.median(r["elapsed_s"] for r in subset), 3) if count else None,
                              "usage_tokens": sum(r["usage_tokens"] for r in subset) if count else None,
                              "unknown_usage_runs": sum(r["usage_unknown"] for r in subset) if count else None,
                              "token_budget": sorted({r["token_budget"] for r in subset}) or [config.RUN_TOKEN_BUDGET],
                              "run_ids": [r["id"] for r in subset]})
            rows.append({"id": condition, "label": label, "n": n, "failures": sum(r["status"] != "resolved" for r in samples) if n else None,
                         "success_rate": sum(r["status"] == "resolved" for r in samples)/n if n else None,
                         "median_s": statistics.median(r["elapsed_s"] for r in samples) if n else None,
                         "usage_tokens": sum(r["usage_tokens"] for r in samples) if n else None,
                         "unknown_usage_runs": sum(r["usage_unknown"] for r in samples) if n else None,
                         "conditions": [{"run_id": r["id"], "manifest": r["manifest"]} for r in samples],
                         "run_ids": [r["id"] for r in samples], "cells": cells})
        return {"rows": rows, "versions": list(versions.values()), "current_version": current_version, "selected_version": selected_version, "limitations": ["按后端、前端、依赖、fixture、验收合同哈希分组；历史缺少绑定的证据单列", "只统计独立 9024 进程及数据库中的 evaluation 运行；集成演练与导览不混入计分", "共享物理 Mac 与供应商；未控制供应商缓存，样本不足不能推断优势", "失败、重置、未知 usage 均保留；未知成本不算零成本"]}

    async def set_attract(self, enabled):
        if os.getenv("S9_ENVIRONMENT") == "attract":
            raise Rejected("ATTRACT_NESTING", "导览环境不能再创建导览", 403)
        if enabled and not (self.attract_process and self.attract_process.poll() is None):
            env = dict(os.environ)
            env.update(S9_DATA_DIR=str(config.DATA / "attract"), S9_PORT="9020", S9_ENVIRONMENT="attract")
            log = (self.runtime / "attract.log").open("ab")
            self.attract_process = subprocess.Popen([sys.executable, "-m", "uvicorn", "s9.api:app", "--host", "127.0.0.1", "--port", "9020"], cwd=config.ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
            log.close()
        elif not enabled and self.attract_process and self.attract_process.poll() is None:
            self.attract_process.terminate()
            await asyncio.to_thread(self.attract_process.wait, timeout=10)
        return {"enabled": enabled, "url": "http://127.0.0.1:9020", "limit": "3 real cycles per activation"}

    async def attract_loop(self):
        self.attract_active = True
        await asyncio.sleep(5)
        for scenario in ["prompt", "loop", "cost"]:
            cycle_start = time.monotonic()
            self.attract_cycles += 1
            await self.reset()
            result = await self.inject(scenario)
            while self.store.run(result["run_id"])["status"] in ACTIVE:
                await asyncio.sleep(2)
            await asyncio.sleep(max(2, 90 - (time.monotonic() - cycle_start)))
        self.store.emit("attract.completed", {"summary": "三轮真实导览完成；已停止自动模型调用"})
        self.attract_active = False
