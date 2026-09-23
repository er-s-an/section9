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
from scripts.process_ownership import (  # noqa: E402
    capture_process_record,
    command_has_tokens,
    load_json_record,
    owned_process_matches,
    parse_pid_file,
    process_snapshot,
    write_json_record,
)
RUNTIME = ROOT / ".runtime"
PID_FILE = RUNTIME / "server.pid"
PID_ID_FILE = RUNTIME / "server.identity.json"
GUARD_FILE = RUNTIME / "caffeinate.identity.json"
LEGACY_GUARD_FILE = RUNTIME / "caffeinate.pid"
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
SERVER_ROLE = "section9-server"
GUARD_ROLE = "section9-caffeinate-guard"


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


def _server_command_matches(record):
    return command_has_tokens(
        record.get("command", ""), str(ROOT / ".venv" / "bin" / "python"),
        "-m", "uvicorn", "s9.api:app", "--host", "127.0.0.1", "--port", str(config.PORT),
    )


def _guard_command_matches(record, target_pid):
    return command_has_tokens(record.get("command", ""), "/usr/bin/caffeinate", "-i", "-w", str(target_pid))


def _read_owned_record(path, pid_path, *, role, port):
    record = load_json_record(path)
    pid = parse_pid_file(pid_path) if pid_path is not None else (record.get("pid") if record else None)
    if not record or pid is None or pid != record.get("pid"):
        return None, None, "missing or malformed process ownership record"
    observed = process_snapshot(pid)
    if observed is None:
        return record, None, None
    if not owned_process_matches(record, observed, role=role, root=str(ROOT), port=port):
        return record, observed, "process identity does not match the recorded Section9 owner"
    return record, observed, None


def _stop_record(record, *, role, port, command_check, timeout=30):
    observed = process_snapshot(record["pid"])
    if observed is None:
        return "already stopped"
    if not owned_process_matches(record, observed, role=role, root=str(ROOT), port=port):
        return "process identity changed; left it untouched"
    if not command_check(record):
        return "executable or launch arguments do not match; left it untouched"
    # Recheck immediately before signaling to narrow PID reuse between lookup and kill.
    current = process_snapshot(record["pid"])
    if not owned_process_matches(record, current, role=role, root=str(ROOT), port=port):
        return "process identity changed before signal; left it untouched"
    os.kill(record["pid"], signal.SIGTERM)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        current = process_snapshot(record["pid"])
        if current is None:
            return "stopped"
        if not owned_process_matches(record, current, role=role, root=str(ROOT), port=port):
            return "original process exited; replacement process left untouched"
        time.sleep(.2)
    return "still shutting down; no force-kill was sent"


def _stop_guard(server_record):
    guard_record = load_json_record(GUARD_FILE)
    if not guard_record:
        if LEGACY_GUARD_FILE.exists():
            print("Legacy caffeinate PID file ignored; no process was signaled")
        return
    target = guard_record.get("target_process")
    if not isinstance(target, dict) or any(target.get(key) != server_record.get(key) for key in ("pid", "start_time", "uid", "pgid", "boot_id", "command")):
        print("Caffeinate ownership target mismatch; guard left untouched")
        return
    guard_record, observed, error = _read_owned_record(
        GUARD_FILE, None, role=GUARD_ROLE, port=config.PORT,
    )
    if error:
        print("Caffeinate guard " + error + "; left untouched")
        return
    if observed is None:
        print("Caffeinate guard already stopped")
        return
    result = _stop_record(
        guard_record, role=GUARD_ROLE, port=config.PORT,
        command_check=lambda item: _guard_command_matches(item, server_record["pid"]),
        timeout=5,
    )
    if result != "stopped" and result != "already stopped":
        print("Caffeinate guard " + result)


def _write_pid(path, pid):
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(str(pid) + "\n", encoding="ascii")
    os.chmod(temporary, 0o600)
    temporary.replace(path)


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
        if not PID_FILE.exists() and not PID_ID_FILE.exists():
            if alive():
                raise SystemExit("Section9 listener is running without process ownership metadata; left untouched")
            print("No managed Section9 process record")
            return 0
        record, observed, error = _read_owned_record(
            PID_ID_FILE, PID_FILE, role=SERVER_ROLE, port=config.PORT,
        )
        if error:
            raise SystemExit("Refusing to stop Section9: " + error)
        if record is None:
            raise SystemExit("Refusing to stop Section9: ownership metadata is incomplete")
        payload = listener_health()
        if payload and payload.get("status") == "running":
            if payload.get("identity") != record.get("startup_identity"):
                raise SystemExit("Listener identity differs from its process record; process left untouched")
        if observed is None:
            print("Section9 process already stopped; stale ownership record left in place")
            return 0
        if not _server_command_matches(record):
            raise SystemExit("Refusing to stop Section9: executable or launch arguments do not match")
        result = _stop_record(record, role=SERVER_ROLE, port=config.PORT, command_check=_server_command_matches)
        if result not in {"stopped", "already stopped"}:
            raise SystemExit("Section9 " + result)
        _stop_guard(record)
        print("Section9 stopped")
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
        record, observed_process, error = _read_owned_record(
            PID_ID_FILE, PID_FILE, role=SERVER_ROLE, port=config.PORT,
        )
        if error or record is None or observed_process is None or record.get("startup_identity") != observed:
            raise SystemExit("Refusing existing Section9 listener without matching process ownership metadata; no process was touched")
        if not _server_command_matches(record):
            raise SystemExit("Refusing existing Section9 listener with an unexpected executable or launch command")
        print("Already running: " + url())
        return 0
    python = ROOT / ".venv" / "bin" / "python"
    with (RUNTIME / "server.log").open("ab") as log:
        child = subprocess.Popen([str(python), "-m", "uvicorn", "s9.api:app", "--host", "127.0.0.1", "--port", str(config.PORT), "--no-access-log", "--timeout-graceful-shutdown", "5"],
                                 cwd=ROOT, stdout=log, stderr=log, start_new_session=True)
    process_record = capture_process_record(
        child.pid, role=SERVER_ROLE, root=str(ROOT), port=config.PORT,
    )
    if not process_record or not _server_command_matches(process_record):
        raise SystemExit("Started server process could not be captured as the expected Section9 executable; refusing to signal it")
    process_record["startup_identity"] = expected
    write_json_record(PID_ID_FILE, process_record)
    _write_pid(PID_FILE, child.pid)
    if sys.platform == "darwin" and Path("/usr/bin/caffeinate").exists():
        # Process-scoped idle-sleep assertion, released when this server exits.
        # No system power settings are changed.
        with open(os.devnull, "wb") as null:
            guard = subprocess.Popen(["/usr/bin/caffeinate", "-i", "-w", str(child.pid)], stdout=null, stderr=null, start_new_session=True)
        guard_record = capture_process_record(
            guard.pid, role=GUARD_ROLE, root=str(ROOT), port=config.PORT,
            target_process={"identity_version": process_record["identity_version"], **{
                key: process_record[key] for key in ("pid", "start_time", "uid", "pgid", "boot_id", "command")
            }},
        )
        if guard_record and _guard_command_matches(guard_record, child.pid):
            write_json_record(GUARD_FILE, guard_record)
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
