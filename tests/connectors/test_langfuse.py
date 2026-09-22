import httpx
import pytest

from s9.connectors.langfuse import LangfuseConfig, LangfuseConnector


@pytest.mark.asyncio
async def test_observations_are_incremental_and_redacted() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["fromStartTime"] == "2026-09-23T00:00:00Z"
        assert request.url.params["traceId"] == "trace-1"
        return httpx.Response(200, json={"data": [{"id": "obs-1", "traceId": "trace-1", "name": "external", "startTime": "2026-09-23T00:01:00Z", "metadata": {"incident_id": "i1", "api_key": "must-not-leak"}}], "meta": {"page": 1}})

    cfg = LangfuseConfig(public_key="pk", secret_key="sk", project_id="p")
    async with LangfuseConnector(cfg, httpx.AsyncClient(transport=httpx.MockTransport(handler))) as connector:
        result = await connector.observations(from_start_time="2026-09-23T00:00:00Z", trace_id="trace-1")
    assert result["status"] == "ready"
    assert result["watermark"] == "2026-09-23T00:01:00Z"
    assert result["rows"][0]["metadata"] == {"incident_id": "i1"}


@pytest.mark.asyncio
async def test_missing_credentials_is_unknown_not_ready() -> None:
    cfg = LangfuseConfig(public_key="", secret_key="", project_id="p")
    async with LangfuseConnector(cfg, httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(500)))) as connector:
        result = await connector.observations()
    assert result["status"] == "unknown"
    assert result["rows"] == []
