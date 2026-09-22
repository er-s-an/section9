from __future__ import annotations

import httpx
import pytest

from s9.api import app
from s9.pairs.coordinator import PairCoordinator
from s9.pairs.runtime import ArmRuntime
from s9.provenance import capture_identity


class FakeTelemetry:
    pass


@pytest.fixture
def pair_client(tmp_path, monkeypatch):
    # Creation and read APIs exercise the real SQLite catalog/journal while
    # avoiding provider calls and worker processes.
    monkeypatch.setattr(ArmRuntime, "prepare_core", lambda self: self.core)
    pairs = PairCoordinator(tmp_path / "showcase", object(), FakeTelemetry(), capture_identity())
    old = getattr(app.state, "pairs", None)
    app.state.pairs = pairs

    async def run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client

    yield pairs, run
    if old is None:
        delattr(app.state, "pairs")
    else:
        app.state.pairs = old


@pytest.mark.asyncio
async def test_pair_api_freezes_strict_request_and_rejects_model_budget_overrides(pair_client):
    _, client_factory = pair_client
    async for client in client_factory():
        response = await client.post("/api/pairs", json={"scenario": "prompt", "seed": 7,
                                                         "model": "override"}, headers={"Idempotency-Key": "x"})
        assert response.status_code == 422
        response = await client.post("/api/pairs", json={"scenario": "prompt", "seed": 7,
                                                         "budget_policy": {"total_tokens_per_arm": 1}},
                                     headers={"Idempotency-Key": "y"})
        assert response.status_code == 422
        response = await client.post("/api/pairs", json={"scenario": "prompt", "seed": 7},
                                     headers={"Idempotency-Key": "z"})
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ready"
        assert body["immutable_spec"]["model"]
        break


@pytest.mark.asyncio
async def test_pair_api_snapshot_logbook_cursor_and_operator_boundary(pair_client):
    pairs, client_factory = pair_client
    async for client in client_factory():
        created = await client.post("/api/pairs", json={"scenario": "prompt"},
                                    headers={"Idempotency-Key": "read"})
        pair = created.json()
        pair_id = pair["pair_id"]
        snapshot = await client.get(f"/api/pairs/{pair_id}/arms/swarm")
        assert snapshot.status_code == 200
        assert snapshot.json()["pair_id"] == pair_id
        logbook = await client.get(f"/api/pairs/{pair_id}/logbook", params={"arm": "swarm"})
        assert logbook.status_code == 200
        rows = logbook.json()["items"]
        assert rows and all(row["arm"] == "swarm" for row in rows)
        cursor = logbook.json()["as_of_sequence"]
        replay = await client.get(f"/api/pairs/{pair_id}/logbook", params={"after": cursor})
        assert replay.json()["items"] == []
        denied = await client.get("/api/pairs", headers={"Authorization": "Bearer worker-token"})
        assert denied.status_code == 403
        missing = await client.get("/api/pairs/unknown-pair")
        assert missing.status_code == 404
        break
