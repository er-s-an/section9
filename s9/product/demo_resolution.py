"""A deliberately narrow, operator-controlled support routing resolution.

This is a fixed configuration runbook, not a patch generator.  Every action is
bound to an immutable plan hash and stored in the existing scoped registry.
The target policy is supplied as environment configuration to the owned target
process; no shell command or model-produced target is accepted here.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import time
from typing import Any

import httpx

from s9.product.registry import ProductError
from s9.product.models import Approval as ApprovalModel
from s9.product.models import RecoveryReceipt as RecoveryReceiptModel
from s9.product.models import VerificationReceipt
from s9.product.resolution_assertions import check_assertions, derive_assertions
from s9.product.v1_contracts import (
    ExecutionAttempt as V1ExecutionAttempt, ExecutionState, ProposalState,
    ProposalVersion, ValidationCheck, ValidationReport,
)


RUNBOOK_ID = "support-policy-legacy-to-guarded"
OLD_POLICY = "legacy"
NEW_POLICY = "guarded"
_READONLY_PROBES = (
    {"id": "order_truth", "message": "Where is order 1001?"},
    {"id": "refund_guardrail", "message": "Check whether order 1002 is eligible for a refund; check eligibility only and do not issue or initiate a refund."},
    {"id": "complaint_multi_intent", "message": "My delivery is late and I want to complain. Check the status of order 1001 and whether it is eligible for a refund; check only, do not issue a refund."},
)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _sha_file(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except FileNotFoundError:
        return None


class DemoResolution:
    """Resolution service attached to one ``DemoWorkflow`` instance."""

    def __init__(self, workflow):
        self.workflow = workflow
        self.registry = workflow.registry
        self.target_gate = getattr(workflow, "target_gate", asyncio.Lock())

    def _scope(self) -> dict[str, str]:
        return {"project_id": self.workflow.wid, "environment_id": self.workflow.env}

    def _record(self, kind: str, item_id: str, incident_id: str | None = None):
        return self.registry.get(kind, item_id, **self._scope(), incident_id=incident_id)

    def _put(self, kind: str, item_id: str, data: dict[str, Any], incident_id: str | None = None):
        return self.registry.put(kind, item_id, data, **self._scope(), incident_id=incident_id)

    def _target_config(self) -> dict[str, Any]:
        saved = self._record("demo_target_config", "support_policy")
        if saved is None:
            policy = OLD_POLICY
            value = {"policy": policy, "revision": 1, "source_commit": self.workflow.runtime.expected_commit,
                     "application_id": self.workflow.aid,
                     "updated_by": "bootstrap", "updated_at": now()}
            value["configuration_sha256"] = digest({"policy": policy, "source_commit": value["source_commit"]})
            return self._put("demo_target_config", "support_policy", value)
        if saved.get("application_id") != self.workflow.aid:
            raise ProductError("SCOPE_MISMATCH", "策略配置属于另一个应用", 403)
        expected_commit = self.workflow.runtime.expected_commit
        if saved.get("source_commit") != expected_commit:
            # A new adapter binding is accepted only when both the manifest-pinned
            # commit and the actual clean checkout agree. This is read-only Git
            # inspection; it does not start or stop the target process.
            binding = self._source()
            if binding["source_commit"] != expected_commit or not binding["source_clean"]:
                raise ProductError("SOURCE_STALE", "适配器源码未通过固定版本和工作区校验", 409)
            previous = {"source_commit": saved.get("source_commit"),
                "source_hash": saved.get("source_hash"), "configuration_sha256": saved.get("configuration_sha256"),
                "policy": saved.get("policy"), "revision": saved.get("revision"),
                "owner_plan_sha256": saved.get("owner_plan_sha256"),
                "superseded_at": now(), "superseded_by": expected_commit}
            history = list(saved.get("binding_history", []))
            history.append(previous)
            policy = saved.get("policy")
            if policy not in {OLD_POLICY, NEW_POLICY}:
                raise ProductError("INVALID_SUPPORT_POLICY", "持久策略配置无效，不能刷新源码绑定", 409)
            runtime_config = getattr(self.workflow.product, "_configuration_sha256", lambda: "unavailable")()
            refreshed = {**saved, "policy": policy, "source_commit": binding["source_commit"],
                "source_hash": binding["source_hash"], "source_clean": True,
                "revision": int(saved.get("revision", 0)) + 1, "binding_history": history,
                "updated_at": now(), "updated_by": "manifest-bound-adapter-upgrade",
                "configuration_sha256": digest({"policy": policy, "source_commit": binding["source_commit"],
                    "source_hash": binding["source_hash"], "runtime_configuration_sha256": runtime_config,
                    "application_id": self.workflow.aid, "environment_id": self.workflow.env})}
            refreshed.pop("owner_plan_sha256", None)
            return self._put("demo_target_config", "support_policy", refreshed)
        return saved

    def target_config(self) -> dict[str, Any]:
        """Return the durable desired target policy and its non-secret hash."""
        return dict(self._target_config())

    def model_env(self, policy: str | None = None) -> dict[str, str]:
        """Build target process env from server credentials plus fixed policy."""
        chosen = policy or self._target_config()["policy"]
        if chosen not in {OLD_POLICY, NEW_POLICY}:
            raise ProductError("INVALID_SUPPORT_POLICY", "客服路由策略无效", 409)
        env = dict(self.workflow.product._model_env())
        env["S9_SUPPORT_POLICY"] = chosen
        return env

    async def current_policy(self) -> dict[str, Any]:
        """Read the live target health contract; never infer from desired config."""
        try:
            async with httpx.AsyncClient(timeout=3, trust_env=False) as client:
                response = await client.get(f"http://127.0.0.1:{self.workflow.runtime.port}/health")
                body = response.json() if response.status_code == 200 else {}
            policy = body.get("support_policy")
            if policy not in {OLD_POLICY, NEW_POLICY} or body.get("source_commit") != self.workflow.runtime.expected_commit:
                return {"policy": "unknown", "health": body, "status_code": response.status_code}
            return {"policy": policy, "health": body, "status_code": response.status_code}
        except (httpx.RequestError, ValueError):
            return {"policy": "unknown", "health": None}

    def _source(self) -> dict[str, Any]:
        runtime = self.workflow.runtime
        state = runtime.git_state()
        ref = self.workflow.product.repo / "src" / "tools.py"
        source_hash = _sha_file(ref)
        if not state.get("matches_expected") or not state.get("clean") or not source_hash:
            raise ProductError("SOURCE_STALE", "目标源码版本无法确认，不能准备策略变更", 409)
        return {"source_commit": state["commit"], "source_hash": source_hash,
                "source_clean": bool(state.get("clean")), "source_path": "src/tools.py"}

    def _task_preconditions(self, job: dict[str, Any]) -> tuple[str, str]:
        active = [item for item in self.workflow.jobs() if item.get("id") != job.get("id") and
                  (item.get("state") in {"starting", "running"} or item.get("analysis_state") == "running")]
        if active:
            raise ProductError("TARGET_BUSY", "另一业务任务或调查仍在执行", 409)
        if job.get("state") in {"starting", "running"}:
            raise ProductError("BUSINESS_TASK_ACTIVE", "业务任务仍在执行，暂不能更改策略", 409)
        if job.get("analysis_state") == "running":
            raise ProductError("INVESTIGATION_ACTIVE", "调查仍在执行，暂不能更改策略", 409)
        if job.get("state") != "completed" or job.get("observation_state") != "verified":
            raise ProductError("RESOLUTION_EVIDENCE_NOT_READY", "需先完成业务任务并同步观测证据", 409)
        incident_id = job.get("incident_id")
        if not incident_id:
            raise ProductError("INCIDENT_REQUIRED", "需先完成同事故调查", 409)
        run_id = job.get("runs", {}).get("swarm") or job.get("runs", {}).get("single")
        if not run_id:
            raise ProductError("INVESTIGATION_REQUIRED", "需先完成同事故调查", 409)
        if job.get("source_commit") != self.workflow.runtime.expected_commit:
            raise ProductError("SOURCE_STALE", "事故证据和当前目标源码版本不一致", 409)
        return incident_id, run_id

    def _task(self, job_id: str) -> dict[str, Any]:
        return self.workflow.get(job_id)

    def _snapshot(self, job: dict[str, Any]) -> dict[str, Any] | None:
        resolution = job.get("resolution")
        return dict(resolution) if isinstance(resolution, dict) else None

    def _save_plan(self, job: dict[str, Any], resolution: dict[str, Any]) -> dict[str, Any]:
        previous = self._snapshot(job)
        if previous and (previous.get("status") != resolution.get("status") or
                         previous.get("execution") != resolution.get("execution") or
                         previous.get("verification") != resolution.get("verification") or
                         previous.get("recovery") != resolution.get("recovery")):
            resolution.setdefault("history", list(previous.get("history", [])))
            resolution["history"].append({"sequence": len(resolution["history"]) + 1,
                "at": now(), "status": resolution.get("status"),
                "execution_state": resolution.get("execution", {}).get("state"),
                "verification_status": resolution.get("verification", {}).get("status"),
                "recovery_status": resolution.get("recovery", {}).get("status")})
        elif not previous:
            resolution.setdefault("history", [{"sequence": 1, "at": now(), "status": resolution.get("status")}])
        job["resolution"] = resolution
        self.workflow.save(job)
        plan_id = resolution["plan_id"]
        incident_id = resolution["incident_id"]
        data = {**resolution, "plan_sha256": resolution["plan_sha256"]}
        self._put("demo_resolution_plan", f"resolution-plan:{plan_id}", data, incident_id)
        advance = getattr(self.registry, "advance_demo_resolution", None)
        if callable(advance):
            advance(self.workflow.wid, self.workflow.aid, self.workflow.env, incident_id, plan_id)
        return resolution

    def _save_execution(self, job: dict[str, Any], resolution: dict[str, Any], state: str,
                        *, idempotency_key: str, detail: str = "", attempt_id: str | None = None) -> None:
        execution_id = attempt_id or f"execution:{resolution['plan_id']}:attempt-1"
        run_id = resolution["run_id"]
        mapped_state = {"not_applied": ExecutionState.NOT_APPLIED, "applied": ExecutionState.APPLIED,
                        "running": ExecutionState.RUNNING, "outcome_unknown": ExecutionState.OUTCOME_UNKNOWN,
                        "manual_required": ExecutionState.MANUAL_REQUIRED}.get(state, ExecutionState.OUTCOME_UNKNOWN)
        started = resolution["execution"].get("started_at") or now()
        contract = V1ExecutionAttempt.model_validate({"id": execution_id, "workspace_id": self.workflow.wid,
            "application_id": self.workflow.aid, "environment_id": self.workflow.env,
            "incident_id": resolution["incident_id"], "run_id": run_id,
            "proposal_version_id": f"resolution-proposal:{resolution['plan_id']}",
            "proposal_sha256": resolution["plan_sha256"], "idempotency_key": idempotency_key,
            "resource_versions": [{"resource_ref": "env:S9_SUPPORT_POLICY", "version": resolution["configuration_sha256"]}],
            "state": mapped_state, "external_operation_ref": f"target-policy:{resolution['new_policy']}",
            "result_sha256": digest({"state": state, "detail": detail}) if state != "running" else None,
            "started_at": started, "completed_at": now() if state != "running" else None,
            "revision": 1, "created_at": started, "updated_at": now()})
        previous = self._record("execution_attempt", execution_id, resolution["incident_id"])
        history = list(previous.get("history", [])) if previous else []
        history.append({"at": now(), "state": state, "detail": detail})
        attempt = {**contract.model_dump(mode="json"), "plan_id": resolution["plan_id"],
                   "plan_sha256": resolution["plan_sha256"], "kind": "execution_attempt", "detail": detail,
                   "history": history}
        self._put("execution_attempt", execution_id, attempt, resolution["incident_id"])

    async def prepare(self, job_id: str, runbook_id: str = RUNBOOK_ID,
                      expected_plan_sha256: str | None = None) -> dict[str, Any]:
        if runbook_id != RUNBOOK_ID:
            raise ProductError("RUNBOOK_NOT_ALLOWED", "仅支持客服路由策略固定预案", 422)
        job = self._task(job_id)
        incident_id, run_id = self._task_preconditions(job)
        async with self.target_gate:
            job = self._task(job_id)
            incident_id, run_id = self._task_preconditions(job)
            source = self._source()
            ensure_target = getattr(self.workflow, "ensure_target", None)
            if callable(ensure_target):
                await ensure_target()
            current = await self.current_policy()
            if current["policy"] != OLD_POLICY:
                raise ProductError("POLICY_PRECONDITION_FAILED", "当前策略不是 legacy；先对照实时策略读取并人工处理", 409)
            target = self._target_config()
            if target["policy"] != OLD_POLICY:
                raise ProductError("CONFIG_PRECONDITION_FAILED", "持久策略配置已变化，请重新读取后准备", 409)
            original_assertions = derive_assertions(job)
            service_config = getattr(self.workflow.product, "_configuration_sha256", lambda: "unavailable")()
            configuration = digest({"policy": OLD_POLICY, "source_commit": source["source_commit"],
                                    "source_hash": source["source_hash"], "environment_id": self.workflow.env,
                                    "runtime_configuration_sha256": service_config})
            payload = {"schema": "section9.demo.support-policy/v1", "environment_variable": "S9_SUPPORT_POLICY",
                       "from": OLD_POLICY, "to": NEW_POLICY, "restart_owned_runtime": True}
            prior = self._snapshot(job)
            version = int(prior.get("version", 0)) + 1 if prior else 1
            validation_plan = {"health_observations_required": 5, "policy_readback_required": NEW_POLICY,
                "readonly_business_probes": [dict(p) for p in _READONLY_PROBES],
                "original_incident_replay": original_assertions,
                "database_mutation_guard": "logical business.sqlite rows must be readable and unchanged",
                "rollback_policy": "manual-only"}
            plan_body = {"runbook_id": RUNBOOK_ID, "incident_id": incident_id, "run_id": run_id,
                         "source": source, "configuration_sha256": configuration, "payload": payload,
                         "validation_plan": validation_plan, "rollback_policy": OLD_POLICY,
                         "original_assertions": original_assertions, "version": version, "operator_selected": True}
            plan_hash = digest(plan_body)
            if expected_plan_sha256 and expected_plan_sha256 != plan_hash:
                raise ProductError("PLAN_HASH_MISMATCH", "预案摘要不匹配", 409)
            if prior and prior.get("plan_sha256") == plan_hash and prior.get("status") not in {"rejected", "rolled_back"}:
                return {"resolution": prior}
            if prior and prior.get("status") in {"running", "outcome_unknown", "rollback_running"}:
                raise ProductError("RESOLUTION_IN_FLIGHT", "当前执行结果尚未核实，先进行 reconcile", 409)
            plan_id = hashlib.sha256(f"{job_id}:{plan_hash}".encode()).hexdigest()[:24]
            proposal_id = f"resolution-proposal:{plan_id}"
            proposal_contract = ProposalVersion.model_validate({
                "id": proposal_id, "workspace_id": self.workflow.wid, "application_id": self.workflow.aid,
                "environment_id": self.workflow.env, "incident_id": incident_id, "run_id": run_id,
                "proposal_id": proposal_id, "version": version, "proposal_sha256": plan_hash,
                "target_refs": ["env:S9_SUPPORT_POLICY"],
                "actions": [{"action_id": "set-support-policy", "schema_ref": "section9.demo.support-policy/v1",
                    "target_ref": "env:S9_SUPPORT_POLICY", "payload_sha256": digest(payload)}],
                "risk_summary": "受控重启客服助手并将只读路由策略从 legacy 调整为 guarded。",
                "rollback_conditions": ["策略读回不符合 guarded", "业务真值探针失败", "检测到数据库写入"],
                "validation_plan_ref": "section9:demo-resolution-validation:v1", "budget_token_limit": 0,
                "state": ProposalState.READY_FOR_APPROVAL, "revision": 1,
                "created_at": now(), "updated_at": now()})
            self._put("proposal_version", proposal_id, {"contract": proposal_contract.model_dump(mode="json"),
                "plan_id": plan_id, "plan_sha256": plan_hash}, incident_id)
            resolution = {"status": "prepared", "runbook_id": RUNBOOK_ID,
                "runbook_title": "客服助手支持路由策略回归", "operator_selected": True,
                "automation": "fixed-runbook", "incident_id": incident_id, "run_id": run_id,
                "plan_id": plan_id, "version": version, "plan_sha256": plan_hash,
                "approval_id": None, "execution_attempt_id": None, "validation_report_id": None,
                "recovery_receipt_id": None, "rollback_receipt_id": None,
                "old_policy": OLD_POLICY, "new_policy": NEW_POLICY,
                "source_commit": source["source_commit"], "source_hash": source["source_hash"],
                "source_clean": source["source_clean"], "configuration_sha256": configuration,
                "original_assertions": original_assertions,
                "payload": payload, "validation_plan": validation_plan,
                "rollback": {"policy": OLD_POLICY, "manual_only": True},
                "approval": None, "execution": {"state": "authorized_waiting_review", "id": None, "idempotency_key": None},
                "verification": {"status": "not_run", "health_observations": [], "policy_readback": None,
                                 "business_probes": [], "database_guard": None, "completed_at": None},
                "recovery": {"status": "not_needed", "receipt_id": None, "policy_readback": None, "detail": ""},
                "lesson_candidate": None, "created_at": now()}
            self._save_plan(job, resolution)
            return {"resolution": resolution}

    async def review(self, job_id: str, plan_sha256: str, decision: str, reason: str,
                     approver: str = "local-operator") -> dict[str, Any]:
        if decision not in {"approved", "rejected"}:
            raise ProductError("INVALID_REVIEW_DECISION", "审批决定必须为 approved 或 rejected", 422)
        if not reason.strip() or not approver.strip():
            raise ProductError("REVIEW_REASON_REQUIRED", "请填写审批人和理由", 422)
        job = self._task(job_id)
        incident_id, _ = self._task_preconditions(job)
        async with self.target_gate:
            job = self._task(job_id)
            incident_id, _ = self._task_preconditions(job)
            r = self._snapshot(job)
            if not r or r["plan_sha256"] != plan_sha256 or r["incident_id"] != incident_id:
                raise ProductError("PLAN_HASH_MISMATCH", "审批必须绑定当前事故的完整预案摘要", 409)
            if r["status"] != "prepared":
                raise ProductError("PLAN_NOT_REVIEWABLE", "当前预案状态不能审批", 409)
            if decision == "approved":
                current_source = self._source()
                current = await self.current_policy()
                config = self._target_config()
                service_config = getattr(self.workflow.product, "_configuration_sha256", lambda: "unavailable")()
                expected_config = digest({"policy": OLD_POLICY, "source_commit": r["source_commit"],
                    "source_hash": r["source_hash"], "environment_id": self.workflow.env,
                    "runtime_configuration_sha256": service_config})
                if (current_source["source_commit"] != r["source_commit"] or current_source["source_hash"] != r["source_hash"]
                    or expected_config != r["configuration_sha256"]
                    or current["policy"] != OLD_POLICY or config["policy"] != OLD_POLICY):
                    raise ProductError("PLAN_STALE", "源码或策略已变化，请重新准备预案", 409)
            approval_id = f"approval:{r['plan_id']}"
            preflight_id = f"preflight:{r['plan_id']}"
            preflight = VerificationReceipt.model_validate({"project_id": self.workflow.wid,
                "environment_id": self.workflow.env, "incident_id": incident_id,
                "receipt_id": preflight_id, "verified_at": now(), "status": "ready", "evidence_ids": [],
                "candidate_id": r["plan_id"], "configuration_sha256": r["configuration_sha256"],
                "base_ref": r["source_commit"], "candidate_ref": "env:S9_SUPPORT_POLICY=guarded",
                "original_failed": True, "candidate_passed": False, "no_regressions": False,
                "checks": [{"name": "live-policy-is-legacy", "status": "passed" if decision == "approved" else "not_run",
                            "evidence_ids": [], "detail": "Read-only preflight only; not post-change verification."}],
                "detail": "预执行事实检查，不代表变更已执行或验证。"})
            self._put("verification_receipt", preflight_id, {**preflight.model_dump(mode="json"),
                "plan_sha256": plan_sha256}, incident_id)
            approval_contract = ApprovalModel.model_validate({"project_id": self.workflow.wid,
                "environment_id": self.workflow.env, "incident_id": incident_id,
                "approval_id": approval_id, "approver": approver, "approved_at": now(),
                "decision": decision, "change_id": r["plan_id"], "verification_receipt_id": preflight_id,
                "target_revision": r["source_commit"], "policy_version": str(r["version"]),
                "configuration_sha256": r["configuration_sha256"],
                "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat()})
            approval = {"id": approval_id, "incident_id": incident_id, "plan_id": r["plan_id"],
                        "plan_sha256": plan_sha256, "decision": decision, "approver": approver,
                        "reason": reason.strip(), "approved_at": now(), "target_revision": r["source_commit"],
                        "policy_version": r["version"], "configuration_sha256": r["configuration_sha256"],
                        "execution_authorization": decision == "approved", "verification_receipt_id": preflight_id,
                        "contract": approval_contract.model_dump(mode="json")}
            self._put("approval", approval_id, approval, incident_id)
            r["approval"] = {k: approval[k] for k in ("decision", "plan_sha256", "approver", "reason", "approved_at", "execution_authorization")}
            r["approval_id"] = approval_id
            r["status"] = "authorized" if decision == "approved" else "rejected"
            proposal_id = f"resolution-proposal:{r['plan_id']}"
            saved_proposal = self._record("proposal_version", proposal_id, incident_id)
            if saved_proposal:
                contract_data = dict(saved_proposal["contract"])
                contract_data.update(state=ProposalState.APPROVED if decision == "approved" else ProposalState.REJECTED,
                                     revision=int(contract_data["revision"]) + 1, updated_at=now())
                updated_contract = ProposalVersion.model_validate(contract_data)
                self._put("proposal_version", proposal_id, {**saved_proposal,
                    "contract": updated_contract.model_dump(mode="json"), "plan_sha256": plan_sha256}, incident_id)
            self._save_plan(job, r)
            return {"resolution": r}

    async def _wait_health(self, policy: str, samples: int = 1, interval_seconds: float = 1.0) -> list[dict[str, Any]]:
        result = []
        for i in range(samples):
            try:
                async with httpx.AsyncClient(timeout=4, trust_env=False) as client:
                    response = await client.get(f"http://127.0.0.1:{self.workflow.runtime.port}/health")
                    body = response.json() if response.status_code == 200 else {}
                result.append({"at": now(), "status": "passed" if response.status_code == 200 and body.get("support_policy") == policy else "failed",
                               "http_status": response.status_code, "support_policy": body.get("support_policy")})
            except (httpx.RequestError, ValueError):
                result.append({"at": now(), "status": "unknown", "http_status": None, "support_policy": None})
            if i + 1 < samples:
                await asyncio.sleep(interval_seconds)
        return result

    def _database_snapshot(self) -> dict[str, Any]:
        path = Path(self.workflow.runtime.data_path)
        if not path.exists():
            return {"valid": False, "error": "database is missing", "logical_rows_sha256": None}
        try:
            db = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=3)
            tables = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
            rows = {}
            for name in tables:
                columns = [item[1] for item in db.execute(f'PRAGMA table_info("{name}")')]
                select = ",".join('"' + column.replace('"', '""') + '"' for column in columns)
                values = db.execute(f'SELECT {select} FROM "{name}" ORDER BY rowid').fetchall()
                rows[name] = {"columns": columns, "rows": [list(value) for value in values]}
            db.close()
            return {"valid": True, "logical_rows_sha256": digest(rows),
                    "row_counts": {name: len(value["rows"]) for name, value in rows.items()}}
        except sqlite3.Error as exc:
            return {"valid": False, "error": type(exc).__name__, "logical_rows_sha256": None}

    async def _business_probes(self, job: dict[str, Any], plan_id: str,
                              original_assertions: dict[str, Any]) -> list[dict[str, Any]]:
        probes = [*(_READONLY_PROBES), {"id": "original_replay", "message": job["message"]}]
        results = []
        base = f"http://127.0.0.1:{self.workflow.runtime.port}"
        try:
            from s9.product.service import _matches_business_truth
            from integrations.support_agent.manifest import get_manifest
            from integrations.support_agent.business_policy import WRITE_TOOLS
            from integrations.support_agent.observability import redact
            manifest = get_manifest()
        except ImportError:
            _matches_business_truth, manifest, WRITE_TOOLS = None, {"business_truth_samples": []}, set()
            redact = lambda value: value
        for probe in probes:
            try:
                async with httpx.AsyncClient(timeout=150, trust_env=False) as client:
                    response = await client.post(base + "/chat", json={"message": probe["message"],
                        "session_id": f"s9-resolution-{plan_id}-{probe['id']}"})
                body = response.json()
                policy_ok = body.get("support_policy") == NEW_POLICY
                decision = body.get("policy_decision")
                read_only = (isinstance(decision, dict) and decision.get("mode") == NEW_POLICY
                             and decision.get("read_only") is True and decision.get("decision") in {"readonly_query", "clarification_needed"})
                trace = body.get("trace", [])
                no_write_tool = isinstance(trace, list) and all(isinstance(item, dict) and item.get("tool") not in WRITE_TOOLS for item in trace)
                if probe["id"] in {"order_truth", "refund_guardrail"} and _matches_business_truth:
                    sample = next(item for item in manifest["business_truth_samples"] if item["name"] == probe["id"])
                    truth_ok = _matches_business_truth(sample, body)
                elif probe["id"] == "complaint_multi_intent":
                    tools = {item.get("tool") for item in trace if isinstance(item, dict)}
                    order_ids = {arg for item in trace if isinstance(item, dict) and isinstance(item.get("args"), dict)
                                 for arg in [item["args"].get("order_id")]}
                    intents = decision.get("intents", []) if isinstance(decision, dict) else []
                    truth_ok = {"get_order_status", "check_refund_eligibility"}.issubset(tools) and \
                        1001 in order_ids and {"order_status", "refund_eligibility"}.issubset(set(intents))
                elif probe["id"] == "original_replay":
                    assertions = check_assertions(original_assertions, body, self.workflow.runtime.data_path)
                    truth_ok = assertions["passed"]
                else:
                    truth_ok = False
                probe_ok = response.status_code == 200 and policy_ok and read_only and no_write_tool and truth_ok
                results.append({"id": probe["id"], "status": "passed" if probe_ok else "failed",
                                "http_status": response.status_code, "support_policy": body.get("support_policy"),
                                "handoff_state": body.get("handoff_state"), "policy_decision": body.get("policy_decision"),
                                "business_truth_passed": truth_ok,
                                "reply": str(body.get("reply", ""))[:2000],
                                "trace": redact(trace) if isinstance(trace, list) else [],
                                "trace_id": body.get("langfuse_trace_id") or body.get("trace_id"),
                                "reply_sha256": hashlib.sha256(str(body.get("reply", "")).encode()).hexdigest(),
                                "assertion_checks": assertions.get("checks", []) if probe["id"] == "original_replay" else []})
            except (httpx.RequestError, ValueError):
                results.append({"id": probe["id"], "status": "unknown", "http_status": None})
        return results

    async def execute(self, job_id: str, plan_sha256: str, idempotency_key: str) -> dict[str, Any]:
        if not idempotency_key or len(idempotency_key) > 200:
            raise ProductError("IDEMPOTENCY_KEY_REQUIRED", "执行需要有效的 Idempotency-Key", 422)
        job = self._task(job_id)
        self._task_preconditions(job)
        async with self.target_gate:
            job = self._task(job_id)
            self._task_preconditions(job)
            r = self._snapshot(job)
            if not r or r["plan_sha256"] != plan_sha256:
                raise ProductError("PLAN_HASH_MISMATCH", "执行请求与已审批预案不匹配", 409)
            if r["status"] in {"applied", "verified"}:
                return {"resolution": r}
            if r["status"] != "authorized" or not r.get("approval", {}).get("execution_authorization"):
                raise ProductError("EXECUTION_NOT_AUTHORIZED", "执行前需要对完整预案摘要单独审批授权", 403)
            prior_attempts = [item for item in self.registry.list("execution_attempt", **self._scope(), incident_id=r["incident_id"])
                              if item.get("plan_id") == r["plan_id"]]
            if prior_attempts and any(item.get("idempotency_key") != idempotency_key for item in prior_attempts):
                raise ProductError("IDEMPOTENCY_CONFLICT", "此预案已由另一个执行键认领", 409)
            if any(item.get("state") in {"applied", "outcome_unknown", "running", "manual_required"} for item in prior_attempts):
                raise ProductError("EXECUTION_RECONCILE_REQUIRED", "此预案已有执行记录；先检查并 reconcile 实际策略", 409)
            approval_record = self._record("approval", r.get("approval_id"), r["incident_id"])
            if (not approval_record or approval_record.get("decision") != "approved" or
                approval_record.get("plan_sha256") != plan_sha256 or
                datetime.fromisoformat(approval_record["contract"]["expires_at"].replace("Z", "+00:00")) < datetime.now(timezone.utc)):
                raise ProductError("APPROVAL_EXPIRED", "执行授权缺失、过期或与预案不匹配", 403)
            source = self._source()
            current = await self.current_policy()
            service_config = getattr(self.workflow.product, "_configuration_sha256", lambda: "unavailable")()
            expected_config = digest({"policy": OLD_POLICY, "source_commit": r["source_commit"],
                "source_hash": r["source_hash"], "environment_id": self.workflow.env,
                "runtime_configuration_sha256": service_config})
            if (source["source_commit"] != r["source_commit"] or source["source_hash"] != r["source_hash"]
                or expected_config != r["configuration_sha256"] or current["policy"] != OLD_POLICY
                or not self.workflow.runtime.status().get("owned")):
                raise ProductError("PLAN_STALE", "执行前重验失败：源码、配置、策略或runtime归属已变化", 409)
            target_config = self._target_config()
            if target_config["policy"] not in {OLD_POLICY, NEW_POLICY} or (
                target_config["policy"] == NEW_POLICY and target_config.get("owner_plan_sha256") != plan_sha256
            ):
                raise ProductError("PLAN_STALE", "持久策略配置已被其他预案认领", 409)
            # Persist the claim and started state before the first side effect.
            r["status"] = "running"
            r["execution"] = {"state": "running", "id": f"execution:{r['plan_id']}",
                              "idempotency_key": idempotency_key, "started_at": now()}
            seq = len(prior_attempts) + 1
            claim_id = f"execution:{r['plan_id']}:attempt-{seq}"
            r["execution_attempt_id"] = claim_id
            self._save_execution(job, r, "running", idempotency_key=idempotency_key, attempt_id=claim_id)
            self._save_plan(job, r)
            try:
                config = self._target_config()
                if config["policy"] == NEW_POLICY and config.get("owner_plan_sha256") != plan_sha256:
                    raise RuntimeError("target policy changed after approval")
                runtime_config = getattr(self.workflow.product, "_configuration_sha256", lambda: "unavailable")()
                config = {**config, "policy": NEW_POLICY, "revision": int(config["revision"]) + (config["policy"] != NEW_POLICY),
                          "application_id": self.workflow.aid,
                          "owner_plan_sha256": plan_sha256,
                          "updated_by": r["approval"]["approver"], "updated_at": now(),
                          "configuration_sha256": digest({"policy": NEW_POLICY, "source_commit": r["source_commit"],
                              "source_hash": r["source_hash"], "runtime_configuration_sha256": runtime_config})}
                self._put("demo_target_config", "support_policy", config)
                stopped = await asyncio.to_thread(self.workflow.runtime.stop)
                if stopped.get("status") == "unknown":
                    raise RuntimeError("owned target process could not be stopped with certainty")
                await asyncio.to_thread(self.workflow.runtime.start, model_env=self.model_env(NEW_POLICY))
                health = []
                for _ in range(40):
                    health = await self._wait_health(NEW_POLICY, 1)
                    if health[-1]["status"] == "passed":
                        break
                    await asyncio.sleep(.25)
                if not health or health[-1]["status"] != "passed":
                    raise RuntimeError("new policy was not confirmed by live health readback")
                r["status"] = "applied"
                r["execution"].update(state="applied", completed_at=now(), health_readback=health[-1])
                result_id = claim_id
                r["execution_attempt_id"] = result_id
                self._save_execution(job, r, "applied", idempotency_key=idempotency_key, attempt_id=result_id)
                self._save_plan(job, r)
            except Exception as exc:
                r["status"] = "outcome_unknown"
                r["execution"].update(state="outcome_unknown", error_code=type(exc).__name__, detail=str(exc)[:1000])
                result_id = claim_id
                r["execution_attempt_id"] = result_id
                self._save_execution(job, r, "outcome_unknown", idempotency_key=idempotency_key,
                                     detail=str(exc)[:1000], attempt_id=result_id)
                self._save_plan(job, r)
            return {"resolution": r}

    async def reconcile(self, job_id: str) -> dict[str, Any]:
        job = self._task(job_id)
        async with self.target_gate:
            job = self._task(job_id)
            self._task_preconditions(job)
            r = self._snapshot(job)
            if not r:
                raise ProductError("RESOLUTION_NOT_PREPARED", "没有可 reconcile 的预案", 409)
            current = await self.current_policy()
            policy = current["policy"]
            if r["status"] in {"running", "outcome_unknown", "rollback_running", "manual_required", "validating"}:
                if r["status"] == "validating":
                    r["status"] = "applied" if policy == r["new_policy"] else "manual_required"
                    r["verification"].update(status="unknown", detail="verification interrupted; results were not assumed")
                if policy == r["new_policy"]:
                    r["status"] = "applied"
                    r["execution"]["state"] = "applied"
                    r["execution"]["reconciled_at"] = now()
                    r["execution"]["live_policy"] = policy
                    self._save_execution(job, r, "applied", idempotency_key=r["execution"].get("idempotency_key", "reconcile"),
                                         attempt_id=r.get("execution_attempt_id"))
                elif policy == r["old_policy"]:
                    r["status"] = "authorized"
                    r["execution"].update(state="not_applied", reconciled_at=now(), live_policy=policy)
                    self._save_execution(job, r, "not_applied", idempotency_key=r["execution"].get("idempotency_key", "reconcile"),
                                         attempt_id=r.get("execution_attempt_id"))
                else:
                    r["status"] = "manual_required"
                    r["execution"].update(state="manual_required", reconciled_at=now(), live_policy="unknown")
                    self._save_execution(job, r, "manual_required", idempotency_key=r["execution"].get("idempotency_key", "reconcile"),
                                         attempt_id=r.get("execution_attempt_id"))
            r["reconcile"] = {"at": now(), "live_policy": policy, "health": current.get("health")}
            self._save_plan(job, r)
            return {"resolution": r}

    async def verify(self, job_id: str, plan_sha256: str) -> dict[str, Any]:
        job = self._task(job_id)
        self._task_preconditions(job)
        async with self.target_gate:
            job = self._task(job_id)
            self._task_preconditions(job)
            r = self._snapshot(job)
            if not r or r["plan_sha256"] != plan_sha256:
                raise ProductError("PLAN_HASH_MISMATCH", "验证请求与当前预案不匹配", 409)
            if r["status"] not in {"applied", "verification_failed"}:
                raise ProductError("EXECUTION_NOT_APPLIED", "需先 reconcile 并确认策略已应用", 409)
            source = self._source()
            if source["source_commit"] != r["source_commit"] or source["source_hash"] != r["source_hash"]:
                raise ProductError("SOURCE_STALE", "源码版本已变化，不能沿用该预案验证", 409)
            service_config = getattr(self.workflow.product, "_configuration_sha256", lambda: "unavailable")()
            expected_config = digest({"policy": OLD_POLICY, "source_commit": r["source_commit"],
                "source_hash": r["source_hash"], "environment_id": self.workflow.env,
                "runtime_configuration_sha256": service_config})
            if expected_config != r["configuration_sha256"]:
                raise ProductError("CONFIG_STALE", "运行配置已变化，不能沿用该预案验证", 409)
            r["status"] = "validating"
            r["verification"].update(status="validating", started_at=now())
            self._save_plan(job, r)
            verification_started = time.monotonic()
            before = self._database_snapshot()
            probes = await self._business_probes(job, r["plan_id"], r["original_assertions"])
            after = self._database_snapshot()
            db_guard = {"status": "passed" if before.get("valid") and after.get("valid") and before == after else "failed",
                        "before": before, "after": after}
            health = await self._wait_health(NEW_POLICY, 5, interval_seconds=1.0)
            policy = await self.current_policy()
            observation_window_seconds = max(0.0, time.monotonic() - verification_started)
            passed = all(x["status"] == "passed" for x in health) and policy["policy"] == NEW_POLICY \
                and all(x["status"] == "passed" for x in probes) and db_guard["status"] == "passed"
            existing_reports = [item for item in self.registry.list("validation_report", **self._scope(), incident_id=r["incident_id"])
                                if item.get("plan_id") == r["plan_id"]]
            verification_id = f"verification:{r['plan_id']}:{len(existing_reports) + 1}"
            checks = [ValidationCheck.model_validate({"name": f"health-observation-{i + 1}",
                "status": item["status"], "detail": json.dumps(item, ensure_ascii=False)}) for i, item in enumerate(health)]
            checks.append(ValidationCheck.model_validate({"name": "policy-readback",
                "status": "passed" if policy["policy"] == NEW_POLICY else "failed", "detail": policy["policy"]}))
            checks.extend(ValidationCheck.model_validate({"name": f"business-probe-{item['id']}",
                "status": item["status"], "detail": json.dumps(item, ensure_ascii=False)}) for item in probes)
            checks.append(ValidationCheck.model_validate({"name": "database-mutation-guard",
                "status": db_guard["status"], "detail": json.dumps(db_guard, ensure_ascii=False)}))
            report_contract = ValidationReport.model_validate({"id": verification_id, "workspace_id": self.workflow.wid,
                "application_id": self.workflow.aid, "environment_id": self.workflow.env,
                "incident_id": r["incident_id"], "run_id": r["run_id"],
                "proposal_version_id": f"resolution-proposal:{r['plan_id']}",
                "execution_attempt_id": r.get("execution_attempt_id") or f"execution:{r['plan_id']}:attempt-1:applied",
                "applied_version_sha256": digest({"policy": NEW_POLICY, "source_commit": r["source_commit"]}),
                "contract_sha256": r["plan_sha256"], "result": "passed" if passed else "failed",
                "checks": [check.model_dump(mode="json") for check in checks], "evidence_ids": [],
                "completed_at": now(), "revision": len(existing_reports) + 1,
                "created_at": now(), "updated_at": now()})
            report = {"id": verification_id, "incident_id": r["incident_id"], "run_id": r["run_id"],
                      "proposal_version_id": f"resolution-proposal:{r['plan_id']}",
                      "execution_attempt_id": r["execution_attempt_id"],
                      "plan_sha256": plan_sha256, "result": "passed" if passed else "failed",
                      "health_observations": health, "policy_readback": policy,
                      "business_probes": probes, "database_guard": db_guard, "completed_at": now(),
                      "contract": report_contract.model_dump(mode="json"), "plan_id": r["plan_id"]}
            self._put("validation_report", verification_id, report, r["incident_id"])
            r["validation_report_id"] = verification_id
            r["verification"] = {"status": "passed" if passed else "failed", "health_observations": health,
                "policy_readback": policy, "business_probes": probes, "database_guard": db_guard,
                "completed_at": report["completed_at"], "id": verification_id}
            r["status"] = "verified" if passed else "verification_failed"
            if passed:
                recovery_id = f"recovery:{r['plan_id']}:{len(existing_reports) + 1}"
                receipt = RecoveryReceiptModel.model_validate({"project_id": self.workflow.wid,
                    "environment_id": self.workflow.env, "incident_id": r["incident_id"],
                    "receipt_id": recovery_id, "attempt_id": r["execution_attempt_id"],
                    "recovered_at": now(), "status": "recovered", "source_revision": r["source_commit"],
                    "configuration_sha256": r["configuration_sha256"],
                    "evidence_ids": [verification_id], "source_binding_id": self.workflow.binding["id"],
                    "observation_window_seconds": observation_window_seconds, "business_probe_passed": True,
                    "detail": "Live guarded policy, five health observations, business truth probes, and no-write guard passed."})
                receipt_record = {**receipt.model_dump(mode="json"), "plan_sha256": plan_sha256,
                                  "plan_id": r["plan_id"], "verification_report_id": verification_id}
                self._put("recovery_receipt", recovery_id, receipt_record, r["incident_id"])
                r["recovery_receipt_id"] = recovery_id
                r["recovery"] = {"status": "recovered", "receipt_id": recovery_id,
                    "policy_readback": policy, "detail": receipt.detail, "business_probe_passed": True}
                lesson_id = f"lesson-candidate:{r['plan_id']}"
                r["lesson_candidate"] = {"id": lesson_id, "status": "candidate",
                    "title": "客服助手支持路由策略回归", "source_incident_id": r["incident_id"],
                    "plan_sha256": plan_sha256, "evidence": {"verification_report_id": verification_id,
                        "health_observations": len(health), "business_probe_ids": [x["id"] for x in probes],
                        "database_guard": db_guard["status"]}}
                self._put("lesson_candidate", lesson_id, r["lesson_candidate"], r["incident_id"])
            self._save_plan(job, r)
            return {"resolution": r}

    async def rollback(self, job_id: str, plan_sha256: str, idempotency_key: str) -> dict[str, Any]:
        if not idempotency_key or len(idempotency_key) > 200:
            raise ProductError("IDEMPOTENCY_KEY_REQUIRED", "回滚需要有效的 Idempotency-Key", 422)
        job = self._task(job_id)
        self._task_preconditions(job)
        async with self.target_gate:
            job = self._task(job_id)
            self._task_preconditions(job)
            r = self._snapshot(job)
            if not r or r["plan_sha256"] != plan_sha256:
                raise ProductError("PLAN_HASH_MISMATCH", "回滚请求与当前预案不匹配", 409)
            if r["status"] == "rolled_back":
                if r.get("rollback_receipt", {}).get("idempotency_key") != idempotency_key:
                    raise ProductError("IDEMPOTENCY_CONFLICT", "回滚已由另一个键完成", 409)
                return {"resolution": r}
            if r["status"] not in {"verified", "verification_failed", "applied", "manual_required"}:
                raise ProductError("ROLLBACK_NOT_ALLOWED", "当前状态不能人工回滚", 409)
            r["status"] = "rollback_running"
            rollback_id = f"rollback:{r['plan_id']}:{hashlib.sha256(idempotency_key.encode()).hexdigest()[:12]}"
            r["rollback_receipt_id"] = rollback_id
            r["rollback_receipt"] = {"id": rollback_id, "status": "running", "plan_sha256": plan_sha256,
                "operator_triggered": True, "idempotency_key": idempotency_key,
                "started_at": now(), "policy_readback": None}
            self._save_plan(job, r)
            try:
                config = self._target_config()
                runtime_config = getattr(self.workflow.product, "_configuration_sha256", lambda: "unavailable")()
                config = {**config, "policy": OLD_POLICY, "revision": int(config["revision"]) + 1,
                          "application_id": self.workflow.aid,
                          "updated_by": "local-operator-rollback", "updated_at": now(),
                          "configuration_sha256": digest({"policy": OLD_POLICY, "source_commit": r["source_commit"],
                              "source_hash": r["source_hash"], "runtime_configuration_sha256": runtime_config})}
                config.pop("owner_plan_sha256", None)
                self._put("demo_target_config", "support_policy", config)
                stopped = await asyncio.to_thread(self.workflow.runtime.stop)
                if stopped.get("status") == "unknown":
                    raise RuntimeError("owned target process could not be stopped with certainty")
                await asyncio.to_thread(self.workflow.runtime.start, model_env=self.model_env(OLD_POLICY))
                observed = None
                for _ in range(40):
                    observed = await self.current_policy()
                    if observed["policy"] == OLD_POLICY:
                        break
                    await asyncio.sleep(.25)
                rollback = {"id": rollback_id, "incident_id": r["incident_id"], "plan_id": r["plan_id"],
                    "plan_sha256": plan_sha256, "operator_triggered": True,
                    "status": "rolled_back" if observed and observed["policy"] == OLD_POLICY else "unknown",
                    "restored_policy": observed and observed["policy"], "configuration_sha256": config["configuration_sha256"],
                    "completed_at": now(), "detail": "manual rollback policy readback"}
                self._put("rollback_receipt", rollback_id, rollback, r["incident_id"])
                r["rollback_receipt"] = rollback
                r["recovery"] = {**r.get("recovery", {}), "status": "obsolete_after_rollback",
                    "obsolete_at": now(), "detail": "verified guarded state was later manually rolled back"}
                if r.get("lesson_candidate"):
                    r["lesson_candidate"] = {**r["lesson_candidate"], "status": "invalidated",
                        "invalidated_at": now(), "invalidation_reason": "manual rollback"}
                    self._put("lesson_candidate", r["lesson_candidate"]["id"], r["lesson_candidate"], r["incident_id"])
                r["status"] = "rolled_back" if rollback["status"] == "rolled_back" else "manual_required"
            except Exception as exc:
                r["rollback_receipt"] = {**r["rollback_receipt"], "status": "unknown",
                    "completed_at": now(), "detail": str(exc)[:1000]}
                self._put("rollback_receipt", rollback_id, r["rollback_receipt"], r["incident_id"])
                r["status"] = "manual_required"
            self._save_plan(job, r)
            return {"resolution": r}
