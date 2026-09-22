#!/usr/bin/env python3
"""Prove copied Section9 history is readable without a model key.

This is a same-Mac copied-history test, not a fresh-Mac installation test and
not offline inference. It never contacts a provider: the isolated process is
also constrained by macOS Seatbelt to loopback networking only.
"""
from __future__ import annotations

import json
import os
import signal
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_DB = ROOT / "data" / "section9.sqlite"
HTTP_PORT, AGENT_PORT = 9055, 9057
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def sandbox_profile() -> str:
    return "\n".join([
        "(version 1)", '(import "system.sb")', "(allow default)",
        "(deny network-outbound)", '(allow network-outbound (remote tcp "localhost:*"))',
        '(allow network-inbound (local tcp "localhost:*"))',
    ]) + "\n"


def backup_database(destination: Path) -> None:
    source = sqlite3.connect(f"file:{SOURCE_DB}?mode=ro", uri=True)
    try:
        target = sqlite3.connect(destination)
        try:
            source.backup(target)
            target.commit()
        finally:
            target.close()
    finally:
        source.close()


def get_json(url: str, timeout: float = 3) -> tuple[int, dict]:
    try:
        with OPENER.open(url, timeout=timeout) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", "replace")
        try: payload = json.loads(body)
        except ValueError: payload = {"raw": body[:500]}
        return error.code, payload


def wait_health(base: str, process: subprocess.Popen, timeout: float = 20) -> tuple[int, dict]:
    deadline = time.monotonic() + timeout
    last: Exception | None = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"isolated service exited with {process.returncode}")
        try:
            return get_json(base + "/api/health", 1)
        except Exception as error:
            last = error
            time.sleep(.2)
    raise RuntimeError(f"isolated service did not become healthy: {last}")


def usage_snapshot(path: Path) -> dict[str, int]:
    db = sqlite3.connect(path)
    try:
        rows = [json.loads(row[0]) for row in db.execute("SELECT data FROM usage").fetchall()]
        return {"records": len(rows), "reserved": sum(row.get("status") == "reserved" for row in rows),
                "unknown": sum(bool(row.get("unknown")) for row in rows)}
    finally:
        db.close()


def main() -> int:
    if sys.platform != "darwin":
        raise SystemExit("offline history proof requires macOS sandbox-exec")
    if not SOURCE_DB.is_file():
        raise SystemExit(f"missing existing primary database: {SOURCE_DB}")
    for port in (HTTP_PORT, AGENT_PORT):
        with socket.socket() as probe:
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                raise SystemExit(f"isolated port {port} is occupied; existing listener left untouched")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    data_dir = ROOT / "data" / f"product-offline-history-{stamp}"
    artifact = ROOT / "artifacts" / "product-stage1" / "offline-history" / f"report-{stamp}.json"
    data_dir.mkdir(parents=True, exist_ok=False)
    artifact.parent.mkdir(parents=True, exist_ok=True)
    report: dict = {"proof": "same_mac_copied_history_no_key", "ports": {"http": HTTP_PORT, "agent": AGENT_PORT},
                    "source_db": "data/section9.sqlite", "remote_provider_called": False,
                    "offline_inference_supported": False, "sandbox": "macOS Seatbelt loopback-only",
                    "isolated_data": str(data_dir.relative_to(ROOT))}
    process: subprocess.Popen | None = None
    profile = data_dir / "offline.sb"
    log = data_dir / "server.log"
    try:
        backup_database(data_dir / "section9.sqlite")
        before = usage_snapshot(data_dir / "section9.sqlite")
        profile.write_text(sandbox_profile(), encoding="utf-8")
        env = os.environ.copy()
        env.update({"S9_DATA_DIR": str(data_dir), "S9_PORT": str(HTTP_PORT), "EVOMAP_MODEL_API_KEY": "",
                    "S9_ENVIRONMENT": "product-offline-history", "PYTHONUNBUFFERED": "1",
                    "NO_PROXY": "127.0.0.1,localhost"})
        env["PYTHONPATH"] = str(ROOT)
        command = ["/usr/bin/sandbox-exec", "-f", str(profile), str(ROOT / ".venv/bin/python"), "-m", "uvicorn",
                   "s9.api:app", "--host", "127.0.0.1", "--port", str(HTTP_PORT), "--no-access-log"]
        process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=log.open("ab"), stderr=subprocess.STDOUT, start_new_session=True)
        base = f"http://127.0.0.1:{HTTP_PORT}"
        health_status, health = wait_health(base, process)
        report["identity"] = health.get("identity")
        runs_status, runs = get_json(base + "/api/runs")
        items = runs.get("items", []) if isinstance(runs, dict) else []
        old = items[-1] if items else None
        detail_status, detail = get_json(base + "/api/runs/" + str(old.get("id"))) if old and old.get("id") else (0, {})
        state_status, state = get_json(base + "/api/state")
        # One explicit no-key request; urllib's HTTPError is normalized above.
        chat_status, chat = 0, {}
        request = urllib.request.Request(base + "/api/chat", data=json.dumps({"message": "只返回 JSON"}).encode(), headers={"Content-Type": "application/json"})
        try:
            with OPENER.open(request, timeout=15) as response:
                chat_status, chat = response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            chat_status = error.code
            try: chat = json.loads(error.read())
            except ValueError: chat = {"error": {"message": "unparseable"}}
        after = usage_snapshot(data_dir / "section9.sqlite")
        model = (state.get("dependencies") or {}).get("model") if isinstance(state, dict) else {}
        error = chat.get("error") if isinstance(chat, dict) else {}
        report.update({"health": {"http": health_status, "identity_present": isinstance(health.get("identity"), dict)},
                       "history": {"http": runs_status, "count": len(items), "terminal_count": sum(item.get("status") in {"resolved", "failed", "cancelled"} for item in items),
                                   "old_run_readable": detail_status == 200, "old_run_id_present": bool(old and old.get("id"))},
                       "state": {"http": state_status, "model_status": model.get("status"), "remote_inference": model.get("remote_inference")},
                       "model_attempt": {"http": chat_status, "error_code": error.get("code") if isinstance(error, dict) else None,
                                         "model_unconfigured": "MODEL_UNCONFIGURED" in json.dumps(chat, ensure_ascii=False),
                                         "usage_records_before": before["records"], "usage_records_after": after["records"],
                                         "reserved_before": before["reserved"], "reserved_after": after["reserved"]},
                       "historical_records_readable": runs_status == 200 and detail_status == 200,
                       "model_degraded": model.get("status") in {"degraded", "unconfigured"},
                       "notes": "Same-Mac copied-history proof only; fresh Mac and offline inference remain unverified."})
        report["passed"] = (report["historical_records_readable"] and report["model_degraded"]
                            and report["model_attempt"]["model_unconfigured"]
                            and before["records"] == after["records"] and after["reserved"] == 0)
    except Exception as exc:
        report.update({"passed": False, "failure": {"type": type(exc).__name__, "message": str(exc)}})
    finally:
        if process is not None and process.poll() is None:
            try: os.killpg(process.pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError): pass
        if process is not None:
            try: process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try: os.killpg(process.pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError): pass
                process.wait(timeout=3)
            report["server_exit"] = process.returncode
        profile.unlink(missing_ok=True)
        artifact.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"report": str(artifact), "historical_records_readable": report.get("historical_records_readable"),
                      "model_unconfigured": report.get("model_attempt", {}).get("model_unconfigured"),
                      "server_exit": report.get("server_exit")}, ensure_ascii=False))
    return 0 if report.get("passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
