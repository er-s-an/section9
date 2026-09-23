"""Authenticated EvoMap native sessions. Local task/approval authority stays in v1.

This adapter uses native session context/messages for worker handoffs. It does
not claim the Hub's hosted PDRI planner, public bounty market or asset publishing.
"""
from __future__ import annotations

import json
import asyncio
import hashlib
from pathlib import Path
from typing import Any

import httpx

from s9.product.registry import ProductError


class EvoMapSessions:
    def __init__(self, registry, client: httpx.AsyncClient, nodes: dict | None = None):
        self.registry = registry
        self.client = client
        path = Path.home() / ".config/section9/evomap-nodes.json"
        self.nodes = nodes if nodes is not None else json.loads(path.read_text()) if path.exists() else {}
        self.locks = {}

    def status(self):
        return {"configured": all(k in self.nodes for k in ("coordinator", "investigator", "reviewer")),
                "transport": "evomap_native_sessions",
                "members": [{"role": k, "node_id": v["node_id"]} for k, v in self.nodes.items()]}

    async def call(self, method: str, path: str, role: str, data: dict) -> dict:
        node = self.nodes.get(role)
        if not node:
            raise ProductError("EVOMAP_UNCONFIGURED", "原生协作成员尚未接入", 503)
        body = {**data, "sender_id" if method == "POST" else "node_id": node["node_id"]}
        response = await self.client.request(method, "https://evomap.ai/a2a/session/" + path,
            headers={"Authorization": "Bearer " + node["node_secret"]},
            **({"json": body} if method == "POST" else {"params": body}), timeout=25)
        if not response.is_success:
            raise ProductError("EVOMAP_REQUEST_FAILED", f"原生协作服务暂不可用（HTTP {response.status_code}）", 502)
        value = response.json()
        if not isinstance(value, dict) or value.get("error"):
            raise ProductError("EVOMAP_INVALID_RESPONSE", "原生协作未确认操作结果", 502)
        return value

    def binding(self, workspace, run_id):
        return self.registry.get("native_session", "native_" + run_id,
                                 project_id=workspace, environment_id="local-test")

    async def ensure(self, workspace: str, run_id: str) -> dict:
        async with self.locks.setdefault(run_id, asyncio.Lock()):
            return await self._ensure(workspace, run_id)

    async def _ensure(self, workspace: str, run_id: str) -> dict:
        previous = self.binding(workspace, run_id)
        if previous and previous.get('joined'):
            return previous
        created = previous or await self.call("POST", "create", "coordinator", {
            "title": "Section9 application investigation", "description": "Investigate scoped, sanitized application evidence and challenge findings.",
            "invite_node_ids": [self.nodes[r]["node_id"] for r in ("investigator", "reviewer")],
        })
        if not created.get("session_id"):
            raise ProductError("EVOMAP_SESSION_UNKNOWN", "原生会话创建结果不确定，请检查后继续", 502)
        record = previous or {"session_id": created["session_id"], "run_id": run_id,
                  "transport": "evomap_native_sessions", "events": []}
        self.registry.put("native_session", "native_" + run_id, record,
                          project_id=workspace, environment_id="local-test")
        for role in ("investigator", "reviewer"):
            await self.call("POST", "join", role, {"session_id": record["session_id"]})
        record['joined'] = True
        self.registry.put('native_session', 'native_' + run_id, record,
                          project_id=workspace, environment_id='local-test')
        return record

    async def exchange(self, record, sender, receiver, task_id, kind, value):
        """Small protocol messages, reassembled ONLY from native readback.

        The live service rejects large payloads (413). Content addressed chunks
        preserve exact context without turning a remote receipt into local data.
        """
        encoded = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(',', ':'))
        sha = hashlib.sha256(encoded.encode()).hexdigest()
        chunks = [encoded[i:i + 4000] for i in range(0, len(encoded), 4000)]
        if len(chunks) > 35:
            raise ProductError('EVOMAP_CONTEXT_TOO_LARGE', '证据过多，请缩小调查范围后继续', 422)
        for index, chunk in enumerate(chunks):
            await self.call('POST', 'message', sender, {'session_id': record['session_id'],
                'to_node_id': self.nodes[receiver]['node_id'], 'msg_type': kind,
                'payload': {'protocol': 'section9.context.v1', 'task_id': task_id,
                    'sha256': sha, 'part': index, 'parts': len(chunks), 'content': chunk}})
        remote = await self.call('GET', 'context', receiver, {'session_id': record['session_id']})
        received = {}
        last_id = None
        for message in remote.get('recent_messages', []):
            payload = message.get('payload') or {}
            if (message.get('fromNodeId') == self.nodes[sender]['node_id']
                    and payload.get('task_id') == task_id and payload.get('sha256') == sha
                    and payload.get('protocol') == 'section9.context.v1'):
                received[payload['part']] = payload['content']
                last_id = message.get('id')
        assembled = ''.join(received.get(i, '') for i in range(len(chunks)))
        if hashlib.sha256(assembled.encode()).hexdigest() != sha:
            raise ProductError('EVOMAP_HANDOFF_UNCONFIRMED', '原生交接尚未完整确认，已暂停本步骤', 502)
        return json.loads(assembled), last_id

    @staticmethod
    def role(task):
        return "investigator" if task.role == "investigator" else "reviewer" if task.role.startswith("challenger") else "coordinator"

    def member_for(self, workspace, run_id, task):
        record = self.binding(workspace, run_id) or {}
        paused = record.get('paused_members', [])
        preferred = self.role(task)
        candidates = [preferred, 'coordinator'] if preferred != 'coordinator' else ['coordinator', 'investigator']
        member = next((r for r in candidates if r not in paused and r in self.nodes), None)
        if not member:
            raise ProductError('EVOMAP_NO_MEMBER', '当前没有可接手的成员，请恢复成员后继续', 409)
        return member

    def worker_id(self, workspace, run_id, task):
        return self.nodes[self.member_for(workspace, run_id, task)]['node_id']

    async def pause(self, workspace, run_id, role):
        if role not in {'investigator', 'reviewer'}:
            raise ProductError('INVALID_MEMBER', '仅可演练调查或复核成员离线', 422)
        record = await self.ensure(workspace, run_id)
        await self.call('POST', 'message', 'coordinator', {'session_id': record['session_id'],
            'msg_type': 'status_update', 'payload': {'member': self.nodes[role]['node_id'],
                'state': 'paused_by_operator', 'reason': 'Explicit failure recovery demonstration'}})
        fresh = self.binding(workspace, run_id)
        fresh['paused_members'] = list(set(fresh.get('paused_members', []) + [role]))
        self.registry.put('native_session', 'native_' + run_id, fresh, project_id=workspace, environment_id='local-test')
        self._event(workspace, run_id, fresh, role, 'paused', '', '演练：此成员已暂停，未完成的工作需要接力', None)

    async def prepare(self, workspace: str, run_id: str, task, context: dict) -> dict:
        record = await self.ensure(workspace, run_id)
        role = self.member_for(workspace, run_id, task)
        # Inputs stay scoped by the v1 lease; only the selected, redacted evidence
        # reaches this session, never credentials or unrelated application data.
        delivered, remote_id = await self.exchange(record, 'coordinator', role, task.id, 'handoff', context)
        fresh = self.binding(workspace, run_id)
        fresh.setdefault('assignments', {})[task.id] = role
        self.registry.put('native_session', 'native_' + run_id, fresh, project_id=workspace, environment_id='local-test')
        if role != self.role(task):
            self._event(workspace, run_id, fresh, role, 'takeover', task.id, '已从原生会话接手暂停成员的工作，保留已完成证据', remote_id)
        self._event(workspace, run_id, record, role, "handoff", task.id, "已从 EvoMap 接收任务与证据", remote_id)
        return delivered

    async def finish(self, workspace: str, run_id: str, task, result: dict):
        record = self.binding(workspace, run_id)
        role = record.get('assignments', {}).get(task.id, self.role(task))
        if role in record.get('paused_members', []):
            raise ProductError('EVOMAP_MEMBER_PAUSED', '成员已暂停，本次用量已保存，请交给其他成员接力', 409)
        _, remote_id = await self.exchange(record, role, 'coordinator', task.id, 'subtask_result', result)
        self._event(workspace, run_id, record, role, "result", task.id, result["summary"], remote_id)

    async def evidence_read(self, workspace: str, run_id: str, task, request: dict, result: dict) -> dict:
        """Deliver a bounded evidence read and verify native-session readback."""
        record = self.binding(workspace, run_id)
        role = record.get('assignments', {}).get(task.id, self.role(task))
        if role in record.get('paused_members', []) or self.member_for(workspace, run_id, task) != role:
            raise ProductError('EVOMAP_MEMBER_PAUSED', '调查成员已暂停或已接力，不能继续当前证据读取', 409)
        value = {"kind": "evidence_read", "task_id": task.id, "request": request,
                 "result": result, "content_is_untrusted_data": True}
        delivered, remote_id = await self.exchange(record, 'coordinator', role, task.id, 'handoff', value)
        if delivered != value:
            raise ProductError('EVOMAP_HANDOFF_UNCONFIRMED', '原生证据读取回读与请求不一致，已暂停此步骤', 502)
        digest = hashlib.sha256(json.dumps(result, ensure_ascii=True, sort_keys=True,
            separators=(',', ':')).encode()).hexdigest()
        self._event(workspace, run_id, record, role, 'evidence_read', task.id,
            '原生会话已回读并确认一条受限证据片段', remote_id)
        return {"result": delivered["result"], "remote_message_id": remote_id, "result_sha256": digest}

    def _event(self, workspace, run_id, record, role, kind, task_id, summary, remote_id):
        from datetime import datetime, timezone
        fresh = self.binding(workspace, run_id) or record
        fresh["events"].append({"role": role, "node_id": self.nodes[role]["node_id"], "kind": kind,
            "task_id": task_id, "summary": summary, "remote_message_id": remote_id,
            "at": datetime.now(timezone.utc).isoformat()})
        self.registry.put("native_session", "native_" + run_id, fresh,
                          project_id=workspace, environment_id="local-test")
