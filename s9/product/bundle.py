from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterable


_SECRET_KEY = re.compile(r"(?:secret|password|authorization|api[_ -]?key|access[_ -]?token|token(?:[_ -]?hash)?)", re.I)
_USAGE_METRIC_KEYS = {
    "token_budget", "total_tokens", "prompt_tokens", "completion_tokens", "input_tokens",
    "output_tokens", "reasoning_tokens", "usage_tokens", "reserved_tokens", "unknown_reserved_tokens",
    "budget_tokens", "known_tokens", "known_total_tokens", "per_probe_reservation_tokens",
    "token_limit", "validation_reserve_tokens", "budget_token_limit", "actual_tokens",
}
_REDACTED = "[REDACTED]"


def redact_secrets(value: Any, *, key: str | None = None) -> Any:
    """Return a JSON-safe copy with credential-like fields and hash fields redacted."""
    if key is not None:
        normalized_key = key.lower().replace("-", "_").replace(" ", "_")
        if normalized_key not in _USAGE_METRIC_KEYS and _SECRET_KEY.search(key):
            return _REDACTED
    if isinstance(value, dict):
        return {str(k): redact_secrets(v, key=str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_secrets(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    raise TypeError(f"unsupported artifact value: {type(value).__name__}")


class ArtifactBundleWriter:
    """Serialize already-collected records; it never probes, executes, or imports a service."""

    def __init__(self, directory: str | Path):
        self.directory = Path(directory)

    def write(self, records: Iterable[Any], *, bundle_name: str = "bundle") -> Path:
        payload = []
        for record in records:
            data = record.model_dump(mode="json") if hasattr(record, "model_dump") else record
            payload.append(redact_secrets(data))
        document = {"schema_version": "product-bundle-v1", "records": payload}
        encoded = (json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()
        digest = hashlib.sha256(encoded).hexdigest()
        target = self.directory / f"{bundle_name}.json"
        self.directory.mkdir(parents=True, exist_ok=True)
        target.write_bytes(encoded)
        (self.directory / f"{bundle_name}.sha256").write_text(digest + "  " + target.name + "\n", encoding="utf-8")
        return target
