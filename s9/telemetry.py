"""Local OpenTelemetry producer for the Section9 contract.

The collector is the fan-out boundary.  This module deliberately reports a
degraded state when the SDK/exporter cannot be initialized; it never turns a
failed export into an apparently successful control event.
"""
from __future__ import annotations

import json
import os
from typing import Any


class Telemetry:
    def __init__(self) -> None:
        self._provider = None
        self._tracer = None
        self._error: str | None = None
        self._endpoint = os.getenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT") or os.getenv(
            "OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:9431/v1/traces"
        )
        try:
            from opentelemetry import trace
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
            from opentelemetry.sdk.resources import Resource
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import BatchSpanProcessor

            headers = self._headers()
            exporter = OTLPSpanExporter(endpoint=self._endpoint, headers=headers)
            provider = TracerProvider(resource=Resource.create({"service.name": os.getenv("OTEL_SERVICE_NAME", "section9")}))
            provider.add_span_processor(BatchSpanProcessor(exporter))
            trace.set_tracer_provider(provider)
            self._provider = provider
            self._tracer = trace.get_tracer("section9.telemetry", "0.1.0")
        except Exception as exc:  # noqa: BLE001 - dependency errors must become explicit degraded state
            self._error = f"{type(exc).__name__}: {exc}"

    @staticmethod
    def _headers() -> dict[str, str]:
        raw = os.getenv("OTEL_EXPORTER_OTLP_TRACES_HEADERS") or os.getenv("OTEL_EXPORTER_OTLP_HEADERS", "")
        result: dict[str, str] = {}
        for item in raw.split(","):
            if "=" in item:
                key, value = item.split("=", 1)
                result[key.strip()] = value.strip()
        return result

    @staticmethod
    def _value(value: Any) -> str | int | float | bool:
        if isinstance(value, (str, int, float, bool)):
            return value
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    def record(
        self, name: str, *, start_ns: int, end_ns: int, input_data: dict | str,
        output_data: dict | str, metadata: dict, usage: dict | None = None,
    ) -> str | None:
        if self._tracer is None:
            return None
        try:
            from opentelemetry.trace import SpanKind

            span = self._tracer.start_span(name, kind=SpanKind.INTERNAL, start_time=start_ns)
            ended = False
            try:
                span.set_attribute("langfuse.observation.type", "span")
                span.set_attribute("langfuse.observation.input", self._value(input_data))
                span.set_attribute("langfuse.observation.output", self._value(output_data))
                for key, value in metadata.items():
                    span.set_attribute(f"langfuse.observation.metadata.{key}", self._value(value))
                if usage:
                    for key, value in usage.items():
                        if isinstance(value, (int, float)):
                            span.set_attribute(f"gen_ai.usage.{key}", value)
                context = span.get_span_context()
                trace_id = format(context.trace_id, "032x") if context.is_valid else None
                span.end(end_time=end_ns)
                ended = True
                return trace_id
            finally:
                if not ended:
                    span.end(end_time=end_ns)
        except Exception as exc:  # noqa: BLE001 - exporter failures must never escape the control path
            self._error = f"{type(exc).__name__}: {exc}"
            return None

    async def health(self) -> dict:
        if self._tracer is None:
            return {"status": "degraded", "endpoint": self._endpoint, "detail": self._error}
        import httpx

        checks: dict[str, dict[str, Any]] = {}
        urls = {
            "collector": os.getenv("S9_COLLECTOR_HEALTH_URL", "http://127.0.0.1:9133/"),
            "langfuse": os.getenv("S9_LANGFUSE_HEALTH_URL", "http://127.0.0.1:9030/api/public/health"),
        }
        async with httpx.AsyncClient(timeout=2.0, trust_env=False) as client:
            for key, url in urls.items():
                try:
                    response = await client.get(url)
                    checks[key] = {"ok": 200 <= response.status_code < 300, "status_code": response.status_code}
                except Exception as exc:  # noqa: BLE001 - health must report dependency failures
                    checks[key] = {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}
        ready = all(item.get("ok") for item in checks.values())
        return {"status": "ready" if ready else "degraded", "endpoint": self._endpoint, "checks": checks}

    def flush(self) -> None:
        if self._provider is not None:
            self._provider.force_flush()
