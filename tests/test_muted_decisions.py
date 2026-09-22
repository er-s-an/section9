from __future__ import annotations

import json

import httpx
import pytest

from s9.workers import Lease, Worker


def _context(*, muted: bool) -> dict:
    return {
        "generation": "gen-7",
        "transport_epoch": "transport-3",
        "instance_id": "instance-fixer",
        "config": {"revision": "rev-9", "generation": "gen-7"},
        "incident": {"run_id": "run-muted"},
        "observations": [{"id": "obs-raw", "summary": "terminal retry observed"}],
        "messages": [{"kind": "synthesize", "content": "peer suggestion"}],
        "memory": {"prompt_version": "known-good"},
        "muted": muted,
        "detection_pending": False,
    }


def _lease() -> Lease:
    return Lease("task-repair", "task-epoch-2", "run-muted")


@pytest.mark.asyncio
@pytest.mark.parametrize("muted", [True, False])
async def test_repair_decision_uses_raw_facts_with_or_without_peer_channel(
    monkeypatch: pytest.MonkeyPatch, muted: bool
) -> None:
    monkeypatch.setenv("S9_AGENT_TOKEN", "test-token")
    calls: dict[str, object] = {"model": [], "plan": None}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/agent/model":
            calls["model"].append(json.loads(request.content))  # type: ignore[union-attr]
            answer = {
                "decision": "repair",
                "rationale": "raw observation identifies the terminal retry policy",
                "evidence_ids": ["obs-raw"],
                "actions": [{"type": "apply_retry_policy", "values": {"retry_limit": 2}}],
            }
            return httpx.Response(200, json={"content": json.dumps(answer)})
        if path == "/agent/plan":
            calls["plan"] = json.loads(request.content)
            return httpx.Response(200, json={"id": "plan-1"})
        if path == "/agent/grant":
            return httpx.Response(200, json={"grant_id": "grant-1"})
        if path == "/agent/execute":
            return httpx.Response(200, json={"status": "applied", "action_id": "action-1"})
        if path == "/agent/context":
            return httpx.Response(200, json={**_context(muted=muted), "paused": False})
        return httpx.Response(200, json={"delivered": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://test") as client:
        worker = Worker("fixer-a", "http://test", client=client)
        await worker._start()
        result = await worker._fix(_context(muted=muted), _lease())
        await worker.close()

    assert result.startswith("executed")
    model_body = calls["model"][0]  # type: ignore[index]
    facts = json.loads(model_body["messages"][1]["content"])
    assert facts["observations"] == _context(muted=muted)["observations"]
    assert facts["messages"] == ([] if muted else _context(muted=False)["messages"])
    assert facts["memory"] == (None if muted else _context(muted=False)["memory"])
    assert calls["plan"]["run_id"] == "run-muted"  # type: ignore[index]


@pytest.mark.asyncio
async def test_muted_model_can_abstain_without_plan_or_second_model_call(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("S9_AGENT_TOKEN", "test-token")
    calls = {"model": 0, "plan": 0, "messages": []}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/agent/model":
            calls["model"] += 1
            return httpx.Response(200, json={"content": json.dumps({
                "decision": "abstain", "rationale": "raw evidence is insufficient to choose a safe repair",
                "evidence_ids": ["obs-raw"], "actions": [],
            })})
        if request.url.path == "/agent/plan":
            calls["plan"] += 1
        if request.url.path == "/agent/message":
            calls["messages"].append(json.loads(request.content))
        return httpx.Response(200, json={"delivered": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://test") as client:
        worker = Worker("fixer-a", "http://test", client=client)
        await worker._start()
        result = await worker._fix(_context(muted=True), _lease())
        await worker.close()

    assert result == "failed: model_abstained: raw evidence is insufficient to choose a safe repair"
    assert calls["model"] == 1
    assert calls["plan"] == 0
    assert calls["messages"][-1]["kind"] == "result"


@pytest.mark.asyncio
async def test_malformed_abstention_is_rejected_with_bounded_retry_and_no_plan(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("S9_AGENT_TOKEN", "test-token")
    calls = {"model": 0, "plan": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/agent/model":
            calls["model"] += 1
            malformed = {"decision": "abstain", "rationale": "cannot prove safety", "evidence_ids": ["obs-raw"], "actions": [{"type": "apply_retry_policy", "values": {}}]}
            return httpx.Response(200, json={"content": json.dumps(malformed)})
        if request.url.path == "/agent/plan":
            calls["plan"] += 1
        return httpx.Response(200, json={"delivered": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://test") as client:
        worker = Worker("fixer-a", "http://test", client=client)
        await worker._start()
        result = await worker._fix(_context(muted=True), _lease())
        await worker.close()

    assert result.startswith("failed: invalid repair output after two model calls")
    assert calls["model"] == 2
    assert calls["plan"] == 0


@pytest.mark.asyncio
async def test_verify_sends_full_authority_context(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("S9_AGENT_TOKEN", "test-token")
    verify_body: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/agent/verify":
            verify_body.update(json.loads(request.content))
            return httpx.Response(200, json={"passed": False, "summary": "failed fixture check"})
        return httpx.Response(200, json={})

    context = _context(muted=False) | {"last_action": {"id": "action-1"}}
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://test") as client:
        worker = Worker("verifier", "http://test", client=client)
        await worker._start()
        result = await worker._verify(context, _lease())
        await worker.close()

    assert result == "verification failed: failed fixture check"
    assert verify_body == {
        "run_id": "run-muted", "task_id": "task-repair", "task_epoch": "task-epoch-2",
        "instance_id": "instance-fixer", "generation": "gen-7",
        "transport_epoch": "transport-3", "expected_revision": "rev-9",
    }
