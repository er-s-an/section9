#!/usr/bin/env python3
"""Manage only this checkout's Section9 server; never kill an unrelated listener."""
import argparse
import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".runtime"
PID_FILE = RUNTIME / "server.pid"
URL = "http://127.0.0.1:9019"
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def alive():
    try:
        with OPENER.open(URL + "/api/health", timeout=3) as result:
            return json.loads(result.read()).get("status") == "running"
    except (OSError, ValueError):
        return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["start", "stop", "status", "reset"])
    args = parser.parse_args()
    RUNTIME.mkdir(exist_ok=True)
    if args.command == "status":
        print("running " + URL if alive() else "stopped")
        return 0 if alive() else 1
    if args.command == "reset":
        request = urllib.request.Request(URL + "/api/reset", data=b"{}", headers={"Content-Type": "application/json"})
        with OPENER.open(request, timeout=10) as response:
            print(response.read().decode())
        return 0
    if args.command == "stop":
        if PID_FILE.exists():
            pid = int(PID_FILE.read_text())
            command = subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True).stdout
            if str(ROOT) in command and "uvicorn" in command and "s9.api:app" in command:
                os.kill(pid, signal.SIGTERM)
                for _ in range(150):
                    try:
                        os.kill(pid, 0)
                    except ProcessLookupError:
                        break
                    time.sleep(.2)
                else:
                    raise SystemExit("Section9 is still shutting down; no unrelated process was touched")
                print("Section9 stopped")
            else:
                print("PID no longer belongs to this Section9 checkout; left untouched")
        return 0
    if alive():
        print("Already running: " + URL)
        return 0
    python = ROOT / ".venv" / "bin" / "python"
    with (RUNTIME / "server.log").open("ab") as log:
        child = subprocess.Popen([str(python), "-m", "uvicorn", "s9.api:app", "--host", "127.0.0.1", "--port", "9019", "--no-access-log"],
                                 cwd=ROOT, stdout=log, stderr=log, start_new_session=True)
    PID_FILE.write_text(str(child.pid))
    if sys.platform == "darwin" and Path("/usr/bin/caffeinate").exists():
        # Process-scoped idle-sleep assertion, released when this server exits.
        # No system power settings are changed.
        with open(os.devnull, "wb") as null:
            guard = subprocess.Popen(["/usr/bin/caffeinate", "-i", "-w", str(child.pid)], stdout=null, stderr=null, start_new_session=True)
        (RUNTIME / "caffeinate.pid").write_text(str(guard.pid))
    for _ in range(40):
        if alive():
            print("Ready: " + URL)
            return 0
        if child.poll() is not None:
            break
        time.sleep(.5)
    print("Startup failed; inspect " + str(RUNTIME / "server.log"), file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
