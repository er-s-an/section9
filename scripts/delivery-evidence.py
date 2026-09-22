#!/usr/bin/env python3
"""Collect existing real evidence; never creates or fabricates benchmark runs."""
import json
import sqlite3
import statistics
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return json.loads((ROOT / path).read_text())


def main():
    with httpx.Client(trust_env=False, timeout=10) as client:
        state = client.get("http://127.0.0.1:9019/api/state").json()
        scoreboard = client.get("http://127.0.0.1:9019/api/scoreboard").json()
    conn = sqlite3.connect(ROOT / "data/evaluation/section9.sqlite")
    runs = [json.loads(row[0]) for row in conn.execute("SELECT data FROM runs")]
    conditions = []
    for name in ["single", "muted", "swarm", "memory"]:
        subset = [r for r in runs if r["condition"] == name]
        conditions.append({"condition": name, "n": len(subset), "passed": sum(r["status"] == "resolved" for r in subset),
            "failures": sum(r["status"] != "resolved" for r in subset),
            "median_s": round(statistics.median(r["elapsed_s"] for r in subset), 3),
            "range_s": [min(r["elapsed_s"] for r in subset), max(r["elapsed_s"] for r in subset)],
            "provider_tokens": sum(r["usage_tokens"] for r in subset), "unknown_usage_runs": sum(bool(r["usage_unknown"]) for r in subset),
            "run_ids": [r["id"] for r in subset]})
    latest = max((ROOT / "artifacts/telemetry").glob("telemetry-check-*.json"))
    telemetry = json.loads(latest.read_text())
    assert telemetry.get("both_path_matches"), "Dual-path evidence is required"
    final_browser = read("artifacts/final-browser/report.json")
    assert final_browser["passed"]
    assert state["incident"] is None and state["victim"]["active_requests"] == 0
    assert all(state["dependencies"][d]["status"] == "available" for d in ["model", "collector", "langfuse"])
    report = {"checked_at": datetime.now(timezone.utc).isoformat(), "local_url": "http://127.0.0.1:9019",
        "running": True, "autonomy": state["autonomy"], "muted": state["muted"], "memory_enabled": state["memory_enabled"],
        "config": state["config"], "dependencies": state["dependencies"], "conditions": conditions,
        "total_formal_runs": len(runs), "formal_passed": sum(r["status"] == "resolved" for r in runs),
        "formal_failures": [{"id": r["id"], "condition": r["condition"], "scenario": r["scenario"], "reason": r["failure_reason"]} for r in runs if r["status"] != "resolved"],
        "scoreboard": scoreboard, "component_tests": read("artifacts/component-tests.json"),
        "evidence": {
            "core_acceptance": "artifacts/final-acceptance.log", "prior_acceptance_failure": "artifacts/final-acceptance-attempt1.log",
            "latest_browser": "artifacts/final-browser/report.json",
            "memory": "artifacts/browser-memory/2026-09-21T20-54-58.590Z/report.json",
            "attract": "artifacts/attract/20260921T205623Z/report.json", "offline": "artifacts/offline/report.json",
            "startup": "artifacts/startup/report.json", "dual_telemetry": str(latest.relative_to(ROOT)),
            "langfuse_browser": "artifacts/observability-browser/browser-report.json",
            "partial_repair": "artifacts/live-safety/composite-partial-repair.json",
            "revision_race": "artifacts/live-safety/verification-revision-race.json",
            "poster": "artifacts/delivery/section9-A3-poster.pdf", "replay": "artifacts/delivery/section9-90s-replay.mp4"},
        "limits": ["Remote EvoMap inference; offline model use unavailable", "Hub OAuth/publication pending; official GEP SDK used locally",
                   "Jev disabled; 5th row untested", "Process/database isolation on one Mac, not hardware isolation",
                   "Provider cache and USD pricing not controlled; small samples do not establish superiority",
                   "Earlier Langfuse-only telemetry did not prove backend fan-out; final gzip fix verified by identical trace IDs",
                   "Failed calibration, budget-contention trial, and intentional resets retained separately from formal matrix",
                   "No push or public deployment; Star-Office artwork licensed for noncommercial demonstration only"]}
    output = ROOT / "artifacts/delivery/evidence-index.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps({"file": str(output), "formal_runs": len(runs), "formal_passed": report["formal_passed"], "running": True}))


if __name__ == "__main__":
    main()
