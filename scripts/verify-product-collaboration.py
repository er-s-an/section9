#!/usr/bin/env python3
"""Capture sanitized G4 evidence from the live Section9 API."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import urllib.error
import urllib.request
from urllib.parse import quote


ROOT = Path(__file__).resolve().parents[1]
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

SCENARIO_CHECKS = {
    "memory": ("known_usage_within_budget", "structured_message_contract", "approved_experience_reused"),
    "muted": ("known_usage_within_budget", "communication_isolation"),
    "swarm": ("known_usage_within_budget", "fencing_handoff", "structured_message_contract"),
}


def get_json(base_url: str, path: str) -> dict:
    try:
        with OPENER.open(base_url.rstrip("/") + path, timeout=10) as response:
            return json.loads(response.read())
    except (OSError, ValueError, urllib.error.URLError) as exc:
        raise SystemExit(f"could not capture live evidence from {path}: {type(exc).__name__}") from exc


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:9019")
    parser.add_argument("--run-id", action="append", required=True,
                        help="specific browser-triggered G4 run id; repeat for each run")
    parser.add_argument("--output", type=Path,
                        default=ROOT / "artifacts" / "external-support-agent" / "g4-local-collaboration-final.json")
    args = parser.parse_args()
    state = get_json(args.base_url, "/api/state")
    capabilities = get_json(args.base_url, "/api/product/capabilities")
    runs = {run.get("id"): run for run in state.get("runs", []) if isinstance(run, dict)}
    reports: list[dict] = []
    for run_id in args.run_id:
        run = runs.get(run_id)
        if not run:
            raise SystemExit(f"run id not present in live state: {run_id}")
        detail = get_json(args.base_url, f"/api/runs/{quote(run_id, safe='')}")
        scoped = [event for event in detail.get("events", []) if isinstance(event, dict)]
        stale = [event for event in scoped if event.get("event_type") in {"action.rejected", "grant.rejected"}
                 and (event.get("payload") or {}).get("code") == "FENCE_STALE"]
        resumed = [event for event in scoped if event.get("event_type") == "agent.resumed"]
        dropped = [event for event in scoped if event.get("event_type") == "dialog.dropped"
                   and (event.get("payload") or {}).get("reason") == "MUTED"]
        received = [event for event in scoped if event.get("event_type") == "dialog.received"]
        structured = [event for event in received if all((event.get("payload") or {}).get(field) is not None
                                                         for field in ("generation", "task_epoch", "transport_epoch"))]
        checks = {
            "known_usage_within_budget": not run.get("usage_unknown") and not run.get("budget_overrun")
            and isinstance(run.get("usage_tokens"), int) and isinstance(run.get("token_budget"), int)
            and run["usage_tokens"] <= run["token_budget"],
            "fencing_handoff": bool(stale and resumed),
            "communication_isolation": bool(dropped),
            "structured_message_contract": bool(structured),
            "approved_experience_reused": bool(run.get("reuseapproved") is True and run.get("memory_used_id")),
        }
        expected = SCENARIO_CHECKS.get(run.get("condition"), ())
        reports.append({
            "run_id": run_id,
            "condition": run.get("condition"),
            "status": run.get("status"),
            "usage_tokens": run.get("usage_tokens"),
            "token_budget": run.get("token_budget"),
            "usage_unknown": bool(run.get("usage_unknown")),
            "budget_overrun": bool(run.get("budget_overrun")),
            "evidence_counts": {"fence_stale_rejected": len(stale), "agent_resumed": len(resumed),
                                "muted_messages_dropped": len(dropped), "structured_messages": len(structured)},
            "checks": checks,
            "scenario_expected_checks": list(expected),
            "scenario_not_applicable_checks": [name for name in checks if name not in expected],
            "scenario_checks_passed": bool(expected) and all(checks[name] for name in expected),
        })
    local = capabilities.get("local", {})
    official = capabilities.get("official_remote", {})
    capability_status = {name: value.get("status", "unknown") for name, value in local.items() if isinstance(value, dict)}
    official_status = {name: value.get("status", "unknown") for name, value in official.items() if isinstance(value, dict)}
    report = {
        "schema_version": "product-collaboration-evidence-v1",
        "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "scope": capabilities.get("scope"),
        "runs": reports,
        "local_capability_status": capability_status,
        "official_remote_status": official_status,
        "checks": {
            "all_requested_runs_present": len(reports) == len(args.run_id),
            "all_known_usage_within_budget": all(row["checks"]["known_usage_within_budget"] for row in reports),
            "all_scenario_acceptance_checks_passed": all(row["scenario_checks_passed"] for row in reports),
            "fencing_observed": any(row["checks"]["fencing_handoff"] for row in reports),
            "communication_isolation_observed": any(row["checks"]["communication_isolation"] for row in reports),
            "structured_messages_observed": any(row["checks"]["structured_message_contract"] for row in reports),
            "approved_experience_reused": any(row["checks"]["approved_experience_reused"] for row in reports),
            "official_remote_not_overclaimed": all(value in {"unsupported", "unknown"} for value in official_status.values()),
        },
    }
    target = args.output.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(target), "checks": report["checks"],
                      "local_capabilities": capability_status, "official_remote": official_status}, ensure_ascii=False))
    return 0 if all(report["checks"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
