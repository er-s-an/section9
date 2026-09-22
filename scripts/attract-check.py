#!/usr/bin/env python3
"""Run the real bounded attract loop and prove primary/evaluation isolation."""
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/attract" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def memory_hash():
    digest = hashlib.sha256()
    for folder in [ROOT / "data/memory", ROOT / "data/evaluation/evaluation-memory"]:
        for path in sorted(folder.glob("*")):
            if path.is_file():
                digest.update(path.name.encode())
                digest.update(path.read_bytes())
    return digest.hexdigest()


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    report = {"started_at": datetime.now(timezone.utc).isoformat(), "checks": [], "runs": []}
    with httpx.Client(timeout=10, trust_env=False) as client:
        main_url = "http://127.0.0.1:9019"
        child_url = "http://127.0.0.1:9020"
        before = client.get(main_url + "/api/state").json()
        assert not before["attract"]["enabled"], "Disable the existing attract session before this proof"
        assert not before["incident"], "Reset the main environment before this proof"
        before_memory = memory_hash()
        before_score = client.get(main_url + "/api/scoreboard").json()
        response = client.put(main_url + "/api/attract", json={"enabled": True})
        response.raise_for_status()
        start = time.monotonic()
        first = None
        completed = None
        seen = set()
        try:
            while time.monotonic() - start < 390:
                try:
                    snapshot = client.get(child_url + "/api/state").json()
                except httpx.HTTPError:
                    time.sleep(1)
                    continue
                if first is None:
                    first = snapshot
                    (OUT / "initial-state.json").write_text(json.dumps(first, ensure_ascii=False, indent=2))
                for run in snapshot["runs"]:
                    if run["opened_at"] < report["started_at"] or run["id"] in seen:
                        continue
                    if run["status"] in {"resolved", "failed", "reset"}:
                        seen.add(run["id"])
                        detail = client.get(child_url + "/api/runs/" + run["id"]).json()
                        report["runs"].append({"id": run["id"], "scenario": run["scenario"], "status": run["status"], "elapsed_s": run["elapsed_s"]})
                        (OUT / (run["id"] + ".json")).write_text(json.dumps(detail, ensure_ascii=False, indent=2))
                        print(json.dumps(report["runs"][-1]), flush=True)
                if snapshot["attract"]["cycle"] == 3 and not snapshot["attract"]["auto_running"]:
                    completed = snapshot
                    break
                time.sleep(2)
            assert completed is not None, "Attract did not finish its three bounded cycles"
            after = client.get(main_url + "/api/state").json()
            after_score = client.get(main_url + "/api/scoreboard").json()
            report["elapsed_s"] = round(time.monotonic() - start, 3)
            report["checks"] = [
                {"name": "three_actual_cycles", "passed": len(report["runs"]) == 3},
                {"name": "all_business_results_verified", "passed": all(r["status"] == "resolved" for r in report["runs"])},
                {"name": "main_config_unchanged", "passed": before["config"] == after["config"]},
                {"name": "main_incident_unchanged", "passed": before["incident"] == after["incident"]},
                {"name": "evaluation_score_unchanged", "passed": before_score == after_score},
                {"name": "main_and_evaluation_memory_unchanged", "passed": before_memory == memory_hash()},
                {"name": "automatic_calls_stopped", "passed": not completed["attract"]["auto_running"]},
            ]
            report["final_attract"] = completed["attract"]
            report["hardware_isolation"] = False
            (OUT / "final-state.json").write_text(json.dumps(completed, ensure_ascii=False, indent=2))
            assert all(check["passed"] for check in report["checks"]), report["checks"]
        finally:
            client.put(main_url + "/api/attract", json={"enabled": False})
            report["finished_at"] = datetime.now(timezone.utc).isoformat()
            (OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps({"report": str(OUT / "report.json"), "checks": report["checks"]}), flush=True)


if __name__ == "__main__":
    main()
