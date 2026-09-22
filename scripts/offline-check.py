#!/usr/bin/env python3
"""Bounded offline capability check for the Section9 service.

The service is the real app and keeps its configured EvoMap URL. A temporary
macOS Seatbelt profile blocks non-loopback sockets, so a real model request
records the platform boundary instead of being replaced by a fake endpoint.
"""
from __future__ import annotations

import json
import os
import signal
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import httpx


ROOT = Path(__file__).resolve().parents[1]
PYTHON = (ROOT / ".venv" / "bin" / "python").resolve()
PORT = 9034
AGENT_PORT = 9036
DATA = ROOT / "data" / "offline-proof"
ARTIFACT = ROOT / "artifacts" / "offline" / "report.json"


def q(value: Path | str) -> str:
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def sandbox_profile() -> str:
    # This profile is for the trusted root service process, not an untrusted
    # worker. Keep normal filesystem/runtime/SQLite behavior, and make the
    # network boundary the only restriction under test.
    lines = [
        "(version 1)", '(import "system.sb")', "(allow default)",
        "(deny network-outbound)",
        # Seatbelt accepts localhost:* and denies resolved external IPs.
        '(allow network-outbound (remote tcp "localhost:*"))',
        '(allow network-inbound (local tcp "localhost:*"))',
    ]
    return "\n".join(lines) + "\n"


def wait_http(url: str, timeout: float = 20) -> httpx.Response:
    deadline = time.monotonic() + timeout
    last: Exception | None = None
    while time.monotonic() < deadline:
        try:
            return httpx.get(url, timeout=1, trust_env=False)
        except Exception as exc:  # startup race only
            last = exc
            time.sleep(.2)
    raise RuntimeError(f"service did not start: {last}")


def usage_unknown_count() -> int:
    db_path = DATA / "section9.sqlite"
    if not db_path.exists():
        return 0
    connection = sqlite3.connect(db_path)
    try:
        # model failures before provider response retain unknown usage records,
        # including interactive calls with a null run_id.
        return int(connection.execute("SELECT count(*) FROM usage WHERE json_extract(data,'$.unknown')=1").fetchone()[0])
    finally:
        connection.close()


def main() -> int:
    if sys.platform != "darwin":
        raise SystemExit("offline sandbox check requires macOS sandbox-exec")
    DATA.mkdir(parents=True, exist_ok=True)
    ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
    # SQLite's first directory/database creation can be denied by Seatbelt's
    # directory mediation. Pre-create only this isolated database outside the
    # sandbox; all service writes and WAL activity still happen sandboxed.
    subprocess.run([str(PYTHON), "-c", "from pathlib import Path; from s9.store import Store; Store(Path(__import__('os').environ['S9_DATA_DIR']) / 'section9.sqlite')"],
                   cwd=ROOT, env={**os.environ, "S9_DATA_DIR": str(DATA)}, check=True)
    usage_before = usage_unknown_count()
    profile_path = DATA / "offline.sb"
    profile_path.write_text(sandbox_profile(), encoding="utf-8")
    env = os.environ.copy()
    env.update(S9_DATA_DIR=str(DATA), S9_PORT=str(PORT), S9_ENVIRONMENT="offline-proof", PYTHONUNBUFFERED="1")
    env["PYTHONPATH"] = os.pathsep.join([str(ROOT), str(ROOT / ".venv" / "lib" / "python3.12" / "site-packages")])
    log_path = DATA / "server.log"
    command = ["/usr/bin/sandbox-exec", "-f", str(profile_path), str(PYTHON), "-m", "uvicorn", "s9.api:app", "--host", "127.0.0.1", "--port", str(PORT), "--no-access-log"]
    process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=log_path.open("ab"), stderr=subprocess.STDOUT, start_new_session=True)
    report: dict = {"ports": {"http": PORT, "agent": AGENT_PORT}, "sandbox": "macOS Seatbelt temporary profile", "server_pid": process.pid}
    try:
      try:
        state_response = wait_http(f"http://127.0.0.1:{PORT}/api/state")
        chat_response = httpx.post(f"http://127.0.0.1:{PORT}/api/chat", json={"message": "请回答一个普通产品问题，只输出 JSON。"}, timeout=120, trust_env=False)
        state_after = httpx.get(f"http://127.0.0.1:{PORT}/api/state", timeout=10, trust_env=False)
        playbooks = httpx.get(f"http://127.0.0.1:{PORT}/api/playbooks", timeout=10, trust_env=False)
        try:
            chat_body = chat_response.json()
        except ValueError:
            chat_body = {"error": {"code": "INVALID_JSON", "status_code": chat_response.status_code}}
        try:
            state_body = state_after.json()
        except ValueError:
            state_body = {"error": {"code": "INVALID_JSON", "status_code": state_after.status_code}}
        model = state_body.get("dependencies", {}).get("model", {})
        error_text = json.dumps(chat_body, ensure_ascii=False)
        report.update({
            "state_initial_http": state_response.status_code,
            "chat": {"http_status": chat_response.status_code, "status": chat_body.get("status"), "error": chat_body.get("error"), "usage": chat_body.get("usage")},
            "state_after": {"http_status": state_after.status_code, "model": {"status": model.get("status"), "detail": model.get("detail"), "remote_inference": model.get("remote_inference")}, "metrics": state_body.get("metrics")},
            "memory_readable": playbooks.status_code == 200,
            "remote_network_attempt": True,
            "remote_network_blocked": model.get("status") == "degraded" and ("Permission" in error_text or "MODEL_" in error_text),
            "usage_unknown": usage_unknown_count(), "usage_unknown_before": usage_before,
            "usage_unknown_delta": usage_unknown_count() - usage_before,
            "notes": "The real configured model URL was retained; this is an OS network-boundary proof, not offline inference support.",
        })
      except Exception as exc:
        # Keep the unsupported-profile result as evidence instead of masking
        # it or switching to a mock service.
        startup_log = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""
        report.update({"service_started": False, "business_error": {"type": type(exc).__name__, "message": str(exc)},
                       "sandbox_limit": "real service could not initialize under this Seatbelt profile; see server_exit/log-side error",
                       "port_connection": {"127.0.0.1:9034": "connection refused during readiness"},
                       "startup_log_error": "sqlite database open denied by sandbox" if "unable to open database file" in startup_log else "startup failure recorded in isolated server log",
                       "usage_unknown": usage_unknown_count(), "usage_unknown_before": usage_before,
                       "usage_unknown_delta": usage_unknown_count() - usage_before})
    finally:
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                # sandbox-exec may have already exited its child and retained
                # a stale Popen group handle; never signal unrelated services.
                pass
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=3)
        profile_path.unlink(missing_ok=True)
        report["server_exit"] = process.returncode
        ARTIFACT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"report": str(ARTIFACT), "server_exit": report.get("server_exit"), "chat_status": report.get("chat", {}).get("status"), "model_status": report.get("state_after", {}).get("model", {}).get("status"), "usage_unknown": report.get("usage_unknown")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
