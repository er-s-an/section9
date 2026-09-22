#!/usr/bin/env python3
"""Read-only dual-path telemetry proof for one paired experiment."""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import pathlib
import urllib.error
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
ENV = ROOT / "infra" / ".env"
OUT = ROOT / "artifacts" / "showcase-telemetry"
BASE = "http://127.0.0.1:9019"
LANGFUSE = "http://127.0.0.1:9030"
TIMEOUT = 5


def load_env() -> dict[str, str]:
    values = {}
    try:
        lines = ENV.read_text(encoding="utf-8").splitlines()
    except OSError:
        return values
    for line in lines:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] == '"':
                value = value[1:-1]
            values[key] = value
    return values


def get_json(url: str, headers: dict[str, str] | None = None) -> tuple[int | None, object]:
    request = urllib.request.Request(url, headers={"Accept": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, {"error": exc.read().decode("utf-8", errors="replace")[:300]}
    except (OSError, TimeoutError) as exc:
        return None, {"error": type(exc).__name__}


def span_ids(events: object) -> set[str]:
    found = set()
    for event in events if isinstance(events, list) else []:
        if not isinstance(event, dict) or event.get("event_type") != "telemetry.span_received":
            continue
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        trace_id = payload.get("trace_id")
        if isinstance(trace_id, str) and trace_id:
            found.add(trace_id)
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pair-id", required=True, help="pair id, for example pair_492144569b624bec")
    args = parser.parse_args(argv)
    checked = dt.datetime.now(dt.UTC)
    pair_id = args.pair_id
    catalog_status, catalog = get_json(f"{BASE}/api/pairs/{urllib.parse.quote(pair_id, safe='')}")
    catalog = catalog if isinstance(catalog, dict) else {}
    arms = {}
    endpoint_status = {}
    start_times = []
    for arm in ("baseline", "swarm"):
        url = f"{BASE}/api/pairs/{urllib.parse.quote(pair_id, safe='')}/arms/{arm}"
        status, payload = get_json(url)
        endpoint_status[arm] = status
        if status == 200 and isinstance(payload, dict):
            run_id = payload.get("run_id")
            arms[arm] = {"run_id": run_id, "pair_id": payload.get("pair_id"), "arm": payload.get("arm"),
                         "trace_ids": sorted(span_ids(payload.get("events"))),
                         "event_count": len(payload.get("events", [])) if isinstance(payload.get("events"), list) else 0}
            if payload.get("started_at"):
                start_times.append(str(payload["started_at"]))
        else:
            arms[arm] = {"run_id": None, "pair_id": None, "arm": None, "trace_ids": [], "event_count": 0}

    env = load_env()
    public, secret, project = (env.get(k, "") for k in ("LANGFUSE_INIT_PROJECT_PUBLIC_KEY", "LANGFUSE_INIT_PROJECT_SECRET_KEY", "LANGFUSE_INIT_PROJECT_ID"))
    langfuse_rows = []
    langfuse_status = None
    if public and secret:
        try:
            from_start = min(start_times) if start_times else (checked - dt.timedelta(hours=24)).isoformat().replace("+00:00", "Z")
            query = urllib.parse.urlencode({"limit": "100", "fields": "core,basic,metadata,time", "fromStartTime": from_start})
            auth = base64.b64encode(f"{public}:{secret}".encode()).decode()
            langfuse_status, response = get_json(f"{LANGFUSE}/api/public/v2/observations?{query}", {"Authorization": f"Basic {auth}"})
            rows = response.get("data", []) if isinstance(response, dict) else []
            wanted = {(arm["run_id"], arm_name) for arm_name, arm in arms.items() if arm["run_id"]}
            for row in rows if isinstance(rows, list) else []:
                if not isinstance(row, dict):
                    continue
                metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
                key = (metadata.get("run_id") or metadata.get("runId"), metadata.get("arm"))
                if row.get("projectId") == project and metadata.get("pair_id") == pair_id and key in wanted:
                    langfuse_rows.append({"trace_id": row.get("traceId") or row.get("trace_id"), "run_id": key[0], "arm": key[1], "metadata": {"pair_id": pair_id, "run_id": key[0], "arm": key[1]}, "name": row.get("name"), "timestamp": row.get("startTime") or row.get("createdAt")})
        except Exception as exc:
            langfuse_status = type(exc).__name__
    else:
        langfuse_status = "missing_local_project_credentials"

    lang_ids = {(row["run_id"], row["arm"], row["trace_id"]) for row in langfuse_rows if isinstance(row.get("trace_id"), str) and row["trace_id"]}
    matches = []
    for arm_name, arm in arms.items():
        for trace_id in arm["trace_ids"]:
            run_id = arm["run_id"]
            if (run_id, arm_name, trace_id) in lang_ids:
                matches.append({"arm": arm_name, "run_id": run_id, "trace_id": trace_id, "status": "both_paths_received"})
    binding_ok = catalog_status == 200 and all(
        arms[arm].get("pair_id") == pair_id and arms[arm].get("arm") == arm
        and arms[arm].get("run_id") == catalog.get(arm + "_run_id")
        for arm in arms
    )
    matches_by_arm = {arm: sum(1 for item in matches if item["arm"] == arm) for arm in arms}
    status = "received" if binding_ok and all(endpoint_status.get(a) == 200 for a in arms) and langfuse_status == 200 and all(matches_by_arm.values()) else "degraded"
    record = {"checked_at": checked.isoformat().replace("+00:00", "Z"), "pair_id": pair_id, "status": status,
              "endpoint_status": endpoint_status, "arms": arms, "langfuse_status": langfuse_status,
              "catalog_status": catalog_status, "catalog_binding_valid": binding_ok,
              "langfuse_observations": langfuse_rows, "both_path_matches": matches, "matches_by_arm": matches_by_arm,
              "limits": {"timeout_seconds": TIMEOUT, "langfuse_limit": 100, "coverage": "latest 100 Langfuse observations only; missing matches are degraded, not proof of absence", "read_only": True, "model_calls": 0},
              "criteria": "same full trace_id, pair_id, run_id, and arm in pair telemetry.span_received and local Langfuse metadata"}
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / checked.strftime("%Y%m%dT%H%M%SZ") / "report.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"status={status} pair_id={pair_id} matches={len(matches)} langfuse_status={langfuse_status} report={path}")
    return 0 if status == "received" else 1


if __name__ == "__main__":
    raise SystemExit(main())
