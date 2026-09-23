"""Application-side Langfuse instrumentation, copied into the adapted checkout.

Uses Langfuse's LangChain callback for the existing graph, models and tools.
No Section9 runtime/database import and no substitute traces from the observer.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from functools import lru_cache
import os
import re
from typing import Any
from collections import OrderedDict
from threading import RLock
from datetime import datetime, timezone

_live: OrderedDict[str, dict] = OrderedDict()
_live_lock = RLock()
_session: ContextVar[str | None] = ContextVar("support_agent_session", default=None)


def live_observations(session_id: str) -> dict:
    import copy
    with _live_lock:
        return copy.deepcopy(_live.get(session_id, {"state": "not_started", "events": []}))


def _span_processor():
    from opentelemetry.sdk.trace import SpanProcessor

    class LiveProcessor(SpanProcessor):
        def on_start(self, span, parent_context=None):
            self.save(span, "started")

        def on_end(self, span):
            self.save(span, "completed")

        def save(self, span, phase):
            trace_id = format(span.context.trace_id, "032x")
            with _live_lock:
                session_id = _session.get()
                if not session_id:
                    session_id = next((k for k, v in _live.items() if v.get("trace_id") == trace_id), None)
                if not session_id or session_id not in _live:
                    return
                record = _live[session_id]
                record["trace_id"] = trace_id
                attributes = dict(span.attributes or {})
                record["events"].append({
                    "id": format(span.context.span_id, "016x") + ":" + phase,
                    "observation_id": format(span.context.span_id, "016x"),
                    "trace_id": trace_id, "name": span.name, "phase": phase,
                    "parent_id": format(span.parent.span_id, "016x") if span.parent else None,
                    "at": datetime.now(timezone.utc).isoformat(),
                    "type": attributes.get("langfuse.observation.type", "span"),
                    "level": attributes.get("langfuse.observation.level", "DEFAULT"),
                    "source": "application_live",
                })
                record["events"] = record["events"][-200:]

    return LiveProcessor()

_callbacks: ContextVar[list] = ContextVar("support_agent_callbacks", default=[])
_secret_key = re.compile(r"password|secret|authorization|api.?key|access.?token|cookie", re.I)
_credential = re.compile(r"(?i)\bBearer\s+[^\s\"']+|\b(?:sk|pk)-(?:lf-)?[A-Za-z0-9_-]{12,}")


def redact(data: Any, *, _depth: int = 0, **_: Any) -> Any:
    """Bound context size and remove credential fields before SDK export."""
    if _depth > 12:
        return "[depth limit]"
    if isinstance(data, dict):
        return {str(k): "[redacted]" if _secret_key.search(str(k)) else redact(v, _depth=_depth + 1)
                for k, v in list(data.items())[:100]}
    if isinstance(data, (list, tuple)):
        return [redact(v, _depth=_depth + 1) for v in data[:100]]
    if isinstance(data, str):
        return _credential.sub("[redacted]", data)[:16000]
    if data is None or isinstance(data, (bool, int, float)):
        return data
    if hasattr(data, "model_dump"):
        return redact(data.model_dump(), _depth=_depth + 1)
    return redact(str(data), _depth=_depth + 1)


@lru_cache(maxsize=1)
def _client():
    if not (os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY")
            and os.getenv("LANGFUSE_BASE_URL")):
        return None
    from langfuse import Langfuse

    from opentelemetry.sdk.trace import TracerProvider
    provider = TracerProvider()
    provider.add_span_processor(_span_processor())
    return Langfuse(
        public_key=os.environ["LANGFUSE_PUBLIC_KEY"],
        secret_key=os.environ["LANGFUSE_SECRET_KEY"],
        base_url=os.environ["LANGFUSE_BASE_URL"],
        environment=os.getenv("SUPPORT_AGENT_ENVIRONMENT", "local-test"),
        release=os.getenv("SUPPORT_AGENT_SOURCE_COMMIT"),
        timeout=5, mask=redact, flush_interval=1, tracer_provider=provider,
    )


def graph_config() -> dict:
    return {"callbacks": list(_callbacks.get())}


@contextmanager
def request_trace(message: str, session_id: str):
    """One root per real /chat request; callback children share its trace ID.

Returning a trace ID means export was attempted, not that Langfuse ingested it.
The reader must confirm ingestion separately. Export failures never replay a
business operation or turn a failed business operation into success.
"""
    record: dict[str, Any] = {"trace_id": None, "status": "unconfigured", "output": None}
    try:
        client = _client()
    except Exception:
        client = None
        record["status"] = "unavailable"
    if client is None:
        yield record
        return

    from langfuse import propagate_attributes
    from langfuse.langchain import CallbackHandler

    metadata = {
        "project_id": "support-agent",
        "environment_id": os.getenv("SUPPORT_AGENT_ENVIRONMENT", "local-test"),
        "source_commit": os.getenv("SUPPORT_AGENT_SOURCE_COMMIT", "unknown"),
        "telemetry_origin": "application", "native_candidate_sdk": True,
    }
    session_token = _session.set(session_id)
    with _live_lock:
        _live[session_id] = {"state": "running", "events": [], "trace_id": None}
        while len(_live) > 100:
            _live.popitem(last=False)
    try:
        with client.start_as_current_observation(name="support-agent.chat", input={"message": message},
                                                metadata=metadata) as root:
            record.update(trace_id=root.trace_id, status="export_pending")
            with propagate_attributes(session_id=session_id, metadata=metadata,
                                      trace_name="support-agent.chat"):
                token = _callbacks.set([CallbackHandler(public_key=os.environ["LANGFUSE_PUBLIC_KEY"])])
                try:
                    yield record
                    output = record['output']
                    if isinstance(output, dict) and 'telemetry_status' in output:
                        output = {**output, 'telemetry_status': record['status']}
                    root.update(output=output)
                except Exception as exc:
                    root.update(level="ERROR", status_message=type(exc).__name__)
                    raise
                finally:
                    _callbacks.reset(token)
    finally:
        _session.reset(session_token)
        with _live_lock:
            _live[session_id]["state"] = "finished"
    try:
        client.flush()
    except Exception:
        record["status"] = "export_failed"
