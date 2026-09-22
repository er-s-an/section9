"""Run one fully scoped external-product closure against the live local service.

The command records every HTTP response, elapsed time, and failure without
printing response bodies to stdout.  It is intentionally an operator harness:
the server remains authoritative for scope, approval, idempotency, and state
transitions.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from s9.product.bundle import redact_secrets  # noqa: E402


OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def call(base_url: str, method: str, path: str, body: dict[str, Any] | None = None,
         headers: dict[str, str] | None = None) -> dict[str, Any]:
    payload = None if body is None else json.dumps(body, ensure_ascii=False).encode()
    request = urllib.request.Request(
        base_url.rstrip("/") + path,
        data=payload,
        method=method,
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    started = time.monotonic()
    try:
        with OPENER.open(request, timeout=180) as response:
            raw = response.read()
            status_code = response.status
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        status_code = exc.code
    except Exception as exc:
        return {"method": method, "path": path, "status_code": None,
                "elapsed_s": round(time.monotonic() - started, 3),
                "error": type(exc).__name__}
    try:
        value: Any = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        value = {"raw_length": len(raw)}
    return {"method": method, "path": path, "status_code": status_code,
            "elapsed_s": round(time.monotonic() - started, 3), "body": redact_secrets(value)}


def step_ok(step: dict[str, Any]) -> bool:
    return step.get("status_code") is not None and 200 <= int(step["status_code"]) < 300


MAX_TOKENS_PER_BUSINESS_PROBE = 5000


def probe_usage_tokens(step: dict[str, Any] | None) -> int | None:
    body = (step or {}).get("body") or {}
    evidence = body.get("evidence")
    if not isinstance(evidence, dict):
        # Observe wraps a fresh business probe one level below its recovery receipt.
        evidence = ((body.get("probe") or {}).get("evidence"))
    usage = ((evidence or {}).get("value") or {}).get("response", {}).get("usage", {})
    value = usage.get("total_tokens") if isinstance(usage, dict) else None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    # A reported zero can come from the candidate's empty-usage fallback after
    # a failed model connection.  Treat it as unknown, not as a free request.
    return parsed if parsed > 0 else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:9019")
    parser.add_argument("--label", required=True, help="short evidence label, for example run-01")
    parser.add_argument("--report", default=None, help="output JSON path")
    parser.add_argument("--lab-reset", action="store_true", help="reset the built-in lab before the product run")
    parser.add_argument("--stop-first", action="store_true", help="stop the owned external runtime before connecting")
    parser.add_argument("--model-token-budget", type=int, default=16000,
                        help="known-token ceiling for this closure; each business sample reserves 5000 tokens")
    args = parser.parse_args()
    if args.model_token_budget < MAX_TOKENS_PER_BUSINESS_PROBE:
        parser.error(f"--model-token-budget must be at least {MAX_TOKENS_PER_BUSINESS_PROBE}")
    closure_started = time.monotonic()
    scope = {"project_id": "support-agent", "environment_id": "local-test"}
    steps: list[dict[str, Any]] = []
    lab_reset = None
    stop = None

    steps.append(call(args.base_url, "GET", "/api/product/snapshot"))
    if args.lab_reset:
        lab_reset = call(args.base_url, "POST", "/api/reset", {})
        steps.append(lab_reset)
    if args.stop_first:
        stop = call(args.base_url, "POST", "/api/product/stop", scope)
        steps.append(stop)
    connect = call(args.base_url, "POST", "/api/product/connect", {**scope, "run_tests": True})
    steps.append(connect)
    probe = call(args.base_url, "POST", "/api/product/business-probe", {**scope, "sample": "order_truth"})
    steps.append(probe)
    probe_tokens = probe_usage_tokens(probe)
    usage_safe = probe_tokens is not None and probe_tokens <= MAX_TOKENS_PER_BUSINESS_PROBE
    refund_probe = None
    if usage_safe and args.model_token_budget - probe_tokens >= MAX_TOKENS_PER_BUSINESS_PROBE:
        refund_probe = call(args.base_url, "POST", "/api/product/business-probe", {**scope, "sample": "refund_guardrail"})
        steps.append(refund_probe)
        refund_tokens = probe_usage_tokens(refund_probe)
        usage_safe = refund_tokens is not None and refund_tokens <= MAX_TOKENS_PER_BUSINESS_PROBE
    else:
        refund_tokens = None
        refund_probe = {"method": "POST", "path": "/api/product/business-probe", "status_code": None,
                        "error": "not_run_model_usage_unknown_or_budget_reservation_unavailable"}
        steps.append(refund_probe)
    regression = call(args.base_url, "POST", "/api/product/regression", scope)
    steps.append(regression)

    g2_incident = ((regression.get("body") or {}).get("incident") or {}).get("id")
    approval = None
    execution = None
    observation = None
    replay = None
    alternate_approval = None
    conflicting_execution = None
    known_before_recovery = [value for value in (probe_tokens, refund_tokens) if value is not None]
    recovery_budget_safe = usage_safe and len(known_before_recovery) == 2 \
        and args.model_token_budget - sum(known_before_recovery) >= MAX_TOKENS_PER_BUSINESS_PROBE
    if g2_incident and recovery_budget_safe:
        approval = call(args.base_url, "POST", "/api/product/approve", {**scope, "incident_id": g2_incident})
        steps.append(approval)
        approval_id = ((approval.get("body") or {}).get("approval_id"))
        if approval_id:
            execution = call(args.base_url, "POST", "/api/product/execute", {
                **scope, "incident_id": g2_incident, "approval_id": approval_id,
                "idempotency_key": f"closure-{args.label}-{g2_incident}",
            })
            steps.append(execution)
            observation = call(args.base_url, "POST", "/api/product/observe", {**scope, "incident_id": g2_incident})
            steps.append(observation)
            recovery_tokens = probe_usage_tokens(observation)
            replay = call(args.base_url, "POST", "/api/product/execute", {
                **scope, "incident_id": g2_incident, "approval_id": approval_id,
                "idempotency_key": f"closure-{args.label}-{g2_incident}",
            })
            steps.append(replay)
            alternate_approval = call(args.base_url, "POST", "/api/product/approve", {
                **scope, "incident_id": g2_incident,
            })
            steps.append(alternate_approval)
            alternate_id = (alternate_approval.get("body") or {}).get("approval_id")
            if alternate_id:
                conflicting_execution = call(args.base_url, "POST", "/api/product/execute", {
                    **scope, "incident_id": g2_incident, "approval_id": alternate_id,
                    "idempotency_key": f"closure-{args.label}-{g2_incident}",
                })
                steps.append(conflicting_execution)
    elif g2_incident:
        budget_skip = {"method": "POST", "path": "/api/product/approve", "status_code": None,
                       "error": "not_run_model_usage_unknown_or_recovery_budget_reservation_unavailable"}
        steps.append(budget_skip)
        recovery_tokens = None
    else:
        recovery_tokens = None

    unauthorized_worker = call(args.base_url, "GET", "/api/product/snapshot",
                               headers={"Authorization": "Bearer invalid-worker-credential"})
    wrong_origin = call(args.base_url, "GET", "/api/product/snapshot",
                        headers={"Origin": "https://untrusted.example"})
    cross_scope = call(args.base_url, "POST", "/api/product/connect",
                       {"project_id": "another-project", "environment_id": "local-test", "run_tests": False})
    cross_scope_incidents = call(args.base_url, "GET", "/api/product/incidents?project_id=another-project&environment_id=local-test")
    wrong_approval = call(args.base_url, "POST", "/api/product/execute", {
        **scope, "incident_id": g2_incident or "missing-incident", "approval_id": "approval_outside_scope",
        "idempotency_key": f"wrong-scope-{args.label}",
    })
    steps.extend([unauthorized_worker, wrong_origin, cross_scope, cross_scope_incidents, wrong_approval])
    steps.append(call(args.base_url, "GET", "/api/product/snapshot"))

    probe_body = probe.get("body") or {}
    refund_probe_body = refund_probe.get("body") or {}
    regression_body = regression.get("body") or {}
    verification = regression_body.get("verification") or {}
    approval_body = (approval or {}).get("body") or {}
    execution_body = (execution or {}).get("body") or {}
    observation_body = (observation or {}).get("body") or {}
    recovery = observation_body.get("recovery") or {}
    reset_body = (lab_reset or {}).get("body") or {}
    reset_checks = reset_body.get("checks") or {}
    checks = {
        "lab_reset_passed": not args.lab_reset or (step_ok(lab_reset or {})
            and all(value is True for value in reset_checks.values() if isinstance(value, bool))
            and reset_checks.get("cancelled_requests", 0) == 0
            and (reset_body.get("model_cancellation") or {}).get("remaining") == 0),
        "external_runtime_stopped_before_connect": not args.stop_first or (step_ok(stop or {})
            and ((stop or {}).get("body") or {}).get("status") == "stopped"),
        "connect_ready": connect.get("body", {}).get("status") == "ready",
        "business_probe_ready": (probe_body.get("probe") or {}).get("status") == "ready",
        "refund_guardrail_ready": (refund_probe_body.get("probe") or {}).get("status") == "ready",
        "model_usage_known_and_bounded": probe_tokens is not None and refund_tokens is not None
        and recovery_tokens is not None
        and all(value <= MAX_TOKENS_PER_BUSINESS_PROBE for value in (probe_tokens, refund_tokens, recovery_tokens))
        and sum((probe_tokens, refund_tokens, recovery_tokens)) <= args.model_token_budget,
        "verification_ready": verification.get("status") == "ready",
        "approval_granted": approval_body.get("decision") == "approved",
        "execution_succeeded": execution_body.get("status") == "succeeded",
        "recovery_recovered": recovery.get("status") == "recovered",
        "runtime_configuration_bound": bool(verification.get("configuration_sha256"))
        and approval_body.get("configuration_sha256") == verification.get("configuration_sha256")
        and execution_body.get("configuration_sha256") == verification.get("configuration_sha256")
        and recovery.get("configuration_sha256") == verification.get("configuration_sha256"),
        "source_revision_bound": execution_body.get("target_revision") == recovery.get("source_revision"),
        "idempotent_replay_same_attempt": step_ok(replay or {})
        and (replay.get("body") or {}).get("attempt_id") == execution_body.get("attempt_id"),
        "idempotency_conflict_denied": (conflicting_execution or {}).get("status_code") == 409
        and ((conflicting_execution or {}).get("body") or {}).get("error", {}).get("code") == "IDEMPOTENCY_CONFLICT",
        "worker_identity_denied": unauthorized_worker.get("status_code") == 403
        and (unauthorized_worker.get("body") or {}).get("error", {}).get("code") == "ROLE_FORBIDDEN",
        "untrusted_origin_denied": wrong_origin.get("status_code") == 403
        and (wrong_origin.get("body") or {}).get("error", {}).get("code") == "ORIGIN_FORBIDDEN",
        "cross_project_write_denied": cross_scope.get("status_code") == 403
        and (cross_scope.get("body") or {}).get("error", {}).get("code") == "SCOPE_FORBIDDEN",
        "cross_project_read_denied": cross_scope_incidents.get("status_code") == 403
        and (cross_scope_incidents.get("body") or {}).get("error", {}).get("code") == "SCOPE_FORBIDDEN",
        "unbound_approval_denied": wrong_approval.get("status_code") == 403
        and (wrong_approval.get("body") or {}).get("error", {}).get("code") == "APPROVAL_REQUIRED",
        "all_business_http_steps_succeeded": all(step_ok(step or {}) for step in (connect, probe, refund_probe, regression, approval, execution, observation)),
        "langfuse_trace_observed": (probe_body.get("incident") or {}).get("langfuse", {}).get("status") == "ready"
        and (probe_body.get("incident") or {}).get("langfuse", {}).get("observation_count", 0) > 0,
        "refund_langfuse_trace_observed": (refund_probe_body.get("incident") or {}).get("langfuse", {}).get("status") == "ready"
        and (refund_probe_body.get("incident") or {}).get("langfuse", {}).get("observation_count", 0) > 0,
    }
    report = {
        "schema_version": "product-closure-v1",
        "label": args.label,
        "scope": scope,
        "created_at_epoch_ms": int(time.time() * 1000),
        "elapsed_s": round(time.monotonic() - closure_started, 3),
        "checks": checks,
        "incident_ids": {
            "business_probe": (probe_body.get("incident") or {}).get("id"),
            "refund_guardrail": (refund_probe_body.get("incident") or {}).get("id"),
            "g2_g3": g2_incident,
        },
        "model_usage": {
            "budget_tokens": args.model_token_budget,
            "per_probe_reservation_tokens": MAX_TOKENS_PER_BUSINESS_PROBE,
            "probe_count_ceiling": 3,
            "known_tokens": {"order_truth": probe_tokens, "refund_guardrail": refund_tokens,
                             "post_release_recovery": recovery_tokens},
            "known_total_tokens": (probe_tokens + refund_tokens + recovery_tokens)
            if None not in (probe_tokens, refund_tokens, recovery_tokens) else None,
            "within_budget": checks["model_usage_known_and_bounded"],
        },
        "security": {
            "worker_identity": unauthorized_worker,
            "untrusted_origin": wrong_origin,
            "cross_project_write": cross_scope,
            "cross_project_read": cross_scope_incidents,
            "unbound_approval": wrong_approval,
            "idempotent_replay": replay,
            "idempotency_conflict": conflicting_execution,
        },
        "steps": steps,
    }
    report = redact_secrets(report)
    target = Path(args.report) if args.report else ROOT / "artifacts" / "external-support-agent" / "closure-runs" / f"{args.label}.json"
    target = target.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(target), "checks": checks, "all_passed": all(checks.values())}, ensure_ascii=False))
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
