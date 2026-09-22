from __future__ import annotations

import gzip
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from s9.store import Store


def _otlp_payload(run_id: str) -> tuple[bytes, str]:
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest

    trace_id = "00112233445566778899aabbccddeeff"
    request = ExportTraceServiceRequest()
    span = request.resource_spans.add().scope_spans.add().spans.add()
    span.trace_id = bytes.fromhex(trace_id)
    span.span_id = bytes.fromhex("0011223344556677")
    span.name = "s9.test"
    attribute = span.attributes.add()
    attribute.key = "langfuse.observation.metadata.run_id"
    attribute.value.string_value = run_id
    return request.SerializeToString(), trace_id


async def _client_for(store: Store, monkeypatch: pytest.MonkeyPatch):
    # Importing api must not consult either dotenv file or start its lifespan.
    import dotenv

    monkeypatch.setattr(dotenv, "load_dotenv", lambda *args, **kwargs: None)
    from s9 import api

    stub = SimpleNamespace(
        store=store,
        evaluation_store=lambda: None,
        collector_ingest_error=None,
        last_collector_at=None,
    )
    api.app.dependency_overrides[api.core] = lambda: stub
    transport = httpx.ASGITransport(app=api.app)
    client = httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:9019")
    return api, stub, client


@pytest.mark.asyncio
async def test_plain_and_gzip_otlp_are_ingested_into_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    store = Store(tmp_path / "section9.sqlite")
    run_id = store.inject("prompt", "swarm", "telemetry-test", 1, 1000)["id"]
    payload, trace_id = _otlp_payload(run_id)
    api, stub, client = await _client_for(store, monkeypatch)
    try:
        plain = await client.post("/api/telemetry/v1/traces", content=payload,
                                  headers={"content-type": "application/x-protobuf"})
        compressed = await client.post("/api/telemetry/v1/traces", content=gzip.compress(payload),
                                       headers={"content-type": "application/x-protobuf", "content-encoding": "gzip"})
        assert plain.status_code == 200
        assert compressed.status_code == 200
        received = [event for event in store.events(run_id=run_id)
                    if event["event_type"] == "telemetry.span_received"]
        assert len(received) == 2
        assert {event["payload"]["trace_id"] for event in received} == {trace_id}
        assert {event["run_id"] for event in received} == {run_id}
        assert stub.collector_ingest_error is None
    finally:
        await client.aclose()
        api.app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_malformed_gzip_is_rejected_and_audit_event_is_written(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    store = Store(tmp_path / "section9.sqlite")
    api, stub, client = await _client_for(store, monkeypatch)
    try:
        response = await client.post(
            "/api/telemetry/v1/traces", content=b"not-a-gzip-stream",
            headers={"content-type": "application/x-protobuf", "content-encoding": "gzip"},
        )
        assert response.status_code == 400
        rejected = [event for event in store.events() if event["event_type"] == "telemetry.ingest_rejected"]
        assert rejected
        assert rejected[-1]["producer"] == "collector"
        assert stub.collector_ingest_error
    finally:
        await client.aclose()
        api.app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_decompressed_otlp_over_four_mb_returns_413(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    store = Store(tmp_path / "section9.sqlite")
    api, stub, client = await _client_for(store, monkeypatch)
    try:
        payload = gzip.compress(b"x" * 4_000_001)
        response = await client.post(
            "/api/telemetry/v1/traces", content=payload,
            headers={"content-type": "application/x-protobuf", "content-encoding": "gzip"},
        )
        assert response.status_code == 413
        assert not [event for event in store.events() if event["event_type"] == "telemetry.ingest_rejected"]
    finally:
        await client.aclose()
        api.app.dependency_overrides.clear()
