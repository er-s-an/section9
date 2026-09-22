"""Read-only, incremental Langfuse observations connector.

Credentials are read only from the process environment and are never included
in returned observations.  The connector is intentionally independent from
the external application's native SDK: Section9 can attribute a sidecar trace
without claiming that the candidate itself has Langfuse support.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import os
from typing import Any

import httpx


@dataclass(frozen=True)
class LangfuseConfig:
    base_url: str = "http://127.0.0.1:9030"
    public_key: str = ""
    secret_key: str = ""
    project_id: str = ""
    # A local Docker-backed Langfuse can take several seconds to answer the
    # first authenticated query while its query worker wakes up.  This is a
    # bounded read timeout, not a readiness claim; callers still classify an
    # unavailable/empty response as unknown.
    timeout_seconds: float = 20.0


class LangfuseConnector:
    """Fetch only the fields needed for a scoped evidence/call-tree view."""

    def __init__(self, config: LangfuseConfig | None = None, client: httpx.AsyncClient | None = None):
        self.config = config or LangfuseConfig(
            base_url=os.getenv("S9_LANGFUSE_URL", "http://127.0.0.1:9030"),
            public_key=os.getenv("LANGFUSE_INIT_PROJECT_PUBLIC_KEY", ""),
            secret_key=os.getenv("LANGFUSE_INIT_PROJECT_SECRET_KEY", ""),
            project_id=os.getenv("LANGFUSE_INIT_PROJECT_ID", ""),
        )
        self._client = client

    async def __aenter__(self) -> "LangfuseConnector":
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.config.timeout_seconds, trust_env=False)
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def health(self) -> dict[str, Any]:
        if self._client is None:
            raise RuntimeError("use connector as an async context manager")
        response = await self._client.get(self.config.base_url.rstrip("/") + "/api/public/health")
        return {"status_code": response.status_code, "ok": response.status_code == 200,
                "project_id_present": bool(self.config.project_id),
                "credentials_present": bool(self.config.public_key and self.config.secret_key)}

    async def observations(self, *, from_start_time: str | None = None, trace_id: str | None = None,
                           limit: int = 100) -> dict[str, Any]:
        if self._client is None:
            raise RuntimeError("use connector as an async context manager")
        if not (self.config.public_key and self.config.secret_key):
            return {"status_code": None, "status": "unknown", "rows": [], "watermark": None,
                    "detail": "Langfuse project credentials are not configured"}
        params: dict[str, str | int] = {"limit": min(max(limit, 1), 100), "fields": "core,basic,metadata,time"}
        if from_start_time:
            params["fromStartTime"] = from_start_time
        if trace_id:
            params["traceId"] = trace_id
        response = await self._client.get(
            self.config.base_url.rstrip("/") + "/api/public/v2/observations",
            params=params,
            auth=(self.config.public_key, self.config.secret_key),
        )
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        raw_rows = payload.get("data", []) if isinstance(payload, dict) else []
        rows = [self._row(row) for row in raw_rows if isinstance(row, dict)]
        timestamps = [row["timestamp"] for row in rows if row.get("timestamp")]
        return {"status_code": response.status_code, "status": "ready" if response.status_code == 200 else "degraded",
                "rows": rows, "watermark": max(timestamps) if timestamps else None,
                "meta": payload.get("meta", {}) if isinstance(payload, dict) else {}}

    def _row(self, row: dict[str, Any]) -> dict[str, Any]:
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        safe_metadata = {key: metadata[key] for key in ("project_id", "environment_id", "incident_id", "source_commit", "external_trace") if key in metadata}
        return {"id": row.get("id"), "trace_id": row.get("traceId") or row.get("trace_id"),
                "name": row.get("name"), "timestamp": row.get("startTime") or row.get("createdAt"),
                "type": row.get("type"), "metadata": safe_metadata}


def current_watermark() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
