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
sys.path.insert(0, str(ROOT))
from s9.provenance import capture_identity, identity_mismatches  # noqa: E402
from s9 import config  # noqa: E402
RUNTIME = ROOT / ".runtime"
PID_FILE = RUNTIME / "server.pid"
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def url() -> str:
    return f"http://127.0.0.1:{config.PORT}"


def listener_health():
    try:
        with OPENER.open(url() + "/api/health", timeout=3) as result:
            return json.loads(result.read())
    except (OSError, ValueError):
        return None


def alive():
    payload = listener_health()
    return bool(payload and payload.get("status") == "running")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["start", "stop", "stop-app", "status", "reset"])
    args = parser.parse_args()
    RUNTIME.mkdir(exist_ok=True)
    if args.command == "status":
        print("running " + url() if alive() else "stopped")
        return 0 if alive() else 1
    if args.command == "reset":
        request = urllib.request.Request(url() + "/api/reset", data=b"{}", headers={"Content-Type": "application/json"})
        with OPENER.open(request, timeout=10) as response:
            print(response.read().decode())
        return 0
    if args.command in {"stop", "stop-app"}:
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
        guard_file = RUNTIME / "caffeinate.pid"
        if guard_file.exists():
            try:
                os.kill(int(guard_file.read_text()), signal.SIGTERM)
            except (ValueError, ProcessLookupError, PermissionError):
                pass
            guard_file.unlink(missing_ok=True)
        return 0
    expected = capture_identity()
    existing = listener_health()
    if existing and existing.get("status") == "running":
        observed = existing.get("identity")
        if not isinstance(observed, dict):
            raise SystemExit("Refusing existing Section9 listener without startup identity; stop it, then start this checkout")
        mismatches = identity_mismatches(expected, observed)
        if mismatches:
            fields = ", ".join(sorted(mismatches))
            raise SystemExit(f"Refusing existing Section9 listener with mismatched identity ({fields}); stop/start the matching checkout")
        print("Already running: " + url())
        return 0
    python = ROOT / ".venv" / "bin" / "python"
    with (RUNTIME / "server.log").open("ab") as log:
        child = subprocess.Popen([str(python), "-m", "uvicorn", "s9.api:app", "--host", "127.0.0.1", "--port", str(config.PORT), "--no-access-log", "--timeout-graceful-shutdown", "5"],
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
            ready = listener_health()
            if not isinstance(ready, dict) or not isinstance(ready.get("identity"), dict):
                raise SystemExit("Started Section9 listener did not publish startup identity; refusing an unbound process")
            mismatches = identity_mismatches(expected, ready["identity"])
            if mismatches:
                raise SystemExit("Started listener identity does not match this checkout: " + ", ".join(sorted(mismatches)))
            print("service_alive: " + url() + " (business_unchecked; use scripts/readiness.py --business explicitly)")
            return 0
        if child.poll() is not None:
            break
        time.sleep(.5)
    print("Startup failed; inspect " + str(RUNTIME / "server.log"), file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
