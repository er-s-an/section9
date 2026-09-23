"""Read-only, incremental Langfuse observations connector.

Credentials are read only from the process environment and are never included
in returned observations.  The connector is intentionally independent from
the external application's native SDK: Section9 can attribute a sidecar trace
without claiming that the candidate itself has Langfuse support.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
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
            self._client = httpx.AsyncClient(timeout=self.config.timeout_seconds, trust_env=False,
                                             follow_redirects=False)
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
                           to_start_time: str | None = None, cursor: str | None = None,
                           limit: int = 100, include_context: bool = False) -> dict[str, Any]:
        if self._client is None:
            raise RuntimeError("use connector as an async context manager")
        if include_context and not trace_id:
            raise ValueError("Context requires one explicitly selected trace")
        if not (self.config.public_key and self.config.secret_key):
            return {"status_code": None, "status": "unknown", "rows": [], "watermark": None,
                    "availability": "credentials_missing", "detail": "Langfuse project credentials are not configured"}
        upper = to_start_time or datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        lower = from_start_time or (datetime.fromisoformat(upper.replace("Z", "+00:00")) - timedelta(hours=1))\
            .isoformat(timespec="milliseconds").replace("+00:00", "Z")
        try:
            lower_dt = datetime.fromisoformat(lower.replace("Z", "+00:00"))
            upper_dt = datetime.fromisoformat(upper.replace("Z", "+00:00"))
        except ValueError:
            return {"status_code": None, "status": "unknown", "rows": [], "watermark": None,
                    "availability": "invalid_window", "detail": "Langfuse time bounds must be RFC3339 timestamps"}
        if lower_dt.tzinfo is None or upper_dt.tzinfo is None or lower_dt >= upper_dt:
            return {"status_code": None, "status": "unknown", "rows": [], "watermark": None,
                    "availability": "invalid_window", "detail": "Langfuse time bounds must be an ordered timezone-aware interval"}
        params: dict[str, str | int] = {
            "limit": min(max(limit, 1), 1000),
            "fields": "core,basic,metadata,time,usage,trace_context",
            "fromStartTime": lower,
            "toStartTime": upper,
        }
        if trace_id:
            params["traceId"] = trace_id
        if include_context:
            params["fields"] += ",io,model"
        if cursor:
            params["cursor"] = cursor
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
        rows = [self._row(row, include_context=include_context) for row in raw_rows if isinstance(row, dict)]
        timestamps = [row["timestamp"] for row in rows if row.get("timestamp")]
        api_meta = payload.get("meta", {}) if isinstance(payload, dict) else {}
        next_cursor = api_meta.get("cursor") if isinstance(api_meta, dict) else None
        if response.status_code == 401 or response.status_code == 403:
            availability = "permission_denied"
        elif response.status_code == 429:
            availability = "rate_limited"
        elif response.status_code >= 500:
            availability = "unavailable"
        elif response.status_code != 200:
            availability = "request_rejected"
        else:
            availability = "data" if rows else "empty"
        return {"status_code": response.status_code, "status": "ready" if response.status_code == 200 else "degraded",
                "rows": rows, "watermark": max(timestamps) if timestamps else None,
                "availability": availability, "coverage": {"from_start_time": lower, "to_start_time": upper,
                    "complete": not bool(next_cursor), "next_cursor": next_cursor},
                "meta": api_meta}

    def _row(self, row: dict[str, Any], *, include_context: bool = False) -> dict[str, Any]:
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        safe_metadata = {key: metadata[key] for key in ("project_id", "environment_id", "incident_id", "source_commit", "external_trace", "telemetry_origin", "native_candidate_sdk") if key in metadata}
        result = {"id": row.get("id"), "project_id": row.get("projectId") or row.get("project_id"),
                "trace_id": row.get("traceId") or row.get("trace_id"),
                "name": row.get("name"), "timestamp": row.get("startTime") or row.get("createdAt"),
                "type": row.get("type"), "metadata": safe_metadata}
        if include_context:
            from integrations.support_agent.observability import redact

            result.update(parent_observation_id=row.get("parentObservationId"), end_time=row.get("endTime"),
                          level=row.get("level"), model=row.get("model"),
                          input=redact(row.get("input")), output=redact(row.get("output")),
                          usage=redact(row.get("usageDetails") or row.get("usage") or {}))
        return result


def current_watermark() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
