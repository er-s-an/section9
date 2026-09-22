"""Local, evidence-gated memory for Section 9.

The store is intentionally independent of the EvoMap Hub.  Seeded playbooks
are suggestions only; a learned record is created only after the root has
supplied a passed verification result.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SIGNALS = ("quality_mismatch", "cost_high", "tool_stalled")
_SCHEMA_DIR = Path(__file__).resolve().parents[1] / "integrations" / "gep" / "node_modules" / "@evomap" / "gep-sdk" / "schemas"
_BRIDGE = Path(__file__).resolve().parents[1] / "integrations" / "gep" / "asset.mjs"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _short_id(prefix: str, value: Any) -> str:
    return f"{prefix}_{hashlib.sha256(_canonical(value).encode()).hexdigest()[:24]}"


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


class MemoryStore:
    def __init__(self, base_dir: Path):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._playbooks_path = self.base_dir / "playbooks.json"
        self._events_path = self.base_dir / "events.jsonl"
        self._error: str | None = None
        if self._playbooks_path.exists():
            try:
                self._playbooks = json.loads(self._playbooks_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                self._playbooks = self._seeded()
        else:
            self._playbooks = self._seeded()
            _atomic_json(self._playbooks_path, self._playbooks)

    @staticmethod
    def _seeded() -> list[dict[str, Any]]:
        def action(kind: str, values: dict[str, Any]) -> dict[str, Any]:
            return {"type": kind, "values": values}

        # Keep these structurally different.  They are candidates, not claims
        # that any of the strategies has already been run successfully.
        return [
            {"id": "seed_quality_prompt", "title": "恢复提示词质量", "symptoms": ["quality_mismatch"],
             "actions": [action("set_prompt_revision", {"prompt_version": "healthy"})], "reuse_count": 0, "success_count": 0,
             "source": "seeded", "publish_state": "local_only"},
            {"id": "seed_cost_budget", "title": "收紧成本预算", "symptoms": ["cost_high"],
             "actions": [action("apply_context_budget", {"context_multiplier": 1, "max_output_tokens": 1536})], "reuse_count": 0, "success_count": 0,
             "source": "seeded", "publish_state": "local_only"},
            {"id": "seed_stalled_retry", "title": "停止终端重试", "symptoms": ["tool_stalled"],
             "actions": [action("apply_retry_policy", {"retry_limit": 3, "retry_on_terminal": False})], "reuse_count": 0, "success_count": 0,
             "source": "seeded", "publish_state": "local_only"},
            {"id": "seed_composite_repair", "title": "联合质量停滞修复", "symptoms": ["quality_mismatch", "tool_stalled"],
             "actions": [action("apply_config_bundle", {"prompt_version": "healthy", "context_multiplier": 1,
                                                             "max_output_tokens": 1536, "retry_limit": 3,
                                                             "retry_on_terminal": False})], "reuse_count": 0,
             "success_count": 0, "source": "seeded", "publish_state": "local_only"},
        ]

    def list_playbooks(self) -> list[dict[str, Any]]:
        return json.loads(json.dumps(self._playbooks, ensure_ascii=False))

    def match(self, symptoms: list[str], config: dict[str, Any]) -> dict[str, Any] | None:
        observed = set(symptoms) & set(SIGNALS)
        if not observed:
            return None
        candidates = [p for p in self._playbooks if set(p["symptoms"]).issubset(observed)]
        if not candidates:
            return None
        # More required signals first makes the all-three composite beat any
        # single-signal candidate.  Reuse count breaks otherwise equal ties.
        candidates.sort(key=lambda p: (len(p["symptoms"]), p.get("reuse_count", 0), p["id"]), reverse=True)
        return json.loads(json.dumps(candidates[0], ensure_ascii=False))

    @staticmethod
    def _passed(run: dict[str, Any]) -> bool:
        verification = run.get("verification")
        return (verification is True) or (isinstance(verification, dict) and verification.get("passed") is True)

    def _official_assets(self, run: dict[str, Any], actions: list[dict[str, Any]], learned: dict[str, Any], reused: bool) -> dict[str, Any]:
        signals = [s for s in run.get("symptoms", []) if s in SIGNALS]
        model = run.get("model")
        strategy = [f"{a.get('type')}: {_canonical(a.get('values', {}))}" for a in actions]
        gene_id = _short_id("gene_local", {"signals": signals, "actions": actions})
        capsule_id = _short_id("capsule_local", {"gene": gene_id, "run": run.get("id"), "actions": actions})
        event_id = _short_id("event_local", {"capsule": capsule_id, "run": run.get("id")})
        source_type = "reused" if reused else "generated"
        gene = {"type": "Gene", "schema_version": "1.14.0", "id": gene_id, "category": "repair",
                "signals_match": signals, "strategy": strategy,
                "constraints": {"max_files": 1, "forbidden_paths": [".git", "node_modules", ".env", "CLAIMS.md"]},
                "validation": ["root_verified_probe_passed"], "summary": learned["title"]}
        capsule = {"type": "Capsule", "schema_version": "1.14.0", "id": capsule_id, "trigger": signals,
                   "gene": gene_id, "summary": f"Root verified repair for {', '.join(signals)}",
                   "confidence": 1.0, "blast_radius": {"files": 0, "lines": 0},
                   "outcome": {"status": "success", "score": 1.0}, "source_type": source_type,
                   "strategy": strategy, "execution_trace": [{"stage": "validate"}],
                   "trigger_context": {"context_signals": signals, "session_id": str(run.get("id", "")), "agent_model": model or "unknown"}}
        if reused:
            prior = learned.get("capsule_asset_id")
            if prior:
                capsule["reused_asset_id"] = prior
        event = {"type": "EvolutionEvent", "schema_version": "1.14.0", "id": event_id,
                 "intent": "repair", "signals": signals, "genes_used": [gene_id],
                 "mutation_id": _short_id("mutation_local", {"run": run.get("id"), "actions": actions}),
                 "blast_radius": {"files": 0, "lines": 0}, "outcome": {"status": "success", "score": 1.0},
                 "capsule_id": capsule_id, "source_type": source_type,
                 "trigger_context": {"context_signals": signals, "session_id": str(run.get("id", "")), "agent_model": model or "unknown"}}
        assets = self._bridge_assets([gene, capsule, event])
        return {"gene": assets[0], "capsule": assets[1], "evolution_event": assets[2]}

    def _bridge_assets(self, assets: list[dict[str, Any]]) -> list[dict[str, Any]]:
        try:
            result = subprocess.run(["node", str(_BRIDGE)], input=json.dumps({"assets": assets}), text=True,
                                    capture_output=True, check=True, cwd=_BRIDGE.parent, timeout=10)
            payload = json.loads(result.stdout)
            if payload.get("error") or len(payload.get("assets", [])) != len(assets):
                raise RuntimeError(payload.get("error", "invalid GEP bridge response"))
            for item in payload["assets"]:
                if not item.get("verified"):
                    raise RuntimeError("official asset_id verification failed")
            return [item["asset"] for item in payload["assets"]]
        except Exception as exc:
            self._error = f"GEP protocol validator unavailable: {exc}"
            raise RuntimeError(self._error) from exc

    def _schema_check(self, assets: list[dict[str, Any]]) -> None:
        try:
            from jsonschema import validate
            for asset in assets:
                schema = json.loads((_SCHEMA_DIR / {"Gene": "gene.schema.json", "Capsule": "capsule.schema.json",
                                                    "EvolutionEvent": "evolution-event.schema.json"}[asset["type"]]).read_text())
                validate(instance=asset, schema=schema)
        except Exception as exc:
            self._error = f"official GEP schema validation failed: {exc}"
            raise RuntimeError(self._error) from exc

    def _append(self, assets: list[dict[str, Any]]) -> None:
        self._events_path.parent.mkdir(parents=True, exist_ok=True)
        with self._events_path.open("a", encoding="utf-8") as stream:
            for asset in assets:
                stream.write(json.dumps(asset, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    def record_success(self, run: dict[str, Any], actions: list[dict[str, Any]]) -> dict[str, Any]:
        if not self._passed(run):
            raise ValueError("memory accepts only a root-verified passed run")
        if not run.get("id") or not run.get("model"):
            raise ValueError("verified run must include id and model")
        signals = [s for s in run.get("symptoms", []) if s in SIGNALS]
        if not signals:
            raise ValueError("verified run has no supported symptoms")
        normalized_actions = []
        for action in actions:
            if hasattr(action, "model_dump"):
                action = action.model_dump()
            if not isinstance(action, dict) or not action.get("type"):
                raise ValueError("actions must use the Section 9 Action format")
            normalized_actions.append({"type": action["type"], "values": action.get("values", {})})
        signature = _canonical({"symptoms": sorted(signals), "actions": normalized_actions})
        selected_id = run.get("memory_used_id")
        reuse_approved = run.get("reuseapproved") is True or run.get("reuse_approved") is True
        selected = next((p for p in self._playbooks if p.get("id") == selected_id), None)
        selected_signature = None
        if selected:
            selected_signature = selected.get("signature") or _canonical({
                "symptoms": sorted(selected.get("symptoms", [])), "actions": selected.get("actions", [])})
        reused = bool(reuse_approved and selected and selected_signature == signature)
        learned = selected if reused else next(
            (p for p in self._playbooks if p.get("source") == "learned" and p.get("signature") == signature), None)
        if learned is None:
            learned = {"id": _short_id("learned", signature), "title": "已验证本地修复 · " + "/".join(signals),
                       "symptoms": signals, "actions": normalized_actions, "reuse_count": 0, "success_count": 0,
                       "source": "learned",
                       "publish_state": "local_only", "signature": signature}
        assets = self._official_assets(run, normalized_actions, learned, reused)
        self._schema_check(list(assets.values()))
        # Only the root can assert that the selected candidate was actually
        # used.  A repeated, independently derived success is not reuse.
        learned["success_count"] = learned.get("success_count", 0) + 1
        if reused:
            learned["reuse_count"] = learned.get("reuse_count", 0) + 1
        learned["gene_asset_id"] = assets["gene"]["asset_id"]
        learned["capsule_asset_id"] = assets["capsule"]["asset_id"]
        if learned not in self._playbooks:
            self._playbooks.append(learned)
        _atomic_json(self._playbooks_path, self._playbooks)
        self._append(list(assets.values()))
        return {**assets, "playbook": json.loads(json.dumps(learned)), "publish_state": "local_only"}

    def status(self) -> dict[str, Any]:
        events = 0
        if self._events_path.exists():
            events = sum(1 for line in self._events_path.read_text(encoding="utf-8").splitlines() if line.strip())
        return {"playbooks": len(self._playbooks), "learned": sum(p.get("source") == "learned" for p in self._playbooks),
                "events": events, "publish_state": "local_only", "hub_status": "pending_auth",
                "pending_auth": True if events else False,
                "validator_error": self._error}
