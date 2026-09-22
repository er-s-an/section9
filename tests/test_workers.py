from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from s9.workers import Worker, _json_content


def test_json_content_accepts_fenced_and_embedded_json() -> None:
    assert _json_content("```json\n{\"root_cause\": \"x\"}\n```")["root_cause"] == "x"
    assert _json_content("answer: {\"confidence\": 0.8}")["confidence"] == 0.8
    assert _json_content("not json") == {}


@pytest.mark.asyncio
async def test_sentry_deduplicates_observation_batches(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("S9_AGENT_TOKEN", "component-token")
    calls: list[tuple[str, str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        calls.append((request.method, request.url.path, body))
        if request.url.path == "/agent/context":
            return httpx.Response(200, json={"observations": [{"id": "o1", "type": "prompt", "summary": "bad"}]})
        return httpx.Response(200, json={"ok": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://test") as client:
        worker = Worker("sentry", "http://test", client=client, poll_interval=.01)
        await worker._start()
        context = await worker._context()
        await worker._sentry(context)
        await worker._sentry(context)
        await worker.close()
    assert [path for _, path, _ in calls].count("/agent/incidents") == 1


@pytest.mark.asyncio
async def test_paused_worker_keeps_grant_and_sends_original_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("S9_AGENT_TOKEN", "component-token")
    state = {"paused": True}
    execute_bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/agent/context":
            return httpx.Response(200, json={"paused": state["paused"], "config": {"revision": "4"}, "observations": [], "messages": [], "incident": {"run_id": "r1"}})
        if path == "/agent/model":
            return httpx.Response(200, json={"content": json.dumps({"rationale": "repair", "evidence_ids": ["o1"], "actions": [{"type": "apply_retry_policy", "values": {"retry_limit": 2}}]})})
        if path == "/agent/plan":
            return httpx.Response(200, json={"id": "p1", "hash": "h1", "status": "draft"})
        if path == "/agent/grant":
            return httpx.Response(200, json={"grant_id": "g-original", "pause_before_execute": True})
        if path == "/agent/execute":
            execute_bodies.append(json.loads(request.content))
            return httpx.Response(409, json={"error": {"code": "STALE_GRANT"}, "status": "stale"})
        return httpx.Response(200, json={"ok": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://test") as client:
        worker = Worker("single", "http://test", client=client, poll_interval=.001)
        await worker._start()
        lease = type("Lease", (), {"task_id": "t1", "epoch": "3", "run_id": "r1"})()
        task = asyncio.create_task(worker._fix({"config": {"revision": "4"}, "observations": [], "messages": [], "incident": {"run_id": "r1"}}, lease, True))
        await asyncio.sleep(.005)
        state["paused"] = False
        result = await task
        await worker.close()
    assert "stale grant" in result
    assert execute_bodies and execute_bodies[0]["grant_id"] == "g-original"


@pytest.mark.asyncio
async def test_verifier_waits_for_last_action(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("S9_AGENT_TOKEN", "component-token")
    requested = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requested
        requested = requested or request.url.path == "/agent/verify"
        return httpx.Response(200, json={"passed": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://test") as client:
        worker = Worker("verifier", "http://test", client=client)
        await worker._start()
        lease = type("Lease", (), {"run_id": "r1"})()
        result = await worker._verify({}, lease)
        await worker.close()
    assert result.startswith("waiting:")
    assert not requested


def test_readiness_waits_without_claiming_peer_or_detection(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("S9_AGENT_TOKEN", "component-token")
    assert Worker._ready({"detection_pending": True, "messages": []}, "diagnoser") is False
    assert Worker._ready({"detection_pending": True, "messages": [{"kind": "synthesize"}]}, "single") is False
    assert Worker._ready({"detection_pending": False, "messages": []}, "fixer-a") is False
    assert Worker._ready({"detection_pending": False, "messages": [{"kind": "synthesize"}]}, "fixer-a") is True
    assert Worker._ready({"last_action": None}, "verifier") is False


@pytest.mark.asyncio
async def test_policy_approval_retries_grant_without_second_model_call(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("S9_AGENT_TOKEN", "component-token")
    calls = {"model": 0, "grant": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/agent/model":
            calls["model"] += 1
            return httpx.Response(200, json={"content": json.dumps({"rationale": "r", "actions": [{"type": "apply_retry_policy", "values": {"retry_limit": 2}}]})})
        if path == "/agent/plan":
            return httpx.Response(200, json={"id": "p1"})
        if path == "/agent/grant":
            calls["grant"] += 1
            if calls["grant"] == 1:
                return httpx.Response(403, json={"error": {"code": "APPROVAL_REQUIRED"}})
            return httpx.Response(200, json={"grant_id": "g1"})
        if path == "/agent/execute":
            return httpx.Response(200, json={"action_id": "a1", "status": "applied"})
        if path == "/agent/message":
            return httpx.Response(200, json={"delivered": True})
        return httpx.Response(200, json={"paused": False})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://test") as client:
        worker = Worker("single", "http://test", client=client, poll_interval=.001)
        await worker._start()
        lease = type("Lease", (), {"task_id": "t1", "epoch": "1", "run_id": "r1", "plan_id": None})()
        result = await worker._fix({"config": {"revision": "1"}, "observations": [], "messages": [], "incident": {"run_id": "r1"}}, lease, True)
        await worker.close()
    assert result.startswith("executed")
    assert calls == {"model": 1, "grant": 2}


@pytest.mark.asyncio
async def test_completion_rejection_does_not_kill_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("S9_AGENT_TOKEN", "component-token")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/agent/task/complete":
            return httpx.Response(409, json={"error": {"code": "RUN_CLOSED"}})
        return httpx.Response(200, json={})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://test") as client:
        worker = Worker("single", "http://test", client=client)
        await worker._start()
        lease = type("Lease", (), {"task_id": "t1", "epoch": "1", "run_id": "r1", "done": asyncio.Event()})()
        await worker._safe_complete(lease, "resolved")
        await worker.close()
    assert lease.done.is_set()
