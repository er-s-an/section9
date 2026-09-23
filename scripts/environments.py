#!/usr/bin/env python3
"""Start a separate evaluation process, database, workers and memory directory."""
import argparse
import json
import os
import signal
import subprocess
import time
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from s9.provenance import capture_identity, identity_mismatches  # noqa: E402
from scripts.process_ownership import (  # noqa: E402
    capture_process_record,
    command_has_tokens,
    load_json_record,
    owned_process_matches,
    parse_pid_file,
    process_snapshot,
    write_json_record,
)


PORT = 9024
ROLE = "section9-evaluation"


def _command_matches(record: dict) -> bool:
    return command_has_tokens(
        record.get("command", ""), str(ROOT / ".venv" / "bin" / "python"),
        "-m", "uvicorn", "s9.api:app", "--host", "127.0.0.1", "--port", str(PORT),
    )


def _managed_record(runtime: Path) -> tuple[dict | None, dict | None, str | None]:
    record = load_json_record(runtime / "evaluation.identity.json")
    pid = parse_pid_file(runtime / "evaluation.pid")
    if record is None or pid is None or pid != record.get("pid"):
        return record, None, "missing or malformed evaluation process ownership metadata"
    observed = process_snapshot(pid)
    if observed is None:
        return record, None, None
    if not owned_process_matches(record, observed, role=ROLE, root=str(ROOT), port=PORT):
        return record, observed, "evaluation process identity changed; process left untouched"
    if not _command_matches(record):
        return record, observed, "evaluation executable or launch arguments differ; process left untouched"
    return record, observed, None


def _stop_managed(record: dict, timeout: float = 30) -> str:
    observed = process_snapshot(record["pid"])
    if observed is None:
        return "already stopped"
    if not owned_process_matches(record, observed, role=ROLE, root=str(ROOT), port=PORT) or not _command_matches(record):
        return "evaluation process identity changed; process left untouched"
    current = process_snapshot(record["pid"])
    if not owned_process_matches(record, current, role=ROLE, root=str(ROOT), port=PORT):
        return "evaluation process identity changed before signal; process left untouched"
    os.kill(record["pid"], signal.SIGTERM)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        current = process_snapshot(record["pid"])
        if current is None:
            return "stopped"
        if not owned_process_matches(record, current, role=ROLE, root=str(ROOT), port=PORT):
            return "original evaluation process exited; replacement left untouched"
        time.sleep(.2)
    return "still shutting down; no force-kill was sent"


def _write_pid(path: Path, pid: int) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(str(pid) + "\n", encoding="ascii")
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def identity_error(payload: dict, expected: dict) -> str | None:
    if payload.get("status") != "running":
        return "listener is not running"
    observed = payload.get("identity")
    if not isinstance(observed, dict):
        return "listener did not publish startup identity"
    mismatches = identity_mismatches(expected, observed)
    return ", ".join(sorted(mismatches)) if mismatches else None


def wait_for_ready(client, port: int, child, expected: dict, attempts: int = 40) -> None:
    for _ in range(attempts):
        try:
            response = client.get(f"http://127.0.0.1:{port}/api/health")
            if response.is_success:
                payload = response.json()
                error = identity_error(payload, expected)
                if error:
                    raise SystemExit(f"Evaluation listener identity rejected: {error}; stop/start the matching checkout")
                return
        except httpx.HTTPError:
            pass
        if child.poll() is not None:
            raise SystemExit("Evaluation startup failed; inspect .runtime/evaluation.log")
        time.sleep(.5)
    raise SystemExit("Evaluation startup timed out; inspect .runtime/evaluation.log")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["start", "stop", "status"])
    args = parser.parse_args()
    port = PORT
    runtime = ROOT / ".runtime"
    runtime.mkdir(exist_ok=True)
    pid_file = runtime / "evaluation.pid"
    record_file = runtime / "evaluation.identity.json"
    expected = capture_identity()
    with httpx.Client(timeout=2, trust_env=False) as client:
        try:
            health = client.get(f"http://127.0.0.1:{port}/api/health")
            running = health.is_success
            if running and args.command != "stop":
                error = identity_error(health.json(), expected)
                if error:
                    raise SystemExit(f"Evaluation listener identity rejected: {error}; stop/start the matching checkout")
        except httpx.HTTPError:
            running = False
        if args.command == "start" and running:
            record, observed, error = _managed_record(runtime)
            if error or record is None or observed is None or record.get("startup_identity") != health.json().get("identity"):
                raise SystemExit("Refusing existing evaluation listener without matching process ownership metadata")
        if args.command == "status":
            print(json.dumps({"running": running, "url": f"http://127.0.0.1:{port}"}))
            return
        if args.command == "stop":
            if not pid_file.exists() and not record_file.exists():
                if running:
                    raise SystemExit("Evaluation listener is running without ownership metadata; left untouched")
                return
            record, observed, error = _managed_record(runtime)
            if error or record is None:
                raise SystemExit("Refusing to stop evaluation: " + (error or "incomplete ownership metadata"))
            if running and health.json().get("identity") != record.get("startup_identity"):
                raise SystemExit("Evaluation listener identity differs from its process record; left untouched")
            if observed is None:
                print("Evaluation process already stopped; stale ownership record left in place")
                return
            result = _stop_managed(record)
            if result not in {"stopped", "already stopped"}:
                raise SystemExit("Evaluation " + result)
            return
        if not running:
            env = dict(os.environ)
            env.update(S9_DATA_DIR=str(ROOT / "data" / "evaluation"), S9_PORT=str(port), S9_ENVIRONMENT="evaluation")
            with (runtime / "evaluation.log").open("ab") as log:
                child = subprocess.Popen([str(ROOT / ".venv/bin/python"), "-m", "uvicorn", "s9.api:app", "--host", "127.0.0.1", "--port", str(port), "--no-access-log", "--timeout-graceful-shutdown", "5"],
                                         cwd=ROOT, env=env, stdout=log, stderr=log, start_new_session=True)
            record = capture_process_record(child.pid, role=ROLE, root=str(ROOT), port=PORT)
            if not record or not _command_matches(record):
                raise SystemExit("Evaluation process could not be captured as the expected executable; no process was signaled")
            record["startup_identity"] = expected
            write_json_record(record_file, record)
            _write_pid(pid_file, child.pid)
            wait_for_ready(client, port, child, expected)
        print(f"Evaluation environment: http://127.0.0.1:{port}")


if __name__ == "__main__":
    main()
