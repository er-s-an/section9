from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def uid(prefix: str) -> str:
    return prefix + "_" + uuid.uuid4().hex[:16]


def encode(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value: Any) -> str:
    return hashlib.sha256(encode(value).encode()).hexdigest()


class Rejected(Exception):
    def __init__(self, code: str, message: str, status: int = 409):
        self.code, self.message, self.status = code, message, status
        super().__init__(message)


DEFAULT_CONFIG = {
    "prompt_version": "healthy", "context_multiplier": 1, "max_output_tokens": 1536,
    "retry_limit": 3, "retry_on_terminal": False,
}
ROLES = {
    "sentry": ("sentry", "哨兵", ["detect"]),
    "diagnoser": ("diagnoser", "诊断员", ["diagnose"]),
    "fixer-a": ("fixer", "修复员 A", ["repair"]),
    "fixer-b": ("fixer", "修复员 B", ["repair"]),
    "verifier": ("verifier", "复核员", ["verify"]),
    "cost": ("cost", "成本分析师", ["cost"]),
    "single": ("single", "单 Agent 基线", ["single"]),
}
ACTIVE = {"injected", "diagnosing", "repairing", "awaiting_approval", "verifying", "blocked"}


class Store:
    """Single-machine transactional authority. Workers never open this database."""

    def __init__(self, path: Path, *, scope=None, baseline_config=None):
        self.path = path
        self.scope = dict(scope or {})
        self.baseline_config = dict(baseline_config or DEFAULT_CONFIG)
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.tx() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS configs(revision INTEGER PRIMARY KEY,data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY,data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events(sequence INTEGER PRIMARY KEY AUTOINCREMENT,id TEXT UNIQUE,data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS agents(id TEXT PRIMARY KEY,token_hash TEXT NOT NULL,data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY,run_id TEXT NOT NULL,data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS messages(id TEXT PRIMARY KEY,run_id TEXT NOT NULL,data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS plans(id TEXT PRIMARY KEY,run_id TEXT NOT NULL,data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS grants(id TEXT PRIMARY KEY,data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS actions(id TEXT PRIMARY KEY,run_id TEXT NOT NULL,idem TEXT UNIQUE,data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS usage(id TEXT PRIMARY KEY,run_id TEXT,data TEXT NOT NULL);
            """)
            saved_scope = self.meta(db, "runtime_scope")
            if saved_scope is not None and saved_scope != self.scope:
                raise Rejected("SCOPE_MISMATCH", "数据库属于另一个运行作用域", 403)
            self.set_meta(db, "runtime_scope", self.scope)
            saved_baseline = self.meta(db, "frozen_baseline")
            if saved_baseline is not None and saved_baseline != self.baseline_config:
                raise Rejected("BASELINE_MISMATCH", "冻结的健康配置不能被替换", 403)
            self.set_meta(db, "frozen_baseline", self.baseline_config)
            if self.meta(db, "generation") is None:
                for k, v in {"generation": 1, "revision": 1, "autonomy": "L2", "autonomy_revision": 1,
                             "muted": False, "current_run": None, "resource_fence": 0,
                             "transport_epoch": 1, "attract": False}.items():
                    self.set_meta(db, k, v)
                db.execute("INSERT INTO configs VALUES(?,?)", (1, encode({**self.baseline_config, "revision": "1", "generation": "1"})))

    @contextmanager
    def tx(self):
        db = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA busy_timeout=15000")
        db.execute("BEGIN IMMEDIATE")
        try:
            yield db
            if db.in_transaction:
                db.commit()
        except BaseException:
            if db.in_transaction:
                db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def meta(db, key, default=None):
        r = db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(r[0]) if r else default

    @staticmethod
    def set_meta(db, key, value):
        db.execute("INSERT INTO meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, encode(value)))

    @staticmethod
    def get(db, table, key):
        r = db.execute(f"SELECT data FROM {table} WHERE id=?", (key,)).fetchone()
        return json.loads(r[0]) if r else None

    @staticmethod
    def save(db, table, item):
        db.execute(f"UPDATE {table} SET data=? WHERE id=?", (encode(item), item["id"]))

    def event(self, db, event_type, payload, *, run_id=None, producer="system", generation=None):
        scope = getattr(self, "scope", {})
        if scope and run_id and run_id != scope['run_id']:
            # Preserve the attempted foreign ID as rejection evidence, never as
            # a valid foreign-run event in this authority.
            payload = {**payload, 'attempted_run_id': run_id}
            run_id = scope['run_id']
        if scope and run_id is None and (event_type.startswith('agent.') or event_type == 'system.reset'):
            run_id = scope['run_id']
        current = self.current_config(db)
        actor = self.get(db, 'agents', producer)
        event = {**scope, 'scope': 'run' if run_id else 'system',
                 'config_revision': current['revision'],
                 'config_hash': digest({k: current[k] for k in DEFAULT_CONFIG}),
                 'agent_instance_id': actor.get('instance_id') if actor else None,
                 "event_id": uid("ev"), "sequence": "0", "event_type": event_type,
                 "occurred_at": now(), "run_id": run_id, "incident_id": run_id,
                 "producer": producer, "generation": str(generation or self.meta(db, "generation")), "payload": payload}
        cur = db.execute("INSERT INTO events(id,data) VALUES(?,?)", (event["event_id"], encode(event)))
        event["sequence"] = str(cur.lastrowid)
        db.execute("UPDATE events SET data=? WHERE sequence=?", (encode(event), cur.lastrowid))
        return event

    def emit(self, event_type, payload, *, run_id=None, producer="system"):
        with self.tx() as db:
            return self.event(db, event_type, payload, run_id=run_id, producer=producer)

    def events(self, *, after=0, run_id=None, limit=300):
        with self.tx() as db:
            if run_id:
                rows = db.execute("SELECT data FROM events WHERE sequence>? AND json_extract(data,'$.run_id')=? ORDER BY sequence LIMIT ?", (after, run_id, limit)).fetchall()
            else:
                rows = db.execute("SELECT data FROM events WHERE sequence>? ORDER BY sequence LIMIT ?", (after, limit)).fetchall()
            return [json.loads(r[0]) for r in rows]

    def current_config(self, db=None):
        if db is None:
            with self.tx() as conn:
                return self.current_config(conn)
        rev = self.meta(db, "revision")
        return json.loads(db.execute("SELECT data FROM configs WHERE revision=?", (rev,)).fetchone()[0])

    def set_config(self, db, values):
        rev = self.meta(db, "revision") + 1
        config = {**self.current_config(db), **values, "revision": str(rev), "generation": str(self.meta(db, "generation"))}
        db.execute("INSERT INTO configs VALUES(?,?)", (rev, encode(config)))
        self.set_meta(db, "revision", rev)
        return config

    def register(self, agent_id):
        role, name, capabilities = ROLES[agent_id]
        token = secrets.token_urlsafe(32)
        with self.tx() as db:
            record = {**getattr(self, "scope", {}), "id": agent_id, "role": role, "name": name, "capabilities": capabilities,
                      "status": "starting", "detail": "进程启动中", "heartbeat_at": None,
                      "paused": False, "task_id": None, "instance_id": uid("instance")}
            db.execute("INSERT INTO agents VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET token_hash=excluded.token_hash,data=excluded.data", (agent_id, hashlib.sha256(token.encode()).hexdigest(), encode(record)))
            self.event(db, "agent.joined", {"summary": name + "已加入能力目录", "capabilities": capabilities}, producer=agent_id)
        return token

    def authenticate(self, token):
        h = hashlib.sha256(token.encode()).hexdigest()
        with self.tx() as db:
            r = db.execute("SELECT data FROM agents WHERE token_hash=?", (h,)).fetchone()
            if not r:
                raise Rejected("UNAUTHENTICATED", "无效的 Agent 身份", 401)
            return json.loads(r[0])

    def heartbeat(self, agent_id, status, detail, task_id=None):
        with self.tx() as db:
            a = self.get(db, "agents", agent_id)
            if not a:
                raise Rejected("UNKNOWN_AGENT", "Agent 未注册", 404)
            old = (a.get("status"), a.get("detail"))
            a.update(heartbeat_at=now(), task_id=task_id)
            if not a["paused"]:
                a.update(status=status, detail=detail)
            self.save(db, "agents", a)
            if old != (a.get("status"), a.get("detail")):
                self.event(db, "agent.status", {"status": a["status"], "summary": a["detail"], "task_id": task_id}, producer=agent_id,
                           run_id=self.meta(db, "current_run"))
            return {"ok": True, "paused": a["paused"]}

    def pause(self, agent_id, paused):
        with self.tx() as db:
            a = self.get(db, "agents", agent_id)
            if not a:
                raise Rejected("UNKNOWN_AGENT", "Agent 未加入", 404)
            a.update(paused=paused, status="paused" if paused else "resuming", detail="暂停：停止续租，保留旧执行凭证" if paused else "恢复：检查保留的任务与令牌")
            self.save(db, "agents", a)
            self.event(db, "agent.paused" if paused else "agent.resumed", {"summary": a["detail"]}, producer=agent_id, run_id=self.meta(db, "current_run"))
            return a

    def inject(self, scenario, condition, model, seed, token_budget, *, run_id=None):
        mutations = {"prompt": {"prompt_version": "degraded"},
                     "cost": {"context_multiplier": 8, "max_output_tokens": 4096},
                     "loop": {"retry_limit": 6, "retry_on_terminal": True},
                     "composite": {"prompt_version": "degraded", "retry_limit": 6, "retry_on_terminal": True}}
        with self.tx() as db:
            old = self.get(db, "runs", self.meta(db, "current_run"))
            if old and old["status"] in ACTIVE:
                raise Rejected("RUN_ACTIVE", "请先完成当前事故或重置")
            scope = getattr(self, "scope", {})
            run_id = run_id or scope.get('run_id') or uid("run")
            if scope and run_id != scope['run_id']:
                raise Rejected('SCOPE_MISMATCH', '不允许替换固定的 arm run', 403)
            if self.get(db, 'runs', run_id):
                raise Rejected('RUN_REUSE_FORBIDDEN', '旧 run 不能重新注入，重跑需创建新 Pair')
            config = self.set_config(db, {**getattr(self, 'baseline_config', DEFAULT_CONFIG), **mutations[scenario]})
            contract = {"version": "2", "suite": "xiaozhi-heldout-v2",
                        "cost_limits": {"input": 2000, "output": 1536, "total": 3536},
                        "answer_consistency": True, "negative_boundary": True, "whole_run_budget": True, "semantic": True, "unaffected_product": True,
                        "no_stalled_requests": True, "same_revision": True, "bounded_cost": True}
            run = {**scope, "id": run_id, "run_id": run_id, "scenario": scenario, "condition": condition, "seed": seed,
                   "status": "injected", "generation": str(self.meta(db, "generation")), "injected_revision": config["revision"],
                   "initial_config_revision": "1", "initial_config_hash": digest(getattr(self, 'baseline_config', DEFAULT_CONFIG)),
                   "injected_config_hash": digest({k: config[k] for k in DEFAULT_CONFIG}),
                   "opened_at": now(), "closed_at": None, "start_mono": time.monotonic(), "elapsed_s": None,
                   "symptoms": [], "model": model, "plan": None, "verification": None, "last_action": None,
                   "contract": contract, "contract_hash": digest(contract), "usage_tokens": 0, "reserved_tokens": 0,
                   "token_budget": token_budget, "usage_unknown": False, "failure_reason": None,
                   "manifest": {**scope, "model": model, "condition": condition, "scenario_seed": seed, "total_token_budget": token_budget,
                                "request_concurrency_limit": 2, "cache_condition": "not_controlled_provider_cache", "memory_condition": "seeded" if "memory" in condition else "off",
                                "transport_mode": "local", "jev_enabled": False, "fixture_version": "xiaozhi-s9-v1", "verification_suite_hash": digest(contract),
                                "environment": "demo", "evidence_origin": "live", "hardware_isolation": False}}
            db.execute("INSERT INTO runs VALUES(?,?)", (run_id, encode(run)))
            self.set_meta(db, "current_run", run_id)
            if self.meta(db, "muted") != (condition == "muted"):
                self.set_meta(db, "transport_epoch", self.meta(db, "transport_epoch") + 1)
            self.set_meta(db, "muted", condition == "muted")
            self.event(db, "chaos.injected", {"scenario": scenario, "revision": config["revision"], "summary": "故障配置已真实生效，等待独立探针"}, run_id=run_id, producer="operator")
            return run

    def open_incident(self, observation_ids):
        with self.tx() as db:
            run = self.get(db, "runs", self.meta(db, "current_run"))
            if not run or run["status"] not in ACTIVE:
                return {"opened": False}
            symptoms = set(run["symptoms"])
            for eid in observation_ids:
                row = db.execute("SELECT data FROM events WHERE id=?", (eid,)).fetchone()
                if row:
                    e = json.loads(row[0])
                    if e["run_id"] == run["id"] and e["generation"] == run["generation"]:
                        signal = e["payload"].get("signal")
                        if signal in {"quality_mismatch", "cost_high", "tool_stalled"}:
                            symptoms.add(signal)
            if not symptoms:
                return {"opened": False}
            first = not run["symptoms"]
            run["symptoms"] = sorted(symptoms)
            if first:
                run["status"] = "diagnosing"
                kinds = ["single"] if run["condition"].startswith("single") else ["diagnose", "repair", "verify"]
                if "cost_high" in symptoms and self.get(db, "agents", "cost") and not run["condition"].startswith("single") and run["manifest"]["environment"] != "evaluation":
                    kinds.append("cost")
                for kind in kinds:
                    self._new_task(db, run["id"], kind)
            self.save(db, "runs", run)
            self.event(db, "incident.opened" if first else "incident.updated", {"symptoms": run["symptoms"], "evidence_ids": observation_ids, "summary": "哨兵根据实际观测立案" if first else "哨兵补充新线索"}, run_id=run["id"], producer="sentry")
            return {"opened": first, "run_id": run["id"]}

    def _new_task(self, db, run_id, kind):
        task = {**getattr(self, "scope", {}), "id": uid("task"), "run_id": run_id, "kind": kind, "status": "available", "epoch": "0", "holder": None, "lease_deadline": 0.0, "result_summary": None,
                "generation": self.get(db, "runs", run_id)["generation"], "holder_instance_id": None}
        db.execute("INSERT INTO tasks VALUES(?,?,?)", (task["id"], run_id, encode(task)))
        return task

    def claim(self, agent_id, task_id):
        with self.tx() as db:
            a = self.get(db, "agents", agent_id)
            t = self.get(db, "tasks", task_id)
            if not t or t["kind"] not in a["capabilities"]:
                raise Rejected("ROLE_FORBIDDEN", "该角色没有此任务能力", 403)
            run = self.get(db, "runs", t["run_id"])
            if a["paused"] or not run or run["status"] not in ACTIVE or run["generation"] != str(self.meta(db, "generation")):
                raise Rejected("TASK_UNAVAILABLE", "任务或身份目前不可执行")
            if t["status"] == "completed" or (t["status"] == "claimed" and t["lease_deadline"] > time.time()):
                raise Rejected("ALREADY_CLAIMED", "任务仍由有效租约持有")
            old_holder = t["holder"]
            t.update(holder=agent_id, holder_instance_id=a["instance_id"], epoch=str(int(t["epoch"]) + 1), status="claimed", lease_deadline=time.time() + 8)
            if t["kind"] in {"repair", "single"}:
                fence = self.meta(db, "resource_fence") + 1
                self.set_meta(db, "resource_fence", fence)
                t["resource_fence"] = str(fence)
            self.save(db, "tasks", t)
            self.event(db, "task.claimed", {"task_id": task_id, "kind": t["kind"], "epoch": t["epoch"], "previous_holder": old_holder,
                                           "summary": f"自主认领 {t['kind']}，epoch {t['epoch']}"}, run_id=t["run_id"], producer=agent_id)
            return {"task_id": task_id, "epoch": t["epoch"], "lease_deadline": t["lease_deadline"], "run_id": t["run_id"]}

    def renew(self, agent_id, task_id, epoch):
        with self.tx() as db:
            t = self.get(db, "tasks", task_id)
            a = self.get(db, "agents", agent_id)
            if not t or a["paused"] or t["status"] != "claimed" or t["holder"] != agent_id or t["epoch"] != epoch or t["lease_deadline"] <= time.time():
                raise Rejected("LEASE_EXPIRED", "租约无效，不能续租")
            run = self.get(db, "runs", t["run_id"])
            self._valid_task(db, a, t, run, epoch)
            t["lease_deadline"] = time.time() + 8
            self.save(db, "tasks", t)
            return {"ok": True}

    def complete_task(self, agent_id, task_id, epoch, summary):
        with self.tx() as db:
            t = self.get(db, "tasks", task_id)
            run = self.get(db, "runs", t["run_id"]) if t else None
            self._valid_task(db, self.get(db, "agents", agent_id), t, run, epoch)
            t.update(status="completed", result_summary=summary[:1000])
            self.save(db, "tasks", t)
            self.event(db, "task.completed", {"task_id": task_id, "summary": summary[:500]}, producer=agent_id, run_id=t["run_id"])
            if summary.startswith("failed:"):
                run.update(status="failed", failure_reason="WORKER_TASK_FAILED", closed_at=now(),
                           elapsed_s=round(time.monotonic() - run["start_mono"], 3))
                self.save(db, "runs", run)
                self._revoke_run(db, run["id"], "WORKER_TASK_FAILED")
                self.event(db, "incident.failed", {"code": "WORKER_TASK_FAILED", "summary": summary[:500]}, run_id=run["id"], producer=agent_id)
            return {"ok": True}

    def _valid_context(self, db, agent, run, item):
        if item.get("generation") != run["generation"]:
            raise Rejected("RESET_GENERATION_STALE", "消息或方案属于旧轮次")
        if item.get("instance_id") != agent["instance_id"]:
            raise Rejected("INSTANCE_STALE", "工作进程身份已换代")
        if item.get("transport_epoch") != str(self.meta(db, "transport_epoch")):
            raise Rejected("CONTEXT_STALE", "推理开始时的通信上下文已失效")

    def message(self, agent_id, item):
        try:
            with self.tx() as db:
                run = self.get(db, "runs", item["run_id"])
                agent = self.get(db, "agents", agent_id)
                task = self.get(db, "tasks", item.get("task_id"))
                self._valid_task(db, agent, task, run, item.get("task_epoch"))
                self._valid_context(db, agent, run, item)
                msg = {**item, **getattr(self, "scope", {}), "id": uid("msg"), "sender": agent_id, "at": now()}
                if self.meta(db, "muted"):
                    self.event(db, "dialog.dropped", {"message_id": msg["id"], "kind": msg["kind"], "summary": "通信已禁言，消息未交付", "reason": "MUTED"}, run_id=run["id"], producer=agent_id)
                    return {"id": msg["id"], "delivered": False}
                db.execute("INSERT INTO messages VALUES(?,?,?)", (msg["id"], run["id"], encode(msg)))
                self.event(db, "dialog.received", {**msg, "summary": msg["content"]}, run_id=run["id"], producer=agent_id)
                return {"id": msg["id"], "delivered": True}
        except Rejected as exc:
            self.emit("dialog.rejected", {"code": exc.code, "summary": exc.message, "task_id": item.get("task_id")},
                      run_id=item.get("run_id"), producer=agent_id)
            raise

    @staticmethod
    def _plan_hash(plan):
        return digest({k: plan.get(k) for k in ["pair_id", "arm", "spec_hash", "run_id", "holder", "instance_id", "actions", "expected_revision",
                      "expected_config_hash", "generation", "task_id", "task_epoch", "transport_epoch", "resource_fence"]})

    def create_plan(self, agent_id, item):
        with self.tx() as db:
            a = self.get(db, "agents", agent_id)
            if a["role"] not in {"fixer", "single"}:
                raise Rejected("ROLE_FORBIDDEN", "该角色不能提案执行", 403)
            run = self.get(db, "runs", item["run_id"])
            task = self.get(db, "tasks", item["task_id"])
            self._valid_task(db, a, task, run, item["task_epoch"])
            self._valid_context(db, a, run, item)
            if item["expected_revision"] != self.current_config(db)["revision"]:
                raise Rejected("PRECONDITION_FAILED", "提案依据的配置版本已变化")
            updates = self._action_updates(item["actions"])
            if not updates:
                raise Rejected("EMPTY_PLAN", "方案未包含配置变更", 422)
            plan = {**item, **getattr(self, "scope", {}), "id": uid("plan"), "holder": agent_id, "status": "proposed", "approved": False,
                    "generation": run["generation"], "resource_fence": str(self.meta(db, "resource_fence")), "created_at": now(),
                    "autonomy_revision": str(self.meta(db, "autonomy_revision")), "transport_epoch": str(self.meta(db, "transport_epoch"))}
            plan["expected_config_hash"] = digest({k: self.current_config(db)[k] for k in DEFAULT_CONFIG})
            plan["hash"] = self._plan_hash(plan)
            db.execute("INSERT INTO plans VALUES(?,?,?)", (plan["id"], run["id"], encode(plan)))
            run.update(plan=plan, status="awaiting_approval" if self.meta(db, "autonomy") != "L2" else "repairing")
            self.save(db, "runs", run)
            self.event(db, "plan.proposed", {"plan_id": plan["id"], "hash": plan["hash"], "actions": plan["actions"], "summary": plan["rationale"]}, run_id=run["id"], producer=agent_id)
            return {"id": plan["id"], "hash": plan["hash"], "status": plan["status"]}

    @staticmethod
    def _action_updates(actions):
        updates = {}
        allowed = set(DEFAULT_CONFIG)
        types = {"set_prompt_revision": {"prompt_version"}, "apply_context_budget": {"context_multiplier", "max_output_tokens"},
                 "apply_retry_policy": {"retry_limit", "retry_on_terminal"}, "apply_config_bundle": allowed,
                 "rollback_action": allowed}
        for action in actions:
            kind, values = action.get("type"), action.get("values", {})
            if kind not in types or not isinstance(values, dict) or set(values) - types[kind]:
                raise Rejected("POLICY_DENIED", "动作或字段不在白名单内", 403)
            for k, v in values.items():
                if k == "prompt_version" and v not in {"healthy", "degraded"}:
                    raise Rejected("POLICY_DENIED", "未知 prompt 版本", 403)
                if k == "retry_on_terminal" and not isinstance(v, bool):
                    raise Rejected("POLICY_DENIED", "重试策略必须是布尔值", 403)
                bounds = {"context_multiplier": (1, 8), "max_output_tokens": (64, 4096), "retry_limit": (0, 6)}
                if k in bounds and (type(v) is not int or not bounds[k][0] <= v <= bounds[k][1]):
                    raise Rejected("POLICY_DENIED", "配置值超出允许范围", 403)
            updates.update(values)
        return updates

    def _valid_scope(self, *items):
        scope = getattr(self, 'scope', {})
        if scope and any(item is not None and any(item.get(k) != v for k, v in scope.items()) for item in items):
            raise Rejected('SCOPE_MISMATCH', '对象不属于此 Pair / arm / run 权威', 403)

    def _valid_task(self, db, agent, task, run, epoch):
        self._valid_scope(agent, task, run)
        if not run or run["generation"] != str(self.meta(db, "generation")):
            raise Rejected("RESET_GENERATION_STALE", "上轮执行身份已被重置作废")
        if task and (task["run_id"] != run["id"] or task.get("generation") != run["generation"]):
            raise Rejected("TASK_RUN_MISMATCH", "任务租约不属于当前事故与轮次")
        if not agent or not task or task["epoch"] != epoch or task["holder"] != agent["id"] or task.get("holder_instance_id") != agent["instance_id"]:
            raise Rejected("FENCE_STALE", "旧任务 epoch 或执行身份已失效")
        if task["status"] != "claimed" or task["lease_deadline"] < time.time():
            raise Rejected("LEASE_EXPIRED", "执行租约已到期")
        if run["status"] not in ACTIVE:
            raise Rejected("RUN_CLOSED", "事故已经结案")
        if agent["paused"]:
            raise Rejected("AGENT_PAUSED", "暂停的执行者不能获得新权限")

    def grant(self, agent_id, plan_id):
        try:
            return self._grant(agent_id, plan_id)
        except Rejected as exc:
            with self.tx() as db:
                plan = self.get(db, "plans", plan_id)
                self.event(db, "grant.rejected", {"code": exc.code, "summary": exc.message,
                           "plan_id": plan_id, "revision": self.current_config(db)["revision"]},
                           run_id=plan.get("run_id") if plan else None, producer=agent_id)
            raise

    def _grant(self, agent_id, plan_id):
        with self.tx() as db:
            a = self.get(db, "agents", agent_id)
            p = self.get(db, "plans", plan_id)
            if not p or p["holder"] != agent_id or a["role"] not in {"fixer", "single"}:
                raise Rejected("ROLE_FORBIDDEN", "执行授权不属于此身份", 403)
            run = self.get(db, "runs", p["run_id"])
            task = self.get(db, "tasks", p["task_id"])
            self._valid_task(db, a, task, run, p["task_epoch"])
            self._valid_context(db, a, run, p)
            self._autonomy_check(db, p)
            if p["transport_epoch"] != str(self.meta(db, "transport_epoch")):
                raise Rejected("CONTEXT_STALE", "通信上下文已变更，方案必须重新生成")
            if p["hash"] != self._plan_hash(p):
                raise Rejected("GRANT_MISMATCH", "方案内容与审批哈希不符", 403)
            if p["expected_revision"] != self.current_config(db)["revision"]:
                raise Rejected("PRECONDITION_FAILED", "授权时配置版本已变化")
            g = {**getattr(self, "scope", {}), "id": uid("grant"), "plan_id": plan_id, "plan_hash": p["hash"], "holder": agent_id,
                 "instance_id": a["instance_id"], "task_id": p["task_id"], "task_epoch": p["task_epoch"],
                 "resource_fence": p["resource_fence"], "generation": p["generation"], "run_id": run["id"],
                 "expected_revision": p["expected_revision"], "expected_config_hash": p.get("expected_config_hash"), "autonomy_revision": str(self.meta(db, "autonomy_revision")),
                 "transport_epoch": str(self.meta(db, "transport_epoch")), "contract_hash": run["contract_hash"], "expires_at": time.time() + 60}
            db.execute("INSERT INTO grants VALUES(?,?)", (g["id"], encode(g)))
            self.event(db, "execution.granted", {"grant_id": g["id"], "plan_id": plan_id, "epoch": g["task_epoch"], "fence": g["resource_fence"], "summary": "确定性策略签发有限执行授权"}, run_id=run["id"], producer=agent_id)
            pause = bool(run.get("fencing_demo") and not run.get("fencing_paused"))
            if pause:
                run.update(fencing_paused=True, fencing_original=agent_id, fencing_pause_at=time.time())
                self.save(db, "runs", run)
                a.update(paused=True, status="paused", detail="故障注入：已持有旧令牌，进程停止推进与续租")
                self.save(db, "agents", a)
                self.event(db, "agent.paused", {"grant_id": g["id"], "summary": a["detail"]}, run_id=run["id"], producer=agent_id)
            return {**g, "grant_id": g["id"], "pause_before_execute": pause}

    def _autonomy_check(self, db, plan):
        level = self.meta(db, "autonomy")
        if level == "L0":
            raise Rejected("POLICY_DENIED", "L0 仅建议，禁止修改应用", 403)
        if level == "L1" and (not plan.get("approved") or plan.get("approval_revision") != str(self.meta(db, "autonomy_revision"))):
            raise Rejected("APPROVAL_REQUIRED", "L1 需要操作者批准此确切方案", 403)

    def execute(self, agent_id, plan_id, grant_id, idempotency_key):
        rejection = None
        result = None
        with self.tx() as db:
            p = self.get(db, "plans", plan_id)
            g = self.get(db, "grants", grant_id)
            run_id = p.get("run_id") if p else None
            try:
                a = self.get(db, "agents", agent_id)
                if not a or a["role"] not in {"fixer", "single"} or not p or not g or g["holder"] != agent_id or p["holder"] != agent_id:
                    raise Rejected("ROLE_FORBIDDEN", "写入口拒绝非授权身份", 403)
                if g["generation"] != str(self.meta(db, "generation")):
                    raise Rejected("RESET_GENERATION_STALE", "旧轮次令牌已失效")
                run = self.get(db, "runs", run_id)
                task = self.get(db, "tasks", g["task_id"])
                if g["generation"] != str(self.meta(db, "generation")):
                    raise Rejected("RESET_GENERATION_STALE", "旧轮次令牌已失效")
                if not task or g["task_epoch"] != task["epoch"] or g["resource_fence"] != str(self.meta(db, "resource_fence")):
                    raise Rejected("FENCE_STALE", "旧执行者被新的 epoch/fence 拒绝")
                self._valid_scope(p, g)
                self._valid_task(db, a, task, run, g["task_epoch"])
                if g.get("revoked_at") or g["run_id"] != run["id"] or p["generation"] != run["generation"] or p["task_id"] != g["task_id"] or p["task_epoch"] != g["task_epoch"] or p.get("instance_id") != a["instance_id"] or g["plan_id"] != plan_id or g["plan_hash"] != p["hash"] or p["hash"] != self._plan_hash(p) or g["instance_id"] != a["instance_id"] or g["contract_hash"] != run["contract_hash"]:
                    raise Rejected("GRANT_MISMATCH", "授权绑定内容不符", 403)
                if g["expires_at"] <= time.time():
                    raise Rejected("GRANT_EXPIRED", "执行令牌已经过期")
                if g["autonomy_revision"] != str(self.meta(db, "autonomy_revision")) or g["transport_epoch"] != str(self.meta(db, "transport_epoch")):
                    raise Rejected("POLICY_CHANGED", "自治或通信策略已变更，旧令牌失效")
                self._autonomy_check(db, p)
                request_hash = digest([agent_id, plan_id, grant_id])
                old = db.execute("SELECT data FROM actions WHERE idem=?", (idempotency_key,)).fetchone()
                if old:
                    data = json.loads(old[0])
                    if data["request_hash"] != request_hash:
                        raise Rejected("IDEMPOTENCY_CONFLICT", "幂等键已被不同请求使用")
                    return {"action_id": data["id"], "revision": data["after_revision"], "status": "applied", "replayed_receipt": True}
                before = self.current_config(db)
                if before["revision"] != g["expected_revision"] or (g.get("expected_config_hash") and digest({k: before[k] for k in DEFAULT_CONFIG}) != g["expected_config_hash"]):
                    raise Rejected("PRECONDITION_FAILED", "实际配置版本与授权不符")
                updates = self._action_updates(p["actions"])
                after = self.set_config(db, updates)
                action = {**getattr(self, "scope", {}), "id": uid("act"), "run_id": run_id, "plan_id": plan_id, "holder": agent_id,
                          "before_revision": before["revision"], "after_revision": after["revision"],
                          "before": before, "after": after, "actions": p["actions"], "at": now(), "request_hash": request_hash}
                db.execute("INSERT INTO actions VALUES(?,?,?,?)", (action["id"], run_id, idempotency_key, encode(action)))
                p["status"] = "applied"
                self.save(db, "plans", p)
                run.update(status="verifying", plan=p, last_action=action, action_at=now())
                self.save(db, "runs", run)
                self.event(db, "action.applied", {"action_id": action["id"], "before_revision": before["revision"], "after_revision": after["revision"], "actions": p["actions"], "summary": "配置已修改，等待独立业务验收"}, producer=agent_id, run_id=run_id)
                result = {"action_id": action["id"], "revision": after["revision"], "status": "applied"}
            except Rejected as exc:
                rejection = exc
                self.event(db, "action.rejected", {"grant_id": grant_id, "plan_id": plan_id, "code": exc.code,
                                                  "summary": exc.message, "old_epoch": g.get("task_epoch") if g else None,
                                                  "old_fence": g.get("resource_fence") if g else None,
                                                  "current_fence": str(self.meta(db, "resource_fence")), "revision": self.current_config(db)["revision"]}, run_id=run_id, producer=agent_id)
        if rejection:
            raise rejection
        return result

    def _verification_authority(self, db, agent_id, item):
        agent = self.get(db, "agents", agent_id)
        if not agent or agent["role"] not in {"verifier", "single"}:
            raise Rejected("ROLE_FORBIDDEN", "只有复核入口可提交独立验收", 403)
        run = self.get(db, "runs", item["run_id"])
        task = self.get(db, "tasks", item["task_id"])
        self._valid_task(db, agent, task, run, item["task_epoch"])
        self._valid_context(db, agent, run, item)
        expected_kind = "single" if agent["role"] == "single" else "verify"
        if task["kind"] != expected_kind:
            raise Rejected("VERIFY_TASK_REQUIRED", "验收必须持有本角色的验收任务", 403)
        if not run.get("last_action"):
            raise Rejected("NO_APPLIED_ACTION", "尚无可验收的动作")
        return run

    def begin_verification(self, agent, item):
        """Capture server-owned authority and the exact object being tested."""
        with self.tx() as db:
            if item["instance_id"] != agent["instance_id"]:
                raise Rejected("INSTANCE_STALE", "验收请求不属于认证进程")
            run = self._verification_authority(db, agent["id"], item)
            config = self.current_config(db)
            if item["expected_revision"] != config["revision"] or config["revision"] != run["last_action"]["after_revision"]:
                raise Rejected("PRECONDITION_FAILED", "验收对象的配置版本已变化")
            job = {**item, **self.scope, "id": uid("verify_job"), "agent_id": agent["id"],
                   "config_hash": digest({k: config[k] for k in DEFAULT_CONFIG}),
                   "contract_hash": run["contract_hash"], "action_id": run["last_action"]["id"]}
            self.event(db, "verify.started", {**job, "summary": "独立验收开始", "suite": "verify"},
                       run_id=run["id"], producer=agent["id"])
            return job

    def verification(self, agent_id, run_id, result, *, job):
        with self.tx() as db:
            if job["agent_id"] != agent_id or job["run_id"] != run_id:
                raise Rejected("VERIFY_JOB_MISMATCH", "验收授权对象不匹配", 403)
            self._valid_scope(job)
            run = self._verification_authority(db, agent_id, job)
            if job["contract_hash"] != run["contract_hash"] or job["action_id"] != run["last_action"]["id"]:
                raise Rejected("VERIFY_JOB_STALE", "验收契约或被测动作已变化")
            config = self.current_config(db)
            result = {**result, "id": uid("verify"), "job_id": job["id"], "authority": job,
                      "contract_hash": run["contract_hash"], "at": now()}
            same = str(result.get("tested_revision")) == config["revision"] == run["last_action"]["after_revision"]
            same = same and config["revision"] == job["expected_revision"]
            same = same and result.get('tested_config_hash') == job["config_hash"] == digest({k: config[k] for k in DEFAULT_CONFIG})
            result['current_config_hash'] = digest({k: config[k] for k in DEFAULT_CONFIG})
            result.setdefault("checks", []).append({"name": "revision_unchanged_at_close", "passed": same, "expected": result.get("tested_revision"), "actual": config["revision"]})
            budget_ok = not run.get("usage_unknown") and not run.get("reserved_tokens") and run["usage_tokens"] <= run["token_budget"]
            result["checks"].append({"name": "whole_run_budget", "passed": budget_ok,
                                     "expected": run["token_budget"], "actual": {"known_tokens": run["usage_tokens"],
                                     "unknown": run.get("usage_unknown"), "reserved": run.get("reserved_tokens", 0)}})
            required = {"heldout_semantic_policy", "unaffected_product_fact", "terminal_tool_stops", "cost_budget", "no_stalled_requests", "heldout_outside_return_window"}
            complete = required.issubset({c["name"] for c in result["checks"]})
            result["checks"].append({"name": "acceptance_contract_complete", "passed": complete, "expected": sorted(required), "actual": sorted(c["name"] for c in result["checks"])})
            result["passed"] = bool(result.get("passed")) and same and complete and all(c.get("passed") for c in result["checks"])
            run["verification"] = result
            if result["passed"]:
                run.update(status="resolved", closed_at=now(), elapsed_s=round(time.monotonic() - run["start_mono"], 3))
            else:
                run.update(status="failed", closed_at=now(), elapsed_s=round(time.monotonic() - run["start_mono"], 3), failure_reason="VERIFICATION_FAILED")
            self.save(db, "runs", run)
            self._revoke_run(db, run_id, run["status"])
            self.event(db, "verification.completed", {"verification_id": result["id"], "passed": result["passed"], "checks": result["checks"], "tested_revision": result.get("tested_revision"), "summary": "独立业务验收通过" if result["passed"] else "独立验收失败，事故未解决"}, run_id=run_id, producer=agent_id)
            self.event(db, "incident.closed" if result["passed"] else "incident.failed", {"elapsed_s": run["elapsed_s"], "verification_id": result["id"], "summary": "业务恢复，事故结案" if result["passed"] else "验收失败已保留记录"}, run_id=run_id, producer="authority")
            return result

    def set_autonomy(self, level):
        with self.tx() as db:
            self.set_meta(db, "autonomy", level)
            self.set_meta(db, "autonomy_revision", self.meta(db, "autonomy_revision") + 1)
            self.event(db, "policy.autonomy", {"level": level, "summary": f"自治级别切换为 {level}，旧令牌作废"}, producer="operator", run_id=self.meta(db, "current_run"))
            return {"level": level}

    def set_muted(self, muted):
        with self.tx() as db:
            self.set_meta(db, "muted", muted)
            self.set_meta(db, "transport_epoch", self.meta(db, "transport_epoch") + 1)
            self.event(db, "policy.communication", {"muted": muted, "summary": "协作通信已禁言" if muted else "协作通信已恢复"}, producer="operator", run_id=self.meta(db, "current_run"))
            return {"muted": muted}

    def approve(self, plan_id):
        with self.tx() as db:
            p = self.get(db, "plans", plan_id)
            if not p or p["expected_revision"] != self.current_config(db)["revision"] or p["generation"] != str(self.meta(db, "generation")):
                raise Rejected("PRECONDITION_FAILED", "批准的方案已过期")
            run = self.get(db, "runs", p["run_id"])
            self._valid_task(db, self.get(db, "agents", p["holder"]), self.get(db, "tasks", p["task_id"]), run, p["task_epoch"])
            if p["resource_fence"] != str(self.meta(db, "resource_fence")) or p["transport_epoch"] != str(self.meta(db, "transport_epoch")):
                raise Rejected("CONTEXT_STALE", "批准依据的执行者或通信上下文已变化")
            if p["hash"] != self._plan_hash(p):
                raise Rejected("GRANT_MISMATCH", "方案内容与原始哈希不符", 403)
            p.update(approved=True, approval_revision=str(self.meta(db, "autonomy_revision")), status="approved")
            self.save(db, "plans", p)
            run = self.get(db, "runs", p["run_id"])
            run.update(plan=p, status="repairing")
            self.save(db, "runs", run)
            self.event(db, "plan.approved", {"plan_id": plan_id, "hash": p["hash"], "summary": "操作者批准此版本与方案哈希"}, run_id=p["run_id"], producer="operator")
            return {"approved": True}

    def _revoke_run(self, db, run_id, reason):
        for row in db.execute("SELECT data FROM tasks WHERE run_id=?", (run_id,)).fetchall():
            task = json.loads(row[0])
            if task["status"] in {"available", "claimed"}:
                task.update(status="revoked", revoked_at=now(), revoked_reason=reason, lease_deadline=0.0)
                self.save(db, "tasks", task)
        for row in db.execute("SELECT data FROM grants").fetchall():
            grant = json.loads(row[0])
            if grant.get("run_id") == run_id and not grant.get("revoked_at"):
                grant.update(revoked_at=now(), revoked_reason=reason)
                self.save(db, "grants", grant)
        self.event(db, "authority.revoked", {"reason": reason, "summary": "事故终态撤销任务与执行授权"}, run_id=run_id)

    def reset(self):
        with self.tx() as db:
            old = self.get(db, "runs", self.meta(db, "current_run"))
            if old and old["status"] in ACTIVE:
                old.update(status="reset", closed_at=now(), elapsed_s=round(time.monotonic() - old["start_mono"], 3), failure_reason="OPERATOR_RESET")
                self.save(db, "runs", old)
            if old:
                self._revoke_run(db, old["id"], "reset")
            gen = self.meta(db, "generation") + 1
            self.set_meta(db, "generation", gen)
            self.set_meta(db, "resource_fence", self.meta(db, "resource_fence") + 1)
            self.set_meta(db, "transport_epoch", self.meta(db, "transport_epoch") + 1)
            self.set_meta(db, "current_run", None)
            self.set_meta(db, "muted", False)
            config = self.set_config(db, getattr(self, "baseline_config", DEFAULT_CONFIG))
            for row in db.execute("SELECT data FROM agents").fetchall():
                a = json.loads(row[0])
                a.update(paused=False, status="idle", detail="重置完成，等待任务", task_id=None)
                self.save(db, "agents", a)
            checks = {"old_grants_invalidated": True, "healthy_config_restored": True, "history_preserved": True, "message_epoch_advanced": True}
            self.event(db, "system.reset", {"generation": str(gen), "revision": config["revision"], "checks": checks, "summary": "新轮次已启用；旧令牌和迟到消息失效"}, producer="operator")
            return {"generation": str(gen), "revision": config["revision"], "checks": checks}

    def runs(self, limit=200):
        with self.tx() as db:
            return [json.loads(r[0]) for r in db.execute("SELECT data FROM runs ORDER BY rowid DESC LIMIT ?", (limit,)).fetchall()]

    def run(self, run_id):
        with self.tx() as db:
            return self.get(db, "runs", run_id)

    def fail_run(self, run_id, reason):
        with self.tx() as db:
            run = self.get(db, "runs", run_id)
            if run and run["status"] in ACTIVE:
                run.update(status="failed", failure_reason=reason, closed_at=now(), elapsed_s=round(time.monotonic() - run["start_mono"], 3))
                self.save(db, "runs", run)
                self._revoke_run(db, run_id, reason)
                self.event(db, "incident.failed", {"code": reason, "summary": "运行未通过，失败和消耗已保留", "elapsed_s": run["elapsed_s"]}, run_id=run_id)

    def reserve_usage(self, run_id, reservation, purpose):
        usage_id = uid("usage")
        with self.tx() as db:
            if run_id:
                run = self.get(db, "runs", run_id)
                if not run or run["status"] not in ACTIVE or run["generation"] != str(self.meta(db, "generation")):
                    raise Rejected("RUN_STALE", "模型请求属于已结束事故")
                if run["usage_tokens"] + run.get("unknown_reserved_tokens", 0) + run["reserved_tokens"] + reservation > run["token_budget"]:
                    raise Rejected("TOKEN_BUDGET_EXHAUSTED", "本轮总 token 预算不足", 429)
                run["reserved_tokens"] += reservation
                self.save(db, "runs", run)
            usage = {**getattr(self, "scope", {}), "id": usage_id, "run_id": run_id, "reservation": reservation, "purpose": purpose,
                     "status": "reserved", "provider_state": "not_sent", "at": now()}
            db.execute("INSERT INTO usage VALUES(?,?,?)", (usage_id, run_id, encode(usage)))
            return usage_id

    def mark_usage_sent(self, usage_id):
        # Write ahead of the network await. A crash after this commit is
        # conservatively unknown, even if the provider never received bytes.
        with self.tx() as db:
            u = self.get(db, "usage", usage_id)
            if not u or u["status"] != "reserved":
                raise Rejected("USAGE_STALE", "模型预算预留已失效")
            u.update(provider_state="sent", dispatched_at=now())
            self.save(db, "usage", u)

    def recover_usage(self, run_id=None, reason="SERVER_RESTARTED"):
        counts = {"recovered": 0, "known_zero": 0, "unknown": 0}
        with self.tx() as db:
            rows = db.execute("SELECT data FROM usage" + (" WHERE run_id=?" if run_id else ""),
                              (run_id,) if run_id else ()).fetchall()
            for row in rows:
                u = json.loads(row[0])
                if u["status"] != "reserved":
                    continue
                not_sent = u.get("provider_state") == "not_sent"
                actual = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0} if not_sent else None
                self._settle_usage(db, u, actual, None, reason + ("_BEFORE_PROVIDER" if not_sent else "_USAGE_UNKNOWN"))
                counts["recovered"] += 1
                counts["known_zero" if not_sent else "unknown"] += 1
            if counts["recovered"]:
                self.event(db, "model.recovered", {**counts, "reason": reason, "summary": "重启遗留模型预算已结算"}, run_id=run_id)
        return counts

    def settle_usage(self, usage_id, actual, elapsed_s, error=None):
        with self.tx() as db:
            return self._settle_usage(db, self.get(db, "usage", usage_id), actual, elapsed_s, error)

    def _settle_usage(self, db, u, actual, elapsed_s, error=None):
        if u["status"] != "reserved":
            return  # Late duplicate settlement must not count usage twice.
        total = actual.get("total_tokens") if isinstance(actual, dict) else None
        if type(total) is not int or total < 0:
            total = None
        u.update(usage=actual, elapsed_s=elapsed_s, error=error, status="failed" if error else "completed", unknown=total is None)
        self.save(db, "usage", u)
        if u["run_id"]:
            run = self.get(db, "runs", u["run_id"])
            if run:
                run["reserved_tokens"] = max(0, run["reserved_tokens"] - u["reservation"])
                run["usage_tokens"] += int(total or 0)
                run["usage_unknown"] |= total is None
                if total is None:
                    run["unknown_reserved_tokens"] = run.get("unknown_reserved_tokens", 0) + u["reservation"]
                run["budget_overrun"] = run["usage_tokens"] > run["token_budget"]
                self.save(db, "runs", run)
        self.event(db, "model.completed" if not error else "model.failed", {"purpose": u["purpose"], "usage": actual, "elapsed_s": elapsed_s, "error": error,
                                                                            "summary": f"{u['purpose']} 模型调用" + ("失败" if error else "完成")}, run_id=u["run_id"], producer="model")

    def usage_records(self, run_id):
        with self.tx() as db:
            return [json.loads(r[0]) for r in db.execute("SELECT data FROM usage WHERE run_id=?", (run_id,)).fetchall()]
