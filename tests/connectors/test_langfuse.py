import httpx
import pytest

from s9.connectors.langfuse import LangfuseConfig, LangfuseConnector


@pytest.mark.asyncio
async def test_observations_are_incremental_and_redacted() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["fromStartTime"] == "2026-09-23T00:00:00Z"
        assert request.url.params["toStartTime"]
        assert "trace_context" in request.url.params["fields"]
        assert request.url.params["traceId"] == "trace-1"
        return httpx.Response(200, json={"data": [{"id": "obs-1", "projectId": "p", "traceId": "trace-1", "name": "external", "startTime": "2026-09-23T00:01:00Z", "metadata": {"incident_id": "i1", "api_key": "must-not-leak"}}], "meta": {"page": 1}})

    cfg = LangfuseConfig(public_key="pk", secret_key="sk", project_id="p")
    async with LangfuseConnector(cfg, httpx.AsyncClient(transport=httpx.MockTransport(handler))) as connector:
        result = await connector.observations(from_start_time="2026-09-23T00:00:00Z", trace_id="trace-1")
    assert result["status"] == "ready"
    assert result["watermark"] == "2026-09-23T00:01:00Z"
    assert result["rows"][0]["metadata"] == {"incident_id": "i1"}
    assert result["rows"][0]["project_id"] == "p"
    assert result["coverage"]["complete"] is True
    assert result["availability"] == "data"


@pytest.mark.asyncio
async def test_missing_credentials_is_unknown_not_ready() -> None:
    cfg = LangfuseConfig(public_key="", secret_key="", project_id="p")
    async with LangfuseConnector(cfg, httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(500)))) as connector:
        result = await connector.observations()
    assert result["status"] == "unknown"
    assert result["rows"] == []
    assert result["availability"] == "credentials_missing"


@pytest.mark.asyncio
async def test_observations_uses_cursor_and_distinguishes_empty_and_denied():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["cursor"] == "opaque-next-page"
        assert request.url.params["limit"] == "1000"
        return httpx.Response(200, json={"data": [], "meta": {"cursor": "opaque-next-page-2"}})

    cfg = LangfuseConfig(public_key="pk", secret_key="sk", project_id="p")
    async with LangfuseConnector(cfg, httpx.AsyncClient(transport=httpx.MockTransport(handler))) as connector:
        empty = await connector.observations(cursor="opaque-next-page", limit=5000)
    assert empty["availability"] == "empty"
    assert empty["coverage"]["complete"] is False
    assert empty["coverage"]["next_cursor"] == "opaque-next-page-2"

    async with LangfuseConnector(cfg, httpx.AsyncClient(transport=httpx.MockTransport(
            lambda _: httpx.Response(403)))) as connector:
        denied = await connector.observations()
    assert denied["status"] == "degraded"
    assert denied["availability"] == "permission_denied"


@pytest.mark.asyncio
async def test_invalid_or_unordered_time_window_fails_without_http_request():
    async def handler(_: httpx.Request) -> httpx.Response:
        raise AssertionError("invalid time window must not contact the provider")

    cfg = LangfuseConfig(public_key="pk", secret_key="sk", project_id="p")
    async with LangfuseConnector(cfg, httpx.AsyncClient(transport=httpx.MockTransport(handler))) as connector:
        result = await connector.observations(from_start_time="2026-09-24T00:00:00Z",
                                              to_start_time="2026-09-23T00:00:00Z")
    assert result["availability"] == "invalid_window"
    assert result["status_code"] is None
