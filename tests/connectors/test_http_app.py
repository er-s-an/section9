import httpx
import pytest

from s9.connectors.http_app import ConnectorConfig, ExternalHTTPConnector, SourceBinding


@pytest.mark.asyncio
async def test_health_is_scope_bound_and_redacts_sensitive_response() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "ok", "api_key": "must-not-leak", "nested": {"token": "x"}})

    transport = httpx.MockTransport(handler)
    cfg = ConnectorConfig(
        base_url="http://candidate.test", project="section9", environment="test",
        source_binding=SourceBinding("section9", "test", "support-agent", "abc123"),
    )
    async with ExternalHTTPConnector(cfg, httpx.AsyncClient(transport=transport)) as connector:
        result = await connector.health()
    assert result["ok"] is True
    assert result["scope"] == {"project": "section9", "environment": "test"}
    assert result["source_binding"]["commit"] == "abc123"
    assert result["response"]["api_key"] == "[REDACTED]"
    assert result["response"]["nested"]["token"] == "[REDACTED]"


@pytest.mark.asyncio
async def test_chat_posts_generic_payload_without_candidate_specific_logic() -> None:
    seen: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"reply": "ok", "authorization": "Bearer secret",
                                         "usage": {"input_tokens": 12, "output_tokens": 8,
                                                   "total_tokens": 20, "access_token": "never-leak"}})

    cfg = ConnectorConfig("http://candidate.test", "p", "e", SourceBinding("p", "e", "s", "c"))
    async with ExternalHTTPConnector(cfg, httpx.AsyncClient(transport=httpx.MockTransport(handler))) as connector:
        result = await connector.chat("hello", "session-1")
    assert seen[0].url.path == "/chat"
    assert b'"message":"hello"' in seen[0].content
    assert result["response"]["authorization"] == "[REDACTED]"
    assert result["response"]["usage"] == {"input_tokens": 12, "output_tokens": 8,
                                              "total_tokens": 20, "access_token": "[REDACTED]"}


def test_scope_binding_mismatch_is_rejected() -> None:
    with pytest.raises(ValueError, match="scope"):
        ConnectorConfig("http://x", "p", "e", SourceBinding("other", "e", "s", "c"))
