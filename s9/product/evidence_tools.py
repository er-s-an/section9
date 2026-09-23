"""Strict, read-only access to detailed fields in a frozen task evidence context."""

from __future__ import annotations

import json
import re
from typing import Any


MAX_READ_BYTES = 8_000
MAX_TOTAL_READ_BYTES = 16_000
MAX_POINTER_DEPTH = 8
MAX_WINDOW_CHARS = 2_000
DEFAULT_WINDOW_CHARS = 2_000
_SEGMENT = re.compile(r"^[A-Za-z0-9_ -]{1,80}$")


class EvidenceReadError(ValueError):
    """A model requested evidence outside the tool's read-only allowlist."""


def read_bound_evidence(
    *, aliases: dict[str, str], evidence: dict[str, dict[str, Any]],
    evidence_id: str, field: str, path: str = "", offset: int = 0,
    limit: int = DEFAULT_WINDOW_CHARS,
) -> dict[str, Any]:
    """Read one bounded JSON value from a signal already bound to this task.

    `path` is a restricted JSON-pointer-like path relative to `field`, never a
    filesystem path. Only summary and source_context are exposed.
    """
    if evidence_id not in aliases:
        raise EvidenceReadError("EVIDENCE_ID_NOT_ALLOWED")
    if field not in {"summary", "source_context"}:
        raise EvidenceReadError("EVIDENCE_FIELD_NOT_ALLOWED")
    if not isinstance(path, str) or len(path) > 256 or (path and not path.startswith("/")):
        raise EvidenceReadError("EVIDENCE_PATH_INVALID")
    if field == "source_context" and not path:
        raise EvidenceReadError("EVIDENCE_PATH_REQUIRED")
    if (type(offset) is not int or offset < 0 or offset > 100_000
            or type(limit) is not int or limit < 1 or limit > MAX_WINDOW_CHARS):
        raise EvidenceReadError("EVIDENCE_WINDOW_INVALID")
    segments = [] if not path else path[1:].split("/")
    if len(segments) > MAX_POINTER_DEPTH or any(
        not _SEGMENT.fullmatch(segment) or "~" in segment for segment in segments
    ):
        raise EvidenceReadError("EVIDENCE_PATH_INVALID")
    canonical_id = aliases[evidence_id]
    item = evidence.get(canonical_id)
    if not isinstance(item, dict):
        raise EvidenceReadError("EVIDENCE_NOT_FOUND")
    source = item.get("source_record")
    if not isinstance(source, dict):
        raise EvidenceReadError("EVIDENCE_DETAIL_UNAVAILABLE")
    value: Any = source.get(field)
    for segment in segments:
        if isinstance(value, dict) and segment in value:
            value = value[segment]
        elif isinstance(value, list) and segment.isdigit() and int(segment) < len(value):
            value = value[int(segment)]
        else:
            raise EvidenceReadError("EVIDENCE_PATH_NOT_FOUND")
    window = None
    if isinstance(value, str):
        total_characters = len(value)
        if offset > total_characters:
            raise EvidenceReadError("EVIDENCE_WINDOW_OUT_OF_RANGE")
        value = value[offset:offset + limit]
        window = {"offset": offset, "limit": limit, "total_characters": total_characters,
                  "truncated": offset + len(value) < total_characters}
    elif offset != 0:
        raise EvidenceReadError("EVIDENCE_WINDOW_REQUIRES_TEXT")
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    size = len(value.encode("utf-8")) if isinstance(value, str) else len(encoded.encode("utf-8"))
    if size > MAX_READ_BYTES:
        raise EvidenceReadError("EVIDENCE_DETAIL_TOO_LARGE")
    result = {"evidence_id": evidence_id, "field": field, "path": path, "value": value,
              "size_bytes": size, "content_is_untrusted_data": True}
    if window is not None:
        result["window"] = window
    return result


def evidence_tool_schema(allowed_aliases: list[str]) -> list[dict[str, Any]]:
    """Return the sole supported model tool with call-specific ID allowlisting."""
    return [{
        "type": "function",
        "function": {
            "name": "read_evidence",
            "description": "Read a small detail from evidence already provided to this task. Read only.",
            "parameters": {
                "type": "object",
                "properties": {
                    "evidence_id": {"type": "string", "enum": allowed_aliases},
                    "field": {"type": "string", "enum": ["summary", "source_context"]},
                    "path": {"type": "string", "maxLength": 256,
                             "description": "Required restricted JSON pointer to a specific evidence field; do not request the whole source_context object."},
                    "offset": {"type": "integer", "minimum": 0, "maximum": 100000,
                               "description": "Character offset for a text value; defaults to 0."},
                    "limit": {"type": "integer", "minimum": 1, "maximum": MAX_WINDOW_CHARS,
                              "description": "Maximum text characters returned; defaults to 2000."},
                },
                "required": ["evidence_id", "field", "path"],
                "additionalProperties": False,
            },
        },
    }]
