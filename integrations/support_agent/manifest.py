from __future__ import annotations

from typing import Any

COMMIT = "860a29ff80f6e9b866e4cf1098c042e0da425f24"

MANIFEST: dict[str, Any] = {
    "project_id": "support-agent",
    "environment_id": "local-test",
    "name": "support-agent",
    "repo_url": "https://github.com/Pragatheswar-72/support-agent",
    "commit": COMMIT,
    "license": "MIT",
    "commands": {"run": ["uvicorn", "api:app", "--host", "127.0.0.1", "--port", "8127"],
                  "stop": "terminate the owned uvicorn process", "test": ["pytest", "-q"]},
    "port": 8127,
    "source_path": ".runtime/external-projects/support-agent",
    "data_paths": ["support_agent.db", "*.db"],
    "allowed_paths": ["integrations/support_agent/", "s9/connectors/", "tests/connectors/"],
    "protected_paths": ["s9/api.py", "s9/core.py", "s9/store.py", "s9/contracts.py", "pyproject.toml", "uv.lock", "frontend/", ".env", "CLAIMS.md"],
    # These are names only.  Values are supplied at runtime and are never
    # persisted in the manifest or evidence bundle.
    "model_credential_refs": ["GEMINI_API_KEY", "GEMINI_API_KEYS", "GROQ_API_KEY"],
    "adapter_credential_refs": ["SECTION9_MODEL_API_KEY", "SECTION9_MODEL_URL", "SECTION9_MODEL"],
    "model_adapter": {"provider": "openai_compatible", "package": "langchain-openai", "separate_from_repair": True},
    "side_effects": ["network_http", "model_inference", "local_database_read_write", "possible_support_action_mutation"],
    "business_truth_samples": [
        {"name": "health", "path": "/health", "method": "GET", "expected": {"status": "ok"}},
        {"name": "chat_contract", "path": "/chat", "method": "POST", "expected_fields": ["session_id", "reply", "agent", "trace", "usage"]},
        {"name": "order_truth", "path": "/chat", "method": "POST", "request": {"message": "Where is order 1001?"},
         # Both read-only tools expose the same seeded status. The model may
         # choose either based on its interpretation of a shipment question.
         "expected_trace_tools": ["get_order_status", "track_shipment"], "expected_trace_args": {"order_id": 1001},
         "expected_status": "delivered", "side_effect": "none"},
        {"name": "refund_guardrail", "path": "/chat", "method": "POST",
         "request": {"message": "Check whether order 1002 is eligible for a refund; check eligibility only and do not issue or initiate a refund."},
         "expected_trace_tool": "check_refund_eligibility", "expected_trace_args": {"order_id": 1002},
         "expected_eligible": False, "side_effect": "none"},
    ],
}


def get_manifest() -> dict[str, Any]:
    return MANIFEST.copy()
