#!/usr/bin/env python3
"""Read-only proof that recent Section9 spans reached local Langfuse v4."""
from __future__ import annotations

import base64
import datetime as dt
import json
import pathlib
import sqlite3
import sys
import argparse
import urllib.error
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
ENV = ROOT / "infra" / ".env"
OUT = ROOT / "artifacts" / "telemetry"


def load_env() -> dict[str, str]:
    values: dict[str, str] = {}
    for line in ENV.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] == '"':
            value = value[1:-1]
        values[key] = value
    return values


def request_json(url: str, auth: str) -> tuple[int | None, object]:
    req = urllib.request.Request(url, headers={"Authorization": f"Basic {auth}", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, {"error": exc.read().decode("utf-8", errors="replace")[:500]}
    except (OSError, TimeoutError) as exc:
        return None, {"error": type(exc).__name__}


def safe_trace_id(value: object) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    return value


def display_trace_id(value: object) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    return f"{value[:8]}…{value[-4:]}" if len(value) > 12 else value


def database_events(path: pathlib.Path, run_id: str) -> tuple[list[dict], str | None]:
    if not path.exists():
        return [], "missing"
    events: list[dict] = []
    try:
        with sqlite3.connect(path) as db:
            for row in db.execute("SELECT data FROM events WHERE json_extract(data, '$.event_type') = 'telemetry.span_received'"):
                try:
                    event = json.loads(row[0])
                except (TypeError, json.JSONDecodeError):
                    continue
                if event.get("run_id") != run_id:
                    continue
                occurred = str(event.get("occurred_at") or "")
                payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
                trace_id = payload.get("trace_id")
                if isinstance(trace_id, str) and trace_id:
                    events.append({
                        "trace_id_full": trace_id,
                        "trace_id": safe_trace_id(trace_id),
                        "trace_id_display": display_trace_id(trace_id),
                        "run_id": event.get("run_id"),
                        "name": payload.get("name"),
                        "timestamp": occurred,
                    })
    except sqlite3.Error as exc:
        return [], type(exc).__name__
    return events, None


def evidence_run_id(path: pathlib.Path) -> str | None:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if isinstance(document, dict):
        direct = document.get("run_id")
        if isinstance(direct, str) and direct:
            return direct
        run = document.get("run")
        if isinstance(run, dict) and isinstance(run.get("id"), str) and run["id"]:
            return run["id"]
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--run-id", help="exact run id emitted by this acceptance/model run")
    source.add_argument("--evidence", type=pathlib.Path, help="JSON evidence file containing top-level run_id or run.id")
    args = parser.parse_args(argv)
    requested_run_id = args.run_id or (evidence_run_id(args.evidence) if args.evidence else None)
    if not requested_run_id:
        print("status=blocked reason=explicit_run_id_or_evidence_required", file=sys.stderr)
        return 2
    env = load_env()
    public = env.get("LANGFUSE_INIT_PROJECT_PUBLIC_KEY", "")
    secret = env.get("LANGFUSE_INIT_PROJECT_SECRET_KEY", "")
    project_id = env.get("LANGFUSE_INIT_PROJECT_ID", "")
    if not public or not secret:
        print("status=blocked reason=missing_local_project_credentials", file=sys.stderr)
        return 2
    auth = base64.b64encode(f"{public}:{secret}".encode()).decode()
    now = dt.datetime.now(dt.UTC)
    params = urllib.parse.urlencode({
        "limit": "100",
        "fields": "core,basic,metadata,time",
        "fromStartTime": (now - dt.timedelta(hours=24)).isoformat().replace("+00:00", "Z"),
    })
    http_status, payload = request_json(f"http://127.0.0.1:9030/api/public/v2/observations?{params}", auth)
    rows = payload.get("data", []) if isinstance(payload, dict) else []
    langfuse: list[dict[str, object]] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        service = str(row.get("serviceName") or row.get("service_name") or "")
        name = str(row.get("name") or "")
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        row_run_id = metadata.get("run_id") or metadata.get("runId")
        # The v2 list response does not always include serviceName; projectId is
        # the authoritative local-project boundary. Exclude only our own probes.
        is_local = row.get("projectId") == project_id or service == "section9"
        if is_local and name not in {"probe", "infra-probe"} and row_run_id == requested_run_id:
            langfuse.append({
                "trace_id_full": row.get("traceId") or row.get("trace_id"),
                "trace_id": safe_trace_id(row.get("traceId") or row.get("trace_id")),
                "trace_id_display": display_trace_id(row.get("traceId") or row.get("trace_id")),
                "name": name,
                "run_id": row_run_id,
                "timestamp": row.get("startTime") or row.get("start_time") or row.get("createdAt"),
                "metadata_complete": bool(metadata),
                "status": "received",
            })
    db_paths = {
        "main": ROOT / "data" / "section9.sqlite",
        "evaluation": ROOT / "data" / "evaluation" / "section9.sqlite",
        "attract": ROOT / "data" / "attract" / "section9.sqlite",
    }
    db_events: list[dict] = []
    db_status: dict[str, str] = {}
    for label, db_path in db_paths.items():
        found, error = database_events(db_path, requested_run_id)
        db_status[label] = "ok" if error is None else error
        for event in found:
            event["database"] = label
        db_events.extend(found)
    db_by_trace: dict[str, list[dict]] = {}
    for event in db_events:
        db_by_trace.setdefault(event["trace_id_full"], []).append(event)
    both: list[dict] = []
    for observation in langfuse:
        trace_id = observation.get("trace_id_full")
        candidates = db_by_trace.get(trace_id, [])
        for event in candidates:
            lf_run = observation.get("run_id")
            db_run = event.get("run_id")
            if lf_run != requested_run_id or db_run != requested_run_id:
                continue
            both.append({
                "trace_id": observation["trace_id"],
                "trace_id_display": observation.get("trace_id_display"),
                "name": observation.get("name"),
                "run_id": lf_run or db_run,
                "langfuse_timestamp": observation.get("timestamp"),
                "database_timestamp": event.get("timestamp"),
                "database": event.get("database"),
                "status": "both_paths_received",
            })
            break
    complete = bool(both) and all(item["metadata_complete"] and item.get("run_id") == requested_run_id for item in langfuse if item.get("trace_id_full") in db_by_trace)
    status = "received" if http_status == 200 and complete else "degraded"
    record = {
        "checked_at": now.isoformat().replace("+00:00", "Z"),
        "run_id": requested_run_id,
        "evidence": str(args.evidence) if args.evidence else None,
        "endpoint": "/api/public/v2/observations?fields=core,basic,metadata,time",
        "window_start": (now - dt.timedelta(hours=24)).isoformat().replace("+00:00", "Z"),
        "http_status": http_status,
        "status": status,
        "matching_spans": [{k: v for k, v in item.items() if k != "trace_id_full"} for item in langfuse[:5]],
        "database_events": [{k: v for k, v in item.items() if k != "trace_id_full"} for item in db_events[:5]],
        "both_path_matches": both[:5],
        "database_status": db_status,
        "returned_count": len(rows) if isinstance(rows, list) else 0,
        "criteria": "explicit run_id; same full trace_id and same run_id in Langfuse metadata observation and telemetry.span_received in main/evaluation/attract DB; metadata_complete must be true",
    }
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"telemetry-check-{now.strftime('%Y%m%dT%H%M%SZ')}.json"
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
    print(f"status={status} run_id={requested_run_id} langfuse_spans={len(langfuse)} db_events={len(db_events)} both_path_matches={len(both)} http_status={http_status} record={path}")
    for match in both[:5]:
        print(json.dumps(match, ensure_ascii=False, separators=(",", ":")))
    return 0 if status == "received" else 1


if __name__ == "__main__":
    raise SystemExit(main())
