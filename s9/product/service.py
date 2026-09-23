"""Product-facing orchestration for the first declared external project.

This is a thin integration layer.  It persists observations and lifecycle
records through ProductRegistry, while connectors/runners remain replaceable
and the existing lab Core remains authoritative for the built-in fixture.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from s9 import config
from s9.connectors.http_app import ConnectorConfig, ExternalHTTPConnector
from s9.connectors.langfuse import LangfuseConnector
from s9.product.bundle import ArtifactBundleWriter
from s9.product.external_runtime import runtime_from_manifest
from s9.product.models import (Approval, ConnectionCapability, EvidenceRef, ExecutionAttempt, ProbeResult,
                               RecoveryReceipt)
from s9.product.regression import RegressionRunner
from s9.product.registry import ProductError, ProductRegistry


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _execution_event_type(health_ok: bool) -> str:
    return "execution.succeeded" if health_ok else "execution.failed"


def _matches_business_truth(sample: dict[str, Any], body: Any) -> bool:
    """Match a declared business fact against one of its permitted read traces."""
    if not isinstance(body, dict):
        return False
    expected_fields = sample.get("expected_fields", [])
    if any(field not in body for field in expected_fields):
        return False
    expected_tools = sample.get("expected_trace_tools", [])
    if sample.get("expected_trace_tool"):
        expected_tools = [*expected_tools, sample["expected_trace_tool"]]
    if not expected_tools:
        return False
    for entry in body.get("trace", []):
        if not isinstance(entry, dict) or entry.get("tool") not in expected_tools:
            continue
        result = entry.get("result")
        if not isinstance(result, dict):
            continue
        arguments = entry.get("args")
        expected_arguments = sample.get("expected_trace_args", {})
        if not isinstance(arguments, dict) or any(arguments.get(key) != value
                                                  for key, value in expected_arguments.items()):
            continue
        if "expected_status" in sample and result.get("status") != sample["expected_status"]:
            continue
        if "expected_eligible" in sample and result.get("eligible") is not sample["expected_eligible"]:
            continue
        return True
    return False


class ProductService:
    project_id = "support-agent"
    environment_id = "local-test"

    def __init__(self, *, data_dir: Path | None = None, registry: ProductRegistry | None = None, telemetry: Any | None = None):
        root = Path(data_dir or config.DATA).resolve()
        self.registry = registry or ProductRegistry(root / "product.sqlite", root / "product-artifacts")
        self.telemetry = telemetry
        from integrations.support_agent.manifest import get_manifest

        self.manifest = dict(get_manifest())
        self.baseline_repo = (config.ROOT / self.manifest["source_path"]).resolve()
        self.adapter_repo = (config.ROOT / ".runtime" / "external-projects" / "support-agent-adapted").resolve()
        self.adapter_metadata = self._load_adapter_metadata()
        self.adapter_enabled = bool(self.adapter_metadata and (self.adapter_repo / ".venv" / "bin" / "uvicorn").exists())
        self.repo = self.adapter_repo if self.adapter_enabled else self.baseline_repo
        self.runtime_commit = str(self.adapter_metadata["adapter_commit"]) if self.adapter_enabled else str(self.manifest["commit"])
        runtime_manifest = {**self.manifest, "commit": self.runtime_commit}
        self.runtime = runtime_from_manifest(
            runtime_manifest, self.repo, root / "product-runtime", root / "product-external" / "support-agent.sqlite",
            root / "product-runtime" / "support-agent.log",
        )
        self._last_connection: dict[str, Any] | None = None
        self.bootstrap()
        self._import_existing_bundles()

    def _load_adapter_metadata(self) -> dict[str, Any] | None:
        path = self.adapter_repo.parent / "support-agent-adapted-manifest.json"
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, ValueError):
            return None
        if value.get("base_commit") != self.manifest["commit"] or not value.get("adapter_commit"):
            return None
        return value

    def _model_env(self) -> dict[str, str]:
        """Build the external process environment without persisting secrets."""
        if not self.adapter_enabled:
            return {}
        result = {
            "SECTION9_MODEL_URL": config.MODEL_URL,
            "SECTION9_MODEL": config.MODEL,
        }
        if config.MODEL_KEY:
            result["SECTION9_MODEL_API_KEY"] = config.MODEL_KEY
        if self.adapter_metadata.get("observability_sha256"):
            result.update({
                "LANGFUSE_BASE_URL": os.getenv("S9_LANGFUSE_URL", "http://127.0.0.1:9030"),
                "LANGFUSE_PUBLIC_KEY": os.getenv("LANGFUSE_INIT_PROJECT_PUBLIC_KEY", ""),
                "LANGFUSE_SECRET_KEY": os.getenv("LANGFUSE_INIT_PROJECT_SECRET_KEY", ""),
                "SUPPORT_AGENT_ENVIRONMENT": self.environment_id,
                "SUPPORT_AGENT_SOURCE_COMMIT": self.runtime_commit,
            })
        return result

    def _configuration_sha256(self) -> str:
        endpoint = urlsplit(config.MODEL_URL)
        binding = {
            "project_id": self.project_id,
            "environment_id": self.environment_id,
            "model": config.MODEL,
            "endpoint": {"scheme": endpoint.scheme, "hostname": endpoint.hostname,
                         "port": endpoint.port, "path": endpoint.path},
            "credential_configured": bool(config.MODEL_KEY),
            "credential_revision": config.MODEL_CREDENTIAL_REVISION,
            "adapter_enabled": self.adapter_enabled,
            "adapter_commit": (self.adapter_metadata or {}).get("adapter_commit"),
            "adapter_package_version": (self.adapter_metadata or {}).get("package_version"),
        }
        return _hash(binding)

    @staticmethod
    def _require_versioned_credential_binding() -> None:
        revision = str(config.MODEL_CREDENTIAL_REVISION or "").strip()
        if config.MODEL_KEY and revision.lower() in {"", "unversioned", "unknown", "none"}:
            raise ProductError("CONFIGURATION_BINDING_UNVERSIONED",
                               "模型凭据已配置但凭据版本未标记；拒绝批准或执行", 409)

    def bootstrap(self) -> dict[str, Any]:
        project = self.registry.ensure_project(self.manifest)
        if not self.registry.list("connection", project_id=self.project_id, environment_id=self.environment_id):
            self.registry.event("product.project.registered", {"name": self.manifest["name"], "commit": self.manifest["commit"],
                              "license": self.manifest["license"], "summary": "外部项目清单已锁定，尚未宣称业务接通"},
                                project_id=self.project_id, environment_id=self.environment_id)
        return project

    def _import_existing_bundles(self) -> None:
        source_root = config.ROOT / "artifacts" / "external-support-agent"
        target_root = self.registry.artifact_root / "support-agent"
        names = ("g1-business-probe.json", "g1-business-probe.sha256", "g2-regression.json",
                 "g2-regression.sha256", "g3-execution.json", "g3-execution.sha256",
                 "g3-recovery.json", "g3-recovery.sha256")
        for source_dir in source_root.glob("incident_*"):
            if not source_dir.is_dir():
                continue
            for name in names:
                source = source_dir / name
                destination = target_root / source_dir.name / name
                if source.is_file() and not destination.exists():
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, destination)

    def _write_bundle(self, incident_id: str, records: list[Any], bundle_name: str) -> None:
        ArtifactBundleWriter(self.registry.artifact_root / "support-agent" / incident_id).write(
            records, bundle_name=bundle_name
        )
        output = config.ROOT / "artifacts" / "external-support-agent" / incident_id
        ArtifactBundleWriter(output).write(records, bundle_name=bundle_name)

    def _source(self) -> dict[str, Any]:
        try:
            state = self.runtime.git_state()
            state["upstream_commit"] = self.manifest["commit"]
            state["adapter_enabled"] = self.adapter_enabled
            if self.adapter_metadata:
                state["adapter_commit"] = self.adapter_metadata.get("adapter_commit")
                state["adapter_package_version"] = self.adapter_metadata.get("package_version")
            return state
        except Exception as exc:
            return {"status": "unknown", "error": type(exc).__name__}

    def _capability(self, name: str, status: str, detail: str, *, checked_at: str | None = None,
                    technical: dict[str, Any] | None = None) -> dict[str, Any]:
        item = ConnectionCapability(
            project_id=self.project_id, environment_id=self.environment_id,
            capability_id=f"cap_{name}", provider="support-agent", capability=name,
            checked_at=checked_at or _now(), status=status, detail=detail,
        ).model_dump(mode="json")
        item["technical"] = technical or {}
        self.registry.put("connection", item["capability_id"], item, project_id=self.project_id, environment_id=self.environment_id)
        return item

    async def connect(self, *, run_tests: bool = True) -> dict[str, Any]:
        started = time.monotonic()
        capabilities: list[dict[str, Any]] = []
        try:
            start_info = await asyncio.to_thread(self.runtime.start, require_clean=True,
                                                  model_env={"PYTHONUNBUFFERED": "1", **self._model_env()})
        except Exception as exc:
            detail = f"运行时未启动：{type(exc).__name__}"
            capabilities.append(self._capability("runtime", "degraded", detail, technical={"error": str(exc)[:240]}))
            return {"status": "degraded", "project": self.manifest, "capabilities": capabilities,
                    "elapsed_s": round(time.monotonic() - started, 3)}

        source = self._source()
        source_status = "ready" if source.get("matches_expected") and source.get("clean") else "unknown"
        capabilities.append(self._capability("source", source_status,
                                              "当前运行源码与锁定提交一致" if source_status == "ready" else "源码版本或工作树状态无法确认",
                                              technical=source))
        try:
            observations = await self._probe_with_retry()
            health, readiness = observations
            health_ok = health.get("status_code") == 200 and health.get("response", {}).get("status") == "ok"
            readiness_status = "ready" if readiness.get("status_code") == 200 else "unsupported" if readiness.get("status_code") == 404 else "degraded"
            capabilities.append(self._capability("health", "ready" if health_ok else "degraded",
                                                  "外部应用健康接口返回业务状态" if health_ok else "健康接口未返回预期真值",
                                                  technical=health))
            capabilities.append(self._capability("readiness", readiness_status,
                                                  "候选项目未声明 /ready；不能把 404 当作健康" if readiness_status == "unsupported" else "就绪探针已返回",
                                                  technical=readiness))
        except Exception as exc:
            capabilities.append(self._capability("health", "unknown", "健康探针未取得结果", technical={"error": type(exc).__name__}))
            capabilities.append(self._capability("readiness", "unknown", "就绪探针未取得结果", technical={"error": type(exc).__name__}))
        if run_tests:
            tests = await asyncio.to_thread(self.runtime.run_tests)
            capabilities.append(self._capability("upstream_tests", "ready" if tests["status"] == "passed" else tests["status"],
                                                  "upstream 测试完成" if tests["status"] == "passed" else "upstream 测试未通过或超时",
                                                  technical={k: v for k, v in tests.items() if k not in {"stdout", "stderr"}}))
        adapter_status = "ready" if self.adapter_enabled and config.MODEL_KEY and config.MODEL_URL else "unknown" if self.adapter_enabled else "unsupported"
        capabilities.append(self._capability("model_adapter", adapter_status,
                                              "Section9 OpenAI-compatible适配已启用" if adapter_status == "ready" else
                                              "适配工作树已存在，但运行时模型凭据未配置" if adapter_status == "unknown" else
                                              "候选项目仅保留原生 Gemini/Groq，尚未接入 Section9 模型适配",
                                              technical={"enabled": self.adapter_enabled, "credential_refs": self.manifest.get("adapter_credential_refs", [])}))
        capabilities.append(self._capability("business_probe", "unknown", "尚未在受控模型适配下运行业务真值探针；无新日志不等于健康",
                                              technical={"credential_refs": self.manifest["model_credential_refs"], "model_adapter": "not_enabled"}))
        try:
            async with LangfuseConnector() as langfuse:
                langfuse_health = await langfuse.health()
                langfuse_read = await langfuse.observations(limit=1)
            langfuse_status = "ready" if langfuse_read.get("status_code") == 200 else "permission_denied" if langfuse_read.get("status_code") in {401, 403} else "unknown"
            capabilities.append(self._capability("langfuse", langfuse_status,
                                                  "Section9 只读连接器已取得 Langfuse 观察记录" if langfuse_status == "ready" else
                                                  "Langfuse 服务可达，但只读项目凭据未获准" if langfuse_status == "permission_denied" else
                                                  "Langfuse 连接器未取得可读观察记录",
                                                  technical={"health": langfuse_health, "read": {k: v for k, v in langfuse_read.items() if k != "rows"},
                                                             "native_candidate_sdk": False}))
        except Exception as exc:
            capabilities.append(self._capability("langfuse", "unknown", "Langfuse 连接器读取失败；不把服务可达当作证据可用",
                                                  technical={"error": type(exc).__name__, "native_candidate_sdk": False}))
        result = {"status": "ready" if all(x["status"] == "ready" for x in capabilities if x["capability"] in {"source", "health", "upstream_tests"}) else "degraded",
                  "project": self.manifest, "capabilities": capabilities, "started": start_info,
                  "elapsed_s": round(time.monotonic() - started, 3), "source": source,
                  "configuration_sha256": self._configuration_sha256()}
        self._last_connection = result
        self.registry.event("product.connection.checked", {"status": result["status"], "capabilities": [
            {"capability": x["capability"], "status": x["status"]} for x in capabilities]},
            project_id=self.project_id, environment_id=self.environment_id)
        return result

    def snapshot(self) -> dict[str, Any]:
        connections = self.registry.list("connection", project_id=self.project_id, environment_id=self.environment_id)
        incidents = self.registry.list("incident", project_id=self.project_id, environment_id=self.environment_id)
        changes = self.registry.list("candidate_change", project_id=self.project_id, environment_id=self.environment_id)
        verifications = self.registry.list("verification", project_id=self.project_id, environment_id=self.environment_id)
        approvals = self.registry.list("approval", project_id=self.project_id, environment_id=self.environment_id)
        runtime_state = {**self.runtime.status(), "port": self.runtime.port, "source": self._source()}
        persisted = {item.get("capability"): item for item in connections}
        required = ("source", "health", "upstream_tests")
        persisted_ready = all(persisted.get(name, {}).get("status") == "ready" for name in required)
        if runtime_state.get("status") == "running" and persisted_ready:
            overall_status = "ready"
        elif connections and runtime_state.get("status") != "running":
            overall_status = "stopped"
        elif any(persisted.get(name, {}).get("status") in {"degraded", "failed"} for name in required):
            overall_status = "degraded"
        else:
            overall_status = "unknown"
        return {"status": overall_status, "runtime_configuration_sha256": self._configuration_sha256(),
                "project": self.manifest, "selected_project": {"id": self.project_id, "name": self.manifest["name"]},
                "selected_environment": {"id": self.environment_id, "name": "本地测试环境"},
                "connections": connections, "incidents": incidents, "issues": incidents,
                "changes": changes, "verifications": verifications, "approvals": approvals,
                "runtime": runtime_state,
                "events": self.registry.events(project_id=self.project_id, environment_id=self.environment_id)}

    def _new_incident(self, title: str, *, status: str = "observed", summary: str = "") -> dict[str, Any]:
        incident_id = f"incident_{int(time.time_ns())}"
        item = {"id": incident_id, "title": title, "status": status, "summary": summary,
                "opened_at": _now(), "updated_at": _now(), "project_id": self.project_id,
                "environment_id": self.environment_id, "runtime_configuration_sha256": self._configuration_sha256(),
                "evidence": [], "repair": {"status": "unknown", "label": "尚未形成候选"},
                "verification": {"status": "unknown", "label": "尚未独立验收"},
                "approval": {"status": "unknown", "label": "尚未批准"},
                "governance": {"status": "ready", "policy": "local-test / L0-L1 only", "reviewRequired": True}}
        self.registry.put("incident", incident_id, item, project_id=self.project_id, environment_id=self.environment_id)
        self.registry.event("incident.opened", {"title": title, "summary": summary}, project_id=self.project_id,
                            environment_id=self.environment_id, incident_id=incident_id)
        return item

    def _evidence(self, incident: dict[str, Any], kind: str, value: Any, status: str, source: str) -> dict[str, Any]:
        captured = _now()
        ref = EvidenceRef(project_id=self.project_id, environment_id=self.environment_id, incident_id=incident["id"],
                          evidence_id=f"evidence_{int(time.time_ns())}", kind=kind, uri=source, captured_at=captured,
                          content_sha256=_hash(value), status=status).model_dump(mode="json")
        ref["value"] = value
        self.registry.put("evidence", ref["evidence_id"], ref, project_id=self.project_id, environment_id=self.environment_id,
                          incident_id=incident["id"])
        incident["evidence"].append(ref)
        incident["updated_at"] = captured
        self.registry.put("incident", incident["id"], incident, project_id=self.project_id, environment_id=self.environment_id)
        self.registry.event("evidence.captured", {"evidence_id": ref["evidence_id"], "kind": kind, "status": status},
                            project_id=self.project_id, environment_id=self.environment_id, incident_id=incident["id"])
        return ref

    async def _probe_with_retry(self, attempts: int = 20) -> list[dict[str, Any]]:
        last: list[dict[str, Any]] | None = None
        for attempt in range(attempts):
            try:
                last = await self.runtime.probe(self.project_id, self.environment_id)
                if last[0].get("status_code") == 200:
                    return last
            except Exception:
                if attempt == attempts - 1:
                    raise
            await asyncio.sleep(0.25)
        return last or []

    async def business_probe(self, incident_id: str | None = None, sample_name: str = "order_truth") -> dict[str, Any]:
        samples = {item.get("name"): item for item in self.manifest.get("business_truth_samples", [])
                   if isinstance(item, dict)}
        sample = samples.get(sample_name)
        if sample is None or sample_name not in {"order_truth", "refund_guardrail"}:
            raise ProductError("UNKNOWN_BUSINESS_SAMPLE", "未声明的业务真值样本", 422)
        message = (sample.get("request") or {}).get("message")
        if not isinstance(message, str) or not message.strip() or sample.get("side_effect") != "none":
            raise ProductError("INVALID_BUSINESS_SAMPLE", "业务真值样本不是有效只读请求", 422)
        if self.runtime.status().get("status") != "running":
            raise ProductError("EXTERNAL_RUNTIME_STOPPED", "外部运行时未连接；请先接入并核验版本，再运行业务探针", 409)
        if incident_id:
            incident = self.registry.get("incident", incident_id, project_id=self.project_id, environment_id=self.environment_id)
            if not incident:
                raise ProductError("NOT_FOUND", "事故不存在", 404)
        else:
            title = "退款资格只读边界探针" if sample_name == "refund_guardrail" else "外部客服订单状态业务探针"
            incident = self._new_incident(title, summary=f"只读业务真值样本 {sample_name}；核验工具 trace 与业务结果")
        source = self.runtime.source_binding(self.project_id, self.environment_id)
        cfg = ConnectorConfig(f"http://127.0.0.1:{self.runtime.port}", self.project_id, self.environment_id, source, timeout_seconds=120)
        started_ns = time.time_ns()
        try:
            async with ExternalHTTPConnector(cfg) as connector:
                response = await connector.chat(message, session_id=f"s9-{incident['id']}")
            ended_ns = time.time_ns()
            body = response.get("response")
            ok = response.get("status_code") == 200 and isinstance(body, dict) and all(k in body for k in ["session_id", "reply", "agent", "trace", "usage"])
            trace = body.get("trace", []) if isinstance(body, dict) else []
            truth = _matches_business_truth(sample, body)
            status = "ready" if ok and truth else "unknown"
            detail = ("业务真值与允许的只读工具 trace 均满足" if status == "ready"
                      else "HTTP 或声明的业务真值/只读工具 trace 未取得；不把响应存在当作恢复")
            application_trace_id = body.get("langfuse_trace_id") if isinstance(body, dict) else None
            # Accept only the application SDK's opaque trace identifier, never a URL.
            import re
            if not isinstance(application_trace_id, str) or not re.fullmatch(r"[0-9a-f]{32}", application_trace_id):
                application_trace_id = None
            external_trace_id = None
            if self.telemetry:
                external_trace_id = self.telemetry.record(
                    "s9.external.support-agent.chat", start_ns=started_ns, end_ns=ended_ns,
                    input_data={"path": "/chat", "message": message, "business_sample": sample_name},
                    output_data={"status_code": response.get("status_code"), "trace": trace, "usage": body.get("usage") if isinstance(body, dict) else {}},
                    metadata={"project_id": self.project_id, "environment_id": self.environment_id,
                              "incident_id": incident["id"], "business_sample": sample_name,
                              "source_commit": source.commit,
                              "configuration_sha256": self._configuration_sha256(), "external_trace": True},
                    usage=body.get("usage") if isinstance(body, dict) else None,
                )
            if external_trace_id:
                response = {**response, "section9_trace_id": external_trace_id}
            # The observer's request receipt is distinct from target internals.
            external_trace_id = application_trace_id or external_trace_id
            native_candidate_sdk = False
            ref = self._evidence(incident, "business_trace", response, status, "/chat")
            langfuse_ref = None
            langfuse_read: dict[str, Any] = {"status": "unknown", "rows": []}
            if external_trace_id:
                try:
                    if self.telemetry:
                        await asyncio.to_thread(self.telemetry.flush)
                    async with LangfuseConnector() as langfuse:
                        # The OTEL collector and Langfuse query worker are
                        # asynchronous.  A successful HTTP 200 with no rows
                        # is therefore retryable for this exact trace, but it
                        # never becomes ready unless a row is observed.
                        for attempt in range(4):
                            langfuse_read = await langfuse.observations(trace_id=external_trace_id, limit=100,
                                                                     include_context=bool(application_trace_id))
                            if langfuse_read.get("rows") or langfuse_read.get("status_code") in {401, 403}:
                                break
                            if attempt < 3:
                                await asyncio.sleep(2)
                    if application_trace_id:
                        # Never substitute Section9 telemetry or a different application.
                        rows = langfuse_read.get("rows", [])
                        scoped = [r for r in rows if r.get("trace_id") == application_trace_id
                                  and r.get("metadata", {}).get("project_id") == self.project_id
                                  and r.get("metadata", {}).get("environment_id") == self.environment_id
                                  and r.get("metadata", {}).get("source_commit") == source.commit
                                  and r.get("metadata", {}).get("telemetry_origin") == "application"]
                        langfuse_read["rows"] = scoped
                        native_candidate_sdk = bool(scoped)
                    lang_status = "ready" if langfuse_read.get("status_code") == 200 and langfuse_read.get("rows") else "unknown"
                    langfuse_ref = self._evidence(incident, "langfuse_call_tree", {
                        "trace_id": external_trace_id, "status": lang_status,
                        "rows": langfuse_read.get("rows", []), "watermark": langfuse_read.get("watermark")
                    }, lang_status, "/api/public/v2/observations")
                    incident["langfuse"] = {"status": lang_status, "trace_id": external_trace_id,
                                            "observation_count": len(langfuse_read.get("rows", [])),
                                            "watermark": langfuse_read.get("watermark"),
                                            "native_candidate_sdk": native_candidate_sdk,
                                            "telemetry_origin": "application" if native_candidate_sdk else "observer_sidecar" if not application_trace_id else "unverified_application"}
                except Exception as exc:
                    incident["langfuse"] = {"status": "unknown", "trace_id": external_trace_id,
                                            "error": type(exc).__name__, "native_candidate_sdk": False}
                    langfuse_ref = self._evidence(incident, "langfuse_call_tree", {
                        "trace_id": external_trace_id, "status": "unknown", "rows": [],
                        "error_type": type(exc).__name__
                    }, "unknown", "/api/public/v2/observations")
            lang_status = incident.get("langfuse", {}).get("status", "unknown")
            self._capability("langfuse", lang_status,
                              "被治理应用 SDK 已按 trace 取得内部调用与上下文" if native_candidate_sdk else
                              "仅取得观察者 sidecar 记录；尚未验证应用内部调用" if lang_status == "ready" else
                              "sidecar 可访问但当前 trace 未取得观察记录；保持 unknown",
                              technical={"trace_id": external_trace_id, "native_candidate_sdk": native_candidate_sdk,
                                         "observation_count": len(langfuse_read.get("rows", []))})
            probe = ProbeResult(project_id=self.project_id, environment_id=self.environment_id, incident_id=incident["id"],
                                probe_id=sample_name, capability_id="cap_business_probe", probed_at=_now(), status=status,
                                response_sha256=_hash(response), detail=detail).model_dump(mode="json")
            self.registry.put("probe", probe["probe_id"] + "_" + incident["id"], probe, project_id=self.project_id,
                              environment_id=self.environment_id, incident_id=incident["id"])
            incident["summary"] = detail
            incident["status"] = "observed" if status == "ready" else "evidence_insufficient"
            incident["business_probe"] = probe
            incident["source_binding"] = source.as_dict()
            incident["source_binding_id"] = "binding_" + _hash(source.as_dict())[:16]
            incident["runtime_configuration_sha256"] = self._configuration_sha256()
            self.registry.put("incident", incident["id"], incident, project_id=self.project_id, environment_id=self.environment_id)
            self.registry.event("product.external.business_probe", {"status": status, "business_sample": sample_name,
                                "trace_id": external_trace_id,
                                "source_commit": source.commit, "langfuse_status": incident.get("langfuse", {}).get("status", "unknown")}, project_id=self.project_id,
                               environment_id=self.environment_id, incident_id=incident["id"])
            result = {"incident": incident, "probe": probe, "evidence": ref}
            if langfuse_ref:
                result["langfuse_evidence"] = langfuse_ref
            self._capability("business_probe", status,
                              detail if status == "ready" else "业务探针返回但未同时取得声明真值与只读工具 trace",
                              technical={"incident_id": incident["id"], "trace_id": external_trace_id,
                                         "business_sample": sample_name,
                                         "langfuse_status": incident.get("langfuse", {}).get("status", "unknown")})
            self._write_bundle(incident["id"], [result], "g1-business-probe")
            return result
        except Exception as exc:
            ref = self._evidence(incident, "business_probe_error", {"error_type": type(exc).__name__, "detail": str(exc)[:240]}, "unknown", "/chat")
            incident["status"] = "evidence_insufficient"
            incident["summary"] = "业务探针失败；需要模型适配或权限恢复，未宣称业务健康"
            self.registry.put("incident", incident["id"], incident, project_id=self.project_id, environment_id=self.environment_id)
            result = {"incident": incident, "probe": {"status": "unknown", "error": type(exc).__name__}, "evidence": ref}
            self._capability("business_probe", "unknown", "业务探针异常；失败证据已保留",
                              technical={"incident_id": incident["id"], "error_type": type(exc).__name__})
            self._write_bundle(incident["id"], [result], "g1-business-probe")
            return result

    async def run_regression(self) -> dict[str, Any]:
        incident = self._new_incident("重复工具调用回退回归", status="investigating",
                                      summary="upstream 修复提交 708fd1d 的真实回归测试，当前候选提交 860a29f 验证")
        runner = RegressionRunner(self.runtime.root, self.runtime.root / ".venv" / "bin" / "python",
                                  config.ROOT / "integrations/support_agent/regression_test.py",
                                  config.ROOT / ".runtime" / "product-runner" / incident["id"])
        configuration_sha256 = self._configuration_sha256()
        result = await asyncio.to_thread(runner.run, project_id=self.project_id, environment_id=self.environment_id,
                                         incident_id=incident["id"], configuration_sha256=configuration_sha256)
        change = result["change"]
        receipt = result["verification"]
        self.registry.put("candidate_change", change["change_id"], change, project_id=self.project_id, environment_id=self.environment_id)
        self.registry.put("verification", receipt["receipt_id"], receipt, project_id=self.project_id, environment_id=self.environment_id)
        evidence = self._evidence(incident, "verification_bundle", result, "ready" if receipt["status"] == "ready" else "degraded",
                                   "artifacts/external-support-agent/g2")
        change["evidence_ids"].append(evidence["evidence_id"])
        receipt["evidence_ids"].append(evidence["evidence_id"])
        self.registry.put("candidate_change", change["change_id"], change, project_id=self.project_id, environment_id=self.environment_id)
        self.registry.put("verification", receipt["receipt_id"], receipt, project_id=self.project_id, environment_id=self.environment_id)
        self._write_bundle(incident["id"], [result], "g2-regression")
        incident.update({"status": "verified" if receipt["status"] == "ready" else "verification_degraded",
                         "summary": receipt["detail"], "change": change, "verification": receipt})
        self.registry.put("incident", incident["id"], incident, project_id=self.project_id, environment_id=self.environment_id)
        self.registry.event("verification.completed", {"receipt_id": receipt["receipt_id"], "status": receipt["status"],
                            "baseline": result["baseline"], "candidate": result["candidate"]}, project_id=self.project_id,
                            environment_id=self.environment_id, incident_id=incident["id"])
        return {"incident": incident, "change": change, "verification": receipt, "evidence": evidence, "result": result}

    def approve(self, incident_id: str) -> dict[str, Any]:
        incident = self.registry.get("incident", incident_id, project_id=self.project_id, environment_id=self.environment_id)
        if not incident:
            raise ProductError("NOT_FOUND", "事故不存在", 404)
        verification = incident.get("verification") or {}
        change = incident.get("change") or {}
        if verification.get("status") != "ready":
            raise ProductError("VERIFICATION_REQUIRED", "独立验收未通过，不能批准发布", 409)
        self._require_versioned_credential_binding()
        configuration_sha256 = self._configuration_sha256()
        if verification.get("configuration_sha256") != configuration_sha256:
            raise ProductError("CONFIGURATION_BINDING_MISMATCH", "模型或适配配置已变化，需要重新验收", 409)
        expires = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        approval = Approval(project_id=self.project_id, environment_id=self.environment_id, incident_id=incident_id,
                            approval_id=f"approval_{int(time.time_ns())}", approver="local-console-operator", approved_at=_now(),
                            decision="approved", change_id=change["change_id"], verification_receipt_id=verification["receipt_id"],
                            target_revision=self.runtime.expected_commit, policy_version="product-local-test-v1",
                            configuration_sha256=configuration_sha256, expires_at=expires).model_dump(mode="json")
        self.registry.put("approval", approval["approval_id"], approval, project_id=self.project_id, environment_id=self.environment_id,
                          incident_id=incident_id)
        incident["approval"] = {"status": "ready", "label": "已批准本地测试环境发布",
                                 "owner": "local-console-operator", "technical": approval}
        self.registry.put("incident", incident_id, incident, project_id=self.project_id, environment_id=self.environment_id)
        self.registry.event("approval.granted", {"approval_id": approval["approval_id"], "change_id": approval["change_id"],
                            "target_revision": approval["target_revision"]}, project_id=self.project_id,
                            environment_id=self.environment_id, incident_id=incident_id)
        return approval

    async def execute(self, incident_id: str, approval_id: str, idempotency_key: str) -> dict[str, Any]:
        self._require_versioned_credential_binding()
        incident = self.registry.get("incident", incident_id, project_id=self.project_id, environment_id=self.environment_id)
        if not incident:
            raise ProductError("NOT_FOUND", "事故不存在", 404)
        approval = self.registry.get("approval", approval_id, project_id=self.project_id, environment_id=self.environment_id, incident_id=incident_id)
        if not approval or approval.get("decision") != "approved":
            raise ProductError("APPROVAL_REQUIRED", "需要绑定本事故、候选和验收回执的批准", 403)
        if approval["expires_at"] < _now():
            raise ProductError("APPROVAL_EXPIRED", "批准已过期", 409)
        if approval.get("configuration_sha256") != self._configuration_sha256():
            raise ProductError("CONFIGURATION_BINDING_MISMATCH", "批准后模型或适配配置已变化", 409)
        existing = [x for x in self.registry.list("execution", project_id=self.project_id, environment_id=self.environment_id, incident_id=incident_id)
                    if x.get("idempotency_key") == idempotency_key]
        if existing:
            if existing[0].get("approval_id") != approval_id:
                raise ProductError("IDEMPOTENCY_CONFLICT", "幂等键已绑定不同批准", 409)
            return existing[0]
        source = self._source()
        if source.get("commit") != approval["target_revision"] or not source.get("clean"):
            raise ProductError("SOURCE_BINDING_MISMATCH", "实际运行源码不是批准的候选版本", 409)
        await asyncio.to_thread(self.runtime.stop)
        try:
            start_info = await asyncio.to_thread(self.runtime.start, require_clean=True,
                                                  model_env={"PYTHONUNBUFFERED": "1", **self._model_env()})
            observations = await self._probe_with_retry()
            health_ok = observations[0].get("status_code") == 200 and observations[0].get("response", {}).get("status") == "ok"
        except Exception as exc:
            start_info = {"status": "failed", "error": type(exc).__name__}
            health_ok = False
        started = _now()
        attempt = ExecutionAttempt(project_id=self.project_id, environment_id=self.environment_id, incident_id=incident_id,
                                   attempt_id=f"attempt_{int(time.time_ns())}", change_id=approval["change_id"], started_at=started,
                                   status="succeeded" if health_ok else "failed", idempotency_key=idempotency_key, target_revision=approval["target_revision"],
                                   configuration_sha256=approval["configuration_sha256"],
                                   external_operation_id=f"local-process-{self.runtime.port}", detail="本地测试环境已重启并绑定批准版本；未触发公网或真实业务副作用").model_dump(mode="json")
        attempt["approval_id"] = approval_id
        attempt["start"] = start_info
        attempt["health"] = observations if health_ok else {"status": "unknown"}
        bound_source = self.runtime.source_binding(self.project_id, self.environment_id)
        incident["source_binding"] = bound_source.as_dict()
        incident["source_binding_id"] = "binding_" + _hash(bound_source.as_dict())[:16]
        self.registry.put("execution", attempt["attempt_id"], attempt, project_id=self.project_id, environment_id=self.environment_id,
                          incident_id=incident_id)
        incident["execution"] = attempt
        incident["status"] = "observing" if health_ok else "manual_review"
        incident["approval"] = {"status": "ready", "label": "已发布到本地测试环境", "technical": approval}
        self.registry.put("incident", incident_id, incident, project_id=self.project_id, environment_id=self.environment_id)
        event_type = _execution_event_type(health_ok)
        self.registry.event(event_type, {"attempt_id": attempt["attempt_id"], "target_revision": attempt["target_revision"],
                                         "status": attempt["status"]},
                            project_id=self.project_id, environment_id=self.environment_id, incident_id=incident_id)
        self._write_bundle(incident_id, [attempt], "g3-execution")
        return attempt

    async def observe(self, incident_id: str) -> dict[str, Any]:
        incident = self.registry.get("incident", incident_id, project_id=self.project_id, environment_id=self.environment_id)
        if not incident:
            raise ProductError("NOT_FOUND", "事故不存在", 404)
        window_started = time.monotonic()
        health_samples: list[dict[str, Any]] = []
        for index in range(5):
            if index:
                await asyncio.sleep(1.0)
            observed_at = _now()
            try:
                observations = await self.runtime.probe(self.project_id, self.environment_id)
                health = observations[0] if observations else {}
                response = health.get("response") if isinstance(health, dict) else None
                health_status = response.get("status") if isinstance(response, dict) else None
                health_samples.append({"observed_at": observed_at,
                                       "status_code": health.get("status_code") if isinstance(health, dict) else None,
                                       "status": health_status,
                                       "passed": isinstance(health, dict) and health.get("status_code") == 200
                                       and health_status == "ok"})
            except Exception as exc:
                health_samples.append({"observed_at": observed_at, "status_code": None,
                                       "status": "unknown", "error_type": type(exc).__name__, "passed": False})
        observation_window_seconds = round(time.monotonic() - window_started, 3)
        health_window_passed = len(health_samples) == 5 and all(item["passed"] for item in health_samples)
        health_ref = self._evidence(incident, "runtime_observation_window",
                                    {"samples": health_samples, "sample_count": len(health_samples),
                                     "observation_window_seconds": observation_window_seconds,
                                     "all_health_samples_passed": health_window_passed},
                                    "ready" if health_window_passed else "unknown", "/health")
        try:
            probe = await self.business_probe(incident_id)
        except ProductError as exc:
            if exc.code != "EXTERNAL_RUNTIME_STOPPED":
                raise
            unavailable_ref = self._evidence(incident, "business_probe_unavailable",
                                             {"error_code": exc.code}, "unknown", "/chat")
            probe = {"incident": incident, "probe": {"status": "unknown", "error": exc.code},
                     "evidence": unavailable_ref}
        # business_probe persists new evidence on its own incident snapshot.
        # Reload before attaching the recovery receipt so its evidence links
        # and Langfuse pointer are not lost to this earlier in-memory copy.
        incident = self.registry.get("incident", incident_id,
                                     project_id=self.project_id, environment_id=self.environment_id) or incident
        configuration_matches = incident.get("execution", {}).get("configuration_sha256") == self._configuration_sha256()
        current_source = self._source()
        source_matches = (current_source.get("commit") == incident.get("execution", {}).get("target_revision")
                          and current_source.get("clean") is True)
        status = "recovered" if (health_window_passed and probe.get("probe", {}).get("status") == "ready"
                                  and configuration_matches and source_matches) else "unknown"
        if not incident.get("source_binding_id"):
            bound_source = self.runtime.source_binding(self.project_id, self.environment_id)
            incident["source_binding"] = bound_source.as_dict()
            incident["source_binding_id"] = "binding_" + _hash(bound_source.as_dict())[:16]
        receipt = RecoveryReceipt(project_id=self.project_id, environment_id=self.environment_id, incident_id=incident_id,
                                  receipt_id=f"recovery_{int(time.time_ns())}", attempt_id=incident.get("execution", {}).get("attempt_id", "unknown"),
                                  recovered_at=_now(), status=status, source_revision=current_source.get("commit", "unknown"),
                                  configuration_sha256=self._configuration_sha256(),
                                  evidence_ids=[health_ref["evidence_id"], probe["evidence"]["evidence_id"]],
                                  source_binding_id=incident.get("source_binding_id", "binding_unknown"),
                                  observation_window_seconds=observation_window_seconds,
                                  business_probe_passed=status == "recovered",
                                  detail="连续五次健康检查、一次独立业务真值探针及源码/运行配置绑定均通过后标记 recovered；未知结果保持 unknown").model_dump(mode="json")
        self.registry.put("recovery", receipt["receipt_id"], receipt, project_id=self.project_id, environment_id=self.environment_id,
                          incident_id=incident_id)
        incident["recovery"] = receipt
        incident["status"] = "recovered" if status == "recovered" else "manual_review"
        self.registry.put("incident", incident_id, incident, project_id=self.project_id, environment_id=self.environment_id)
        result = {"incident": incident, "recovery": receipt, "probe": probe}
        self._write_bundle(incident_id, [result], "g3-recovery")
        return result

    def stop(self) -> dict[str, Any]:
        return self.runtime.stop()
