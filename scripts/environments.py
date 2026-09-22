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
    port = 9024
    runtime = ROOT / ".runtime"
    runtime.mkdir(exist_ok=True)
    pid_file = runtime / "evaluation.pid"
    expected = capture_identity()
    with httpx.Client(timeout=2, trust_env=False) as client:
        try:
            health = client.get(f"http://127.0.0.1:{port}/api/health")
            running = health.is_success
            if running:
                error = identity_error(health.json(), expected)
                if error:
                    raise SystemExit(f"Evaluation listener identity rejected: {error}; stop/start the matching checkout")
        except httpx.HTTPError:
            running = False
        if args.command == "status":
            print(json.dumps({"running": running, "url": f"http://127.0.0.1:{port}"}))
            return
        if args.command == "stop":
            if pid_file.exists():
                pid = int(pid_file.read_text())
                command = subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True).stdout
                if str(ROOT) in command and "uvicorn" in command and str(port) in command:
                    os.kill(pid, signal.SIGTERM)
                    for _ in range(150):
                        try:
                            os.kill(pid, 0)
                        except ProcessLookupError:
                            break
                        time.sleep(.2)
                    else:
                        raise SystemExit("Evaluation is still shutting down; refused to start over its port")
            return
        if not running:
            env = dict(os.environ)
            env.update(S9_DATA_DIR=str(ROOT / "data" / "evaluation"), S9_PORT=str(port), S9_ENVIRONMENT="evaluation")
            with (runtime / "evaluation.log").open("ab") as log:
                child = subprocess.Popen([str(ROOT / ".venv/bin/python"), "-m", "uvicorn", "s9.api:app", "--host", "127.0.0.1", "--port", str(port), "--no-access-log", "--timeout-graceful-shutdown", "5"],
                                         cwd=ROOT, env=env, stdout=log, stderr=log, start_new_session=True)
            pid_file.write_text(str(child.pid))
            wait_for_ready(client, port, child, expected)
        print(f"Evaluation environment: http://127.0.0.1:{port}")


if __name__ == "__main__":
    main()
