#!/usr/bin/env python3
"""Start a separate evaluation process, database, workers and memory directory."""
import argparse
import json
import os
import signal
import subprocess
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["start", "stop", "status"])
    args = parser.parse_args()
    port = 9024
    runtime = ROOT / ".runtime"
    runtime.mkdir(exist_ok=True)
    pid_file = runtime / "evaluation.pid"
    with httpx.Client(timeout=2, trust_env=False) as client:
        try:
            running = client.get(f"http://127.0.0.1:{port}/api/health").is_success
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
            return
        if not running:
            env = dict(os.environ)
            env.update(S9_DATA_DIR=str(ROOT / "data" / "evaluation"), S9_PORT=str(port), S9_ENVIRONMENT="evaluation")
            with (runtime / "evaluation.log").open("ab") as log:
                child = subprocess.Popen([str(ROOT / ".venv/bin/python"), "-m", "uvicorn", "s9.api:app", "--host", "127.0.0.1", "--port", str(port), "--no-access-log"],
                                         cwd=ROOT, env=env, stdout=log, stderr=log, start_new_session=True)
            pid_file.write_text(str(child.pid))
            for _ in range(40):
                try:
                    if client.get(f"http://127.0.0.1:{port}/api/health").is_success:
                        break
                except httpx.HTTPError:
                    pass
                if child.poll() is not None:
                    raise SystemExit("Evaluation startup failed; inspect .runtime/evaluation.log")
                time.sleep(.5)
        print(f"Evaluation environment: http://127.0.0.1:{port}")


if __name__ == "__main__":
    main()
