"""Small, dependency-light HTTP application connector.

The connector knows transport and evidence shape only.  It deliberately has no
knowledge of any candidate application's implementation or business rules.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping

import httpx

_SENSITIVE = re.compile(r"(?i)(api[_-]?key|access[_-]?token|token|auth|authorization|cookie|password|secret|credential)")
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_USAGE_METRICS = {
    "input_tokens", "output_tokens", "total_tokens", "prompt_tokens", "completion_tokens",
    "reasoning_tokens", "usage_tokens",
}


@dataclass(frozen=True)
class ProbeDeclaration:
    name: str
    method: str = "GET"
    path: str = "/health"
    expected_status: tuple[int, ...] = (200,)
    purpose: str = "liveness"


@dataclass(frozen=True)
class BusinessProbe(ProbeDeclaration):
    purpose: str = "business"
    request_json: Mapping[str, Any] | None = None
    response_fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class SourceBinding:
    project: str
    environment: str
    source: str
    commit: str
    bound_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def as_dict(self) -> dict[str, str]:
        return {"project": self.project, "environment": self.environment, "source": self.source,
                "commit": self.commit, "bound_at": self.bound_at}


@dataclass(frozen=True)
class ConnectorConfig:
    base_url: str
    project: str
    environment: str
    source_binding: SourceBinding
    timeout_seconds: float = 10.0
    headers: Mapping[str, str] = field(default_factory=dict)
    probes: tuple[ProbeDeclaration, ...] = ()
    redaction_keys: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.source_binding.project != self.project or self.source_binding.environment != self.environment:
            raise ValueError("source binding scope must match connector scope")


class ExternalHTTPConnector:
    """Perform declared probes and return redacted, scope-bound observations."""

    def __init__(self, config: ConnectorConfig, client: httpx.AsyncClient | None = None) -> None:
        self.config = config
        self._client = client

    async def __aenter__(self) -> "ExternalHTTPConnector":
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.config.timeout_seconds)
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._client is not None and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def request(self, method: str, path: str, json: Mapping[str, Any] | None = None) -> dict[str, Any]:
        if self._client is None:
            raise RuntimeError("use connector as an async context manager")
        response = await self._client.request(method, self._url(path), headers=dict(self.config.headers), json=json)
        try:
            body: Any = response.json()
        except ValueError:
            body = response.text[:4000]
        return self._observation(method, path, response.status_code, body)

    async def health(self) -> dict[str, Any]:
        return await self.request("GET", "/health")

    async def readiness(self) -> dict[str, Any]:
        return await self.request("GET", "/ready")

    async def chat(self, message: str, session_id: str | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"message": message}
        if session_id is not None:
            payload["session_id"] = session_id
        return await self.request("POST", "/chat", payload)

    async def run_probes(self) -> list[dict[str, Any]]:
        return [await self.request(p.method, p.path, p.request_json if isinstance(p, BusinessProbe) else None)
                for p in self.config.probes]

    def _url(self, path: str) -> str:
        return self.config.base_url.rstrip("/") + "/" + path.lstrip("/")

    def _observation(self, method: str, path: str, status: int, body: Any) -> dict[str, Any]:
        return {"scope": {"project": self.config.project, "environment": self.config.environment},
                "source_binding": self.config.source_binding.as_dict(), "request": {"method": method, "path": path},
                "status_code": status, "ok": status < 400, "response": self._redact(body)}

    def _redact(self, value: Any) -> Any:
        extra = "|".join(map(re.escape, self.config.redaction_keys))
        keys = _SENSITIVE.pattern + (("|" + extra) if extra else "")
        if isinstance(value, Mapping):
            return {str(k): "[REDACTED]" if str(k).lower().replace("-", "_") not in _USAGE_METRICS
                    and re.search(keys, str(k)) else self._redact(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self._redact(v) for v in value]
        if isinstance(value, str):
            return _BEARER.sub("Bearer [REDACTED]", value)
        return value
