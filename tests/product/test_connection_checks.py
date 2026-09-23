from contextlib import asynccontextmanager

import pytest

from s9.product.connection_checks import _base_url, check_langfuse_binding
from s9.product.credentials import LangfuseCredentials


@pytest.mark.parametrize("url", [
    "http://169.254.169.254/latest/meta-data",
    "https://10.0.0.8/api",
    "https://192.168.1.1/api",
    "http://langfuse.example.com/api",
    "https://metadata.google.internal/",
])
def test_connection_endpoint_rejects_private_metadata_and_external_http(url):
    with pytest.raises(ValueError):
        _base_url({"endpoint": url})


def test_local_langfuse_is_explicitly_allowed_but_other_private_hosts_are_not():
    assert _base_url({"endpoint": "http://127.0.0.1:9030"}) == "http://127.0.0.1:9030"
    assert _base_url({"endpoint": "http://localhost:9030"}) == "http://localhost:9030"


@pytest.mark.asyncio
async def test_empty_langfuse_window_confirms_auth_and_matching_project_without_exposing_rows():
    class Connector:
        def __init__(self, config):
            assert config.project_id == "project-a"

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def observations(self, *, limit):
            assert limit == 1
            return {"status_code": 200, "availability": "empty", "rows": [], "watermark": None,
                    "coverage": {"from_start_time": "2026-09-22T23:00:00Z",
                                 "to_start_time": "2026-09-23T00:00:00Z", "complete": True}}

    result = await check_langfuse_binding(
        {"endpoint": "http://127.0.0.1:9030"},
        {"external_resource_id": "project-a"},
        LangfuseCredentials("pk", "sk", "project-a"),
        connector_factory=Connector,
    )
    assert result["outcome"] == "empty"
    assert result["connection_status"] == "connected"
    assert result["binding_status"] == "confirmed"
    assert result["observed_count"] == 0
    assert "rows" not in result
    assert "sk" not in str(result)


@pytest.mark.asyncio
async def test_empty_data_without_project_identity_does_not_confirm_scope():
    class Connector:
        def __init__(self, _): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *_): return None
        async def observations(self, *, limit):
            return {"status_code": 200, "rows": [], "watermark": None, "coverage": {"complete": True}}

    result = await check_langfuse_binding(
        {"endpoint": "http://127.0.0.1:9030"},
        {"external_resource_id": "project-a"},
        LangfuseCredentials("pk", "sk", ""),
        connector_factory=Connector,
    )
    assert result["connection_status"] == "connected"
    assert result["binding_status"] == "pending"
    assert result["scope_confirmed"] is False


@pytest.mark.asyncio
async def test_forbidden_langfuse_read_is_reported_as_permission_denied():
    class Connector:
        def __init__(self, _): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *_): return None
        async def observations(self, *, limit):
            return {"status_code": 403, "rows": [], "watermark": None,
                    "availability": "permission_denied", "coverage": {"complete": True}}

    result = await check_langfuse_binding(
        {"endpoint": "http://127.0.0.1:9030"},
        {"external_resource_id": "project-a"},
        LangfuseCredentials("pk", "sk", "project-a"),
        connector_factory=Connector,
    )
    assert result["outcome"] == "permission_denied"
    assert result["connection_status"] == "permission_denied"
    assert result["binding_status"] == "permission_denied"
