from __future__ import annotations

import json

import httpx
import pytest

from s9.workers import Lease, Worker, _validate_diagnosis, _validate_repair


def context() -> dict:
    return {
        "generation": "g1", "transport_epoch": "te1",
        "instance_id": "instance-top", "agent": {"instance_id": "stale-agent-value"},
        "config": {"revision": "7", "generation": "g1"},
        "incident": {"run_id": "run-a"}, "observations": [{"id": "obs-a"}],
        "messages": [{"kind": "synthesize", "content": "peer conclusion"}], "muted": False,
    }


def lease() -> Lease:
    return Lease("task-a", "epoch-4", "run-a")


@pytest.mark.parametrize("value", [
    {"root_cause": "x", "confidence": .5, "evidence_ids": None, "proposed_checks": []},
    {"root_cause": "x", "confidence": "0.5", "evidence_ids": [], "proposed_checks": []},
    {"root_cause": "x", "confidence": .5, "evidence_ids": [3], "proposed_checks": []},
    {"root_cause": "x", "confidence": .5, "evidence_ids": [], "proposed_checks": None},
])
def test_diagnosis_schema_rejects_malformed_fields(value):
    answer, error = _validate_diagnosis(value)
    assert answer is None and error


@pytest.mark.parametrize("actions", [None, [], [{"type": "apply_retry_policy", "values": {}} , None],
                                     [{"type": "apply_retry_policy", "values": None}]])
def test_repair_schema_rejects_malformed_actions(actions):
    answer, error = _validate_repair({"rationale": "r", "evidence_ids": [], "actions": actions})
    assert answer is None and error


@pytest.mark.asyncio
async def test_invalid_repair_is_visible_and_bounded_without_plan(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("S9_AGENT_TOKEN", "test-token")
    calls = {"model": 0, "plan": 0, "messages": []}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/agent/model":
            calls["model"] += 1
            # Invalid array element must not be filtered into a valid plan.
            body = {"rationale": "r", "evidence_ids": [], "actions": [{"type": "apply_retry_policy", "values": {}}, None]}
            return httpx.Response(200, json={"content": json.dumps(body), "usage": {"total_tokens": 11}})
        if path == "/agent/plan":
            calls["plan"] += 1
        if path == "/agent/message":
            calls["messages"].append(json.loads(request.content))
        return httpx.Response(200, json={"delivered": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://test") as client:
        worker = Worker("fixer-a", "http://test", client=client)
        await worker._start()
        result = await worker._fix(context(), lease())
        await worker.close()
    assert result.startswith("failed: invalid repair output")
    assert calls["model"] == 2
    assert calls["plan"] == 0
    assert calls["messages"][-1]["kind"] == "result"
    assert calls["messages"][-1]["task_id"] == "task-a"


@pytest.mark.asyncio
async def test_diagnosis_repair_request_is_bounded_and_message_fields_are_frozen(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("S9_AGENT_TOKEN", "test-token")
    calls = {"model": 0, "messages": []}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/agent/model":
            calls["model"] += 1
            body = {"root_cause": "quality mismatch", "confidence": .8, "evidence_ids": ["obs-a"], "proposed_checks": [{"name": "probe"}]}
            return httpx.Response(200, json={"content": json.dumps(body), "usage": {"total_tokens": 17}})
        if request.url.path == "/agent/message":
            calls["messages"].append(json.loads(request.content))
        return httpx.Response(200, json={"delivered": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://test") as client:
        worker = Worker("diagnoser", "http://test", client=client)
        await worker._start()
        await worker._diagnose(context(), lease())
        await worker.close()
    assert calls["model"] == 1
    assert len(calls["messages"]) == 2
    for message in calls["messages"]:
        assert message["task_id"] == "task-a"
        assert message["task_epoch"] == "epoch-4"
        assert message["instance_id"] == "instance-top"
        assert message["generation"] == "g1"
        assert message["transport_epoch"] == "te1"


@pytest.mark.asyncio
async def test_verifier_terminal_result_does_not_send_stale_message(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("S9_AGENT_TOKEN", "test-token")
    paths = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path == "/agent/verify":
            return httpx.Response(200, json={"passed": True})
        return httpx.Response(200, json={})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://test") as client:
        worker = Worker("verifier", "http://test", client=client)
        await worker._start()
        result = await worker._verify({"last_action": {"id": "a1"}}, lease())
        await worker.close()
    assert result == "verified"
    assert "/agent/verify" in paths
    assert "/agent/message" not in paths


@pytest.mark.asyncio
async def test_unexpected_handler_error_is_completed_and_worker_survives(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("S9_AGENT_TOKEN", "test-token")
    holder: dict[str, Worker] = {}
    completed: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/agent/context":
            return httpx.Response(200, json={"paused": False, "tasks": [{"id": "t1", "kind": "single", "status": "available"}]})
        if request.url.path == "/agent/claim":
            return httpx.Response(200, json={"task_id": "t1", "epoch": "1", "run_id": "r1"})
        if request.url.path == "/agent/task/complete":
            completed.append(json.loads(request.content))
            holder["worker"]._stop.set()
        return httpx.Response(200, json={"ok": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://test") as client:
        worker = Worker("single", "http://test", client=client, poll_interval=.001)
        holder["worker"] = worker

        async def explode(context, lease):
            raise ValueError("unexpected parser boundary")

        worker._handle = explode
        await worker.run()
    assert completed and completed[0]["result_summary"].startswith("failed: unexpected worker error")
