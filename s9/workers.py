"""Autonomous Section9 workers.

Workers deliberately know only the scoped ``/agent`` API.  They do not import
the server, configuration loader, database, dotenv support, or operator logs.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
from dataclasses import dataclass, field
from typing import Any

import httpx


ROLES = {"sentry", "diagnoser", "fixer-a", "fixer-b", "verifier", "cost", "single"}
KINDS = {
    "sentry": "detect", "diagnoser": "diagnose", "fixer-a": "repair",
    "fixer-b": "repair", "verifier": "verify", "cost": "cost", "single": "single",
}
ACTION_TYPES = {"set_prompt_revision", "apply_context_budget", "apply_retry_policy", "apply_config_bundle", "rollback_action"}


class AgentError(RuntimeError):
    def __init__(self, status: int, body: Any):
        self.status, self.body = status, body
        super().__init__(f"agent request failed ({status}): {body}")


@dataclass
class Lease:
    task_id: str
    epoch: str
    run_id: str
    done: asyncio.Event = field(default_factory=asyncio.Event)
    plan_id: str | None = None


def _json_content(content: str) -> dict[str, Any]:
    """Extract a JSON object from a model response, tolerating fenced JSON."""
    text = content.strip()
    if text.startswith("``"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I | re.S).strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.S)
        if not match:
            return {}
        try:
            value = json.loads(match.group(0))
        except json.JSONDecodeError:
            return {}
    return value if isinstance(value, dict) else {}


def _validate_diagnosis(value: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    """Validate the complete diagnosis shape; never coerce malformed fields."""
    if not isinstance(value.get("root_cause"), str) or not value["root_cause"].strip():
        return None, "root_cause must be a non-empty string"
    confidence = value.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
        return None, "confidence must be a number between 0 and 1"
    evidence = value.get("evidence_ids")
    if not isinstance(evidence, list) or any(not isinstance(item, str) for item in evidence):
        return None, "evidence_ids must be a list of strings"
    checks = value.get("proposed_checks")
    if not isinstance(checks, list) or any(not isinstance(item, dict) for item in checks):
        return None, "proposed_checks must be a list of objects"
    return value, None


def _validate_repair(value: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    """Validate repair output without filtering invalid action entries."""
    if not isinstance(value.get("rationale"), str) or not value["rationale"].strip():
        return None, "rationale must be a non-empty string"
    evidence = value.get("evidence_ids")
    if not isinstance(evidence, list) or any(not isinstance(item, str) for item in evidence):
        return None, "evidence_ids must be a list of strings"
    actions = value.get("actions")
    if not isinstance(actions, list) or not 1 <= len(actions) <= 4:
        return None, "actions must contain 1 to 4 objects"
    for index, action in enumerate(actions):
        if not isinstance(action, dict):
            return None, f"actions[{index}] must be an object"
        if action.get("type") not in ACTION_TYPES:
            return None, f"actions[{index}].type is not allowed"
        if not isinstance(action.get("values"), dict):
            return None, f"actions[{index}].values must be an object"
    return value, None


class Worker:
    """One OS process, one role, and one authenticated collaboration client."""

    def __init__(self, worker_id: str, base_url: str, *, client: httpx.AsyncClient | None = None,
                 poll_interval: float = 0.6, heartbeat_interval: float = 2.0):
        if worker_id not in ROLES:
            raise ValueError(f"unknown worker id: {worker_id}")
        token = os.environ.get("S9_AGENT_TOKEN")
        if not token:
            raise RuntimeError("S9_AGENT_TOKEN is required")
        self.id = worker_id
        self.role = worker_id if worker_id == "sentry" else worker_id
        self.base_url = base_url.rstrip("/")
        self.headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        self.client = client
        self._owned_client = client is None
        self.poll_interval, self.heartbeat_interval = poll_interval, heartbeat_interval
        self._stop = asyncio.Event()
        self._lease: Lease | None = None
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._seen_incidents: set[tuple[str, ...]] = set()
        self._blocked: set[str] = set()
        self._attempts: dict[str, int] = {}

    async def _start(self) -> None:
        if self.client is None:
            self.client = httpx.AsyncClient(base_url=self.base_url, headers=self.headers, timeout=120, trust_env=False)
        self._heartbeat_task = asyncio.create_task(self._heartbeats(), name=f"{self.id}-heartbeat")

    async def close(self) -> None:
        self._stop.set()
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            await asyncio.gather(self._heartbeat_task, return_exceptions=True)
        if self._owned_client and self.client:
            await self.client.aclose()

    async def _request(self, method: str, path: str, body: dict[str, Any] | None = None,
                       *, tolerate: tuple[int, ...] = ()) -> dict[str, Any]:
        assert self.client is not None
        response = await self.client.request(method, path, json=body, headers=self.headers)
        try:
            payload = response.json()
        except ValueError:
            payload = {"error": {"code": "INVALID_JSON", "message": response.text}}
        if response.status_code >= 400 and response.status_code not in tolerate:
            raise AgentError(response.status_code, payload)
        return payload if isinstance(payload, dict) else {}

    async def _heartbeats(self) -> None:
        while not self._stop.is_set():
            try:
                context = await self._request("GET", "/agent/context")
                paused = bool(context.get("paused"))
                status = "paused" if paused else ("working" if self._lease else "idle")
                await self._request("POST", "/agent/heartbeat", {
                    "status": status, "detail": "等待协作任务" if not self._lease else "处理已认领任务",
                    "task_id": self._lease.task_id if self._lease else None,
                })
                # A paused worker keeps its grant, but never renews its lease.
                if self._lease and not paused and not self._lease.done.is_set():
                    await self._request("POST", "/agent/renew", {
                        "task_id": self._lease.task_id, "epoch": self._lease.epoch
                    }, tolerate=(409,))
            except asyncio.CancelledError:
                raise
            except (httpx.HTTPError, AgentError):
                if self._stop.is_set():
                    return
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.heartbeat_interval)
            except asyncio.TimeoutError:
                pass

    async def _context(self) -> dict[str, Any]:
        return await self._request("GET", "/agent/context")

    @staticmethod
    def _task_for(context: dict[str, Any], kind: str, blocked: set[str]) -> dict[str, Any] | None:
        for task in context.get("tasks", []):
            if task.get("id") in blocked:
                continue
            if task.get("kind") == kind and task.get("status", "open") in {"open", "pending", "available", "retry"}:
                return task
        return None

    async def _claim(self, task: dict[str, Any]) -> Lease | None:
        try:
            result = await self._request("POST", "/agent/claim", {"task_id": task["id"]}, tolerate=(409,))
        except AgentError:
            return None
        if not result.get("task_id"):
            return None
        lease = Lease(str(result["task_id"]), str(result["epoch"]), str(result.get("run_id", "")))
        self._lease = lease
        return lease

    async def _complete(self, lease: Lease, summary: str) -> None:
        try:
            await self._request("POST", "/agent/task/complete", {
                "task_id": lease.task_id, "epoch": lease.epoch, "result_summary": summary[:2000]
            })
        finally:
            lease.done.set()
            if self._lease is lease:
                self._lease = None

    async def _safe_complete(self, lease: Lease, summary: str) -> None:
        """Completion is best effort: a resolved/reset task must not kill a worker."""
        try:
            await self._complete(lease, summary)
        except (AgentError, httpx.HTTPError, asyncio.TimeoutError):
            lease.done.set()
            if self._lease is lease:
                self._lease = None

    @staticmethod
    def _ready(context: dict[str, Any], worker_id: str) -> bool:
        """Check peer/action prerequisites before acquiring a lease."""
        if worker_id in {"diagnoser", "fixer-a", "fixer-b", "single"} and context.get("detection_pending"):
            return False
        if worker_id in {"fixer-a", "fixer-b"}:
            if context.get("muted"):
                return True  # Same repair capability, but only its own projected evidence.
            return any(m.get("kind") in {"hypothesis", "synthesize"} for m in context.get("messages", []))
        if worker_id == "single":
            return True
        if worker_id == "verifier":
            return bool(context.get("last_action"))
        return True

    async def _model(self, context: dict[str, Any], purpose: str, messages: list[dict[str, str]], max_tokens: int = 1600) -> dict[str, Any]:
        return await self._request("POST", "/agent/model", {
            "run_id": str(context.get("incident", {}).get("run_id", "")),
            "purpose": purpose, "messages": messages[:12], "max_tokens": max_tokens,
        })

    async def _message(self, context: dict[str, Any], lease: Lease, kind: str, content: str,
                       evidence: list[str] | None = None, confidence: float = .5) -> None:
        agent = context.get("agent") or {}
        generation = context.get("generation", context.get("config", {}).get("generation", ""))
        transport_epoch = context.get("transport_epoch", agent.get("transport_epoch", ""))
        await self._request("POST", "/agent/message", {
            "run_id": str(context.get("incident", {}).get("run_id", "")), "kind": kind,
            "content": content[:6000], "evidence_ids": evidence or [], "confidence": confidence,
            "task_id": lease.task_id, "task_epoch": lease.epoch,
            "instance_id": str(context.get("instance_id", agent.get("instance_id", ""))),
            "generation": str(generation), "transport_epoch": str(transport_epoch),
        })

    async def _sentry(self, context: dict[str, Any]) -> None:
        observations = context.get("observations", [])
        ids = tuple(str(item.get("id")) for item in observations if item.get("id"))
        if ids and ids not in self._seen_incidents:
            self._seen_incidents.add(ids)
            await self._request("POST", "/agent/incidents", {"observation_ids": list(ids)})

    async def _diagnose(self, context: dict[str, Any], lease: Lease) -> str:
        evidence = [str(o.get("id")) for o in context.get("observations", []) if o.get("id")]
        prompt = ("You are the diagnoser. Return a JSON object with root_cause (non-empty string), confidence (number 0..1), "
                  "evidence_ids (array of strings), and proposed_checks (array of objects, each like {\"check\":\"description\"}). "
                  "No field may be null. Diagnose only from the projected role config, known-good policy, "
                  "and observations; do not invent scenario truth.")
        projected = {"config": context.get("config", {}), "known_good": context.get("known_good", {}),
                     "observations": context.get("observations", [])}
        result = await self._model(context, "diagnose", [{"role": "system", "content": prompt},
            {"role": "user", "content": json.dumps(projected, ensure_ascii=False)}])
        answer, error = _validate_diagnosis(_json_content(str(result.get("content", ""))))
        if error:
            # One bounded repair request for malformed model output.
            result = await self._model(context, "diagnose", [{"role": "system", "content": prompt + " Repair the schema error: " + error},
                {"role": "user", "content": json.dumps({"facts": projected, "previous_output": result.get("content", "")}, ensure_ascii=False)}])
            answer, error = _validate_diagnosis(_json_content(str(result.get("content", ""))))
        if error or answer is None:
            summary = f"failed: invalid diagnosis output after two model calls ({error})"
            await self._message(context, lease, "result", summary, evidence, 0.0)
            return summary
        cause = answer["root_cause"]
        confidence = answer["confidence"]
        ids = answer["evidence_ids"]
        await self._message(context, lease, "hypothesis", json.dumps({"root_cause": cause, "proposed_checks": answer["proposed_checks"]}, ensure_ascii=False), ids, confidence)
        await self._message(context, lease, "challenge", json.dumps({"challenge": f"请核对该假设并寻找反例：{cause}", "evidence_ids": ids}, ensure_ascii=False), ids, max(0.1, confidence - .1))
        return cause

    async def _fix(self, context: dict[str, Any], lease: Lease, single: bool = False) -> str:
        messages = context.get("messages", [])
        if not single and context.get("muted"):
            return "blocked: missing peer evidence while communication is muted"
        if not single and not self._ready(context, self.id):
            return "waiting: peer conclusion or detection completion required"
        facts = {"role": self.role, "config": context.get("config", {}),
                 "known_good": context.get("known_good", {}), "memory": context.get("memory"),
                 "observations": context.get("observations", []),
                 "messages": messages if single or not context.get("muted") else []}
        schema = {"allowed_action_types": sorted(ACTION_TYPES), "action_example": {"type": "apply_retry_policy", "values": {"retry_limit": 2}},
                  "action_values": {"set_prompt_revision": {"prompt_version": "healthy|degraded"},
                                    "apply_context_budget": {"context_multiplier": "int 1..8", "max_output_tokens": "int 64..4096"},
                                    "apply_retry_policy": {"retry_limit": "int 0..6", "retry_on_terminal": "boolean"},
                                    "apply_config_bundle": "union of these fields; do not add other fields"}}
        result = await self._model(context, "single" if single else "repair", [
            {"role": "system", "content": "Propose a minimal valid repair as JSON with rationale (non-empty string), evidence_ids (array of strings), and actions (1 to 4 action objects). No null fields. Repair observed behavior, never disable safety. Allowed action schema: " + json.dumps(schema, ensure_ascii=False)},
            {"role": "user", "content": json.dumps(facts, ensure_ascii=False)}])
        answer, error = _validate_repair(_json_content(str(result.get("content", ""))))
        if error:
            result = await self._model(context, "single" if single else "repair", [
                {"role": "system", "content": "Repair the schema error: " + error + ". Return only JSON with rationale (non-empty string), evidence_ids (string array), actions (1 to 4 objects). Allowed schema: " + json.dumps(schema)},
                {"role": "user", "content": json.dumps({"facts": facts, "previous_output": result.get("content", "")}, ensure_ascii=False)}])
            answer, error = _validate_repair(_json_content(str(result.get("content", ""))))
        if error or answer is None:
            summary = f"failed: invalid repair output after two model calls ({error})"
            await self._message(context, lease, "result", summary, [], .0)
            return summary
        # This contribution is grounded in this worker's own model output and
        # the delivered peer evidence. Never pretend a scripted challenge was answered.
        evidence = answer["evidence_ids"]
        rationale = answer["rationale"]
        actions = answer["actions"]
        await self._message(context, lease, "build_on" if messages else "hypothesis", rationale, evidence, .7)
        await self._message(context, lease, "synthesize", json.dumps({"actions": actions, "rationale": rationale}, ensure_ascii=False), evidence, .7)
        incident = context.get("incident") or {}
        plan = await self._request("POST", "/agent/plan", {
            "run_id": str(incident.get("run_id", "")), "task_id": lease.task_id, "task_epoch": lease.epoch,
            "expected_revision": str(context.get("config", {}).get("revision", "")), "actions": actions,
            "rationale": answer["rationale"], "evidence_ids": answer["evidence_ids"],
            "instance_id": str(context.get("instance_id", "")),
            "generation": str(context.get("generation", context.get("config", {}).get("generation", ""))),
            "transport_epoch": str(context.get("transport_epoch", "")),
        })
        lease.plan_id = str(plan.get("id")) if plan.get("id") else None
        if not plan.get("id"):
            summary = "blocked: plan was not accepted"
            await self._message(context, lease, "result", summary, [], .0)
            return summary
        # Keep the plan and lease while policy/approval is pending. This loop
        # never invokes the model again; heartbeat renews while it waits.
        while True:
            try:
                grant = await self._request("POST", "/agent/grant", {"plan_id": str(plan["id"])})
                code = grant.get("error", {}).get("code") if isinstance(grant, dict) else None
                if code in {"APPROVAL_REQUIRED", "POLICY_DENIED"}:
                    await asyncio.sleep(self.poll_interval)
                    continue
                break
            except AgentError as exc:
                code = exc.body.get("error", {}).get("code") if isinstance(exc.body, dict) else None
                if code not in {"APPROVAL_REQUIRED", "POLICY_DENIED"}:
                    raise
                await asyncio.sleep(self.poll_interval)
        grant_id = grant.get("grant_id")
        if not grant_id:
            summary = "blocked: no grant"
            await self._message(context, lease, "result", summary, [], .0)
            return summary
        # Fencing deliberately retains this exact grant while paused.
        while True:
            latest = await self._context()
            if not latest.get("paused"):
                break
            await asyncio.sleep(self.poll_interval)
        executed = await self._request("POST", "/agent/execute", {
            "plan_id": str(plan["id"]), "grant_id": str(grant_id),
            "idempotency_key": f"{self.id}:{lease.task_id}:{lease.epoch}",
        }, tolerate=(409,))
        if executed.get("status") in {"stale", "rejected"} or executed.get("error"):
            summary = "stale grant rejected; abandoning old plan"
            await self._message(context, lease, "result", summary, [], .0)
            return summary
        action_summary = f"executed action {executed.get('action_id', 'unknown')}"
        await self._message(context, lease, "result", action_summary, answer["evidence_ids"], .8)
        return action_summary

    async def _verify(self, context: dict[str, Any], lease: Lease) -> str:
        if not context.get("last_action"):
            return "waiting: no last_action to verify"
        result = await self._request("POST", "/agent/verify", {"run_id": lease.run_id})
        summary = "verified" if result.get("passed") else "verification failed: " + str(result.get("summary", result))
        # The verification endpoint emits the authoritative business result;
        # a second message would race terminal closure and create RUN_STALE noise.
        return summary

    async def _handle(self, context: dict[str, Any], lease: Lease) -> str:
        if self.id == "sentry":
            await self._sentry(context)
            return "observations submitted"
        if self.id == "diagnoser":
            return await self._diagnose(context, lease)
        if self.id in {"fixer-a", "fixer-b"}:
            return await self._fix(context, lease)
        if self.id == "single":
            summary = await self._fix(context, lease, True)
            if summary.startswith("executed"):
                summary += "; " + await self._verify(await self._context(), lease)
            return summary
        if self.id == "verifier":
            return await self._verify(context, lease)
        # Cost specialist is read-only and uses the real model/message API.
        result = await self._model(context, "cost", [{"role": "system", "content": "Analyze cost observations and return concise JSON evidence and recommendations."}, {"role": "user", "content": json.dumps(context.get("observations", []), ensure_ascii=False)}])
        content = str(result.get("content", "cost analysis unavailable"))
        await self._message(context, lease, "result", content, [str(o.get("id")) for o in context.get("observations", []) if o.get("id")], .5)
        return "cost analysis sent"

    async def run(self) -> None:
        await self._start()
        try:
            while not self._stop.is_set():
                try:
                    context = await self._context()
                    # Detection is a deterministic observation-to-incident edge;
                    # it does not require a server-created detect task.
                    if self.id == "sentry":
                        await self._sentry(context)
                        await asyncio.sleep(self.poll_interval)
                        continue
                    if context.get("paused"):
                        await asyncio.sleep(self.poll_interval)
                        continue
                    task = self._task_for(context, KINDS[self.id], self._blocked)
                    if task and not self._ready(context, self.id):
                        await asyncio.sleep(self.poll_interval)
                        continue
                    if not task:
                        await asyncio.sleep(self.poll_interval)
                        continue
                    if self.id == "fixer-b":
                        await asyncio.sleep(.8)
                        context = await self._context()
                        if context.get("paused") or not self._ready(context, self.id):
                            continue
                    lease = await self._claim(task)
                    if not lease:
                        await asyncio.sleep(self.poll_interval)
                        continue
                    summary = await self._handle(context, lease)
                    await self._safe_complete(lease, summary)
                except (AgentError, httpx.HTTPError, asyncio.TimeoutError):
                    if self._lease:
                        task_id = self._lease.task_id
                        self._attempts[task_id] = self._attempts.get(task_id, 0) + 1
                        if self._attempts[task_id] >= 2:
                            self._blocked.add(task_id)
                        self._lease.done.set()
                        self._lease = None
                    await asyncio.sleep(self.poll_interval)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    # Unexpected parsing/transport errors are bounded and
                    # visible in task completion; they must not kill the OS
                    # worker or be mistaken for a successful action.
                    if self._lease:
                        lease = self._lease
                        self._attempts[lease.task_id] = self._attempts.get(lease.task_id, 0) + 1
                        await self._safe_complete(lease, f"failed: unexpected worker error {type(exc).__name__}")
                        if self._attempts[lease.task_id] >= 2:
                            self._blocked.add(lease.task_id)
                    await asyncio.sleep(self.poll_interval)
        finally:
            await self.close()


async def _main(args: argparse.Namespace) -> None:
    worker = Worker(args.id, args.base_url)
    await worker.run()


def main() -> None:
    parser = argparse.ArgumentParser(description="Section9 autonomous collaboration worker")
    parser.add_argument("--id", required=True, choices=sorted(ROLES))
    parser.add_argument("--base-url", required=True)
    args = parser.parse_args()
    try:
        asyncio.run(_main(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
