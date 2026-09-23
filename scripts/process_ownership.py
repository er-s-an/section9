"""Fail-closed process identity helpers for Section9's detached local services."""

from __future__ import annotations

import json
import os
import struct
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


IDENTITY_VERSION = 1
PROCESS_FIELDS = ("pid", "start_time", "uid", "pgid", "boot_id", "command")


def boot_identity() -> str:
    if sys.platform.startswith("linux"):
        try:
            return Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
        except OSError:
            pass
    if sys.platform == "darwin":
        try:
            result = subprocess.run(["/usr/sbin/sysctl", "-n", "kern.boottime"], capture_output=True,
                                    text=True, check=True, timeout=2)
            if result.stdout.strip():
                return result.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass
    return "platform-boot-identity-unavailable"


def _ps_field(pid: int, field: str) -> str | None:
    try:
        result = subprocess.run(["ps", "-ww", "-p", str(pid), "-o", field + "="], capture_output=True,
                                text=True, check=False, timeout=2)
    except (OSError, subprocess.SubprocessError):
        return None
    value = result.stdout.strip()
    return value if result.returncode == 0 and value else None


def _linux_start_identity(pid: int) -> str | None:
    try:
        stat = (Path("/proc") / str(pid) / "stat").read_text(encoding="ascii")
        stat_pid, _, _ = stat.partition(" ")
        _, _, fields = stat.rpartition(")")
        values = fields.split()
        if int(stat_pid) != pid or len(values) <= 19:
            return None
        boot_id = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
        return f"linux:{boot_id}:{values[19]}"
    except (OSError, UnicodeError, ValueError):
        return None


def _darwin_start_identity(pid: int) -> str | None:
    try:
        import ctypes

        libproc = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
        proc_pidinfo = libproc.proc_pidinfo
        proc_pidinfo.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_int]
        proc_pidinfo.restype = ctypes.c_int
        buffer = ctypes.create_string_buffer(136)
        if proc_pidinfo(pid, 3, 0, buffer, len(buffer)) != len(buffer):
            return None
        observed_pid = struct.unpack_from("=I", buffer.raw, 12)[0]
        start_seconds, start_microseconds = struct.unpack_from("=QQ", buffer.raw, 120)
        if observed_pid != pid:
            return None
        return f"darwin:{start_seconds}:{start_microseconds}"
    except (AttributeError, OSError, struct.error):
        return None


def _start_identity(pid: int) -> str | None:
    if sys.platform.startswith("linux"):
        return _linux_start_identity(pid)
    if sys.platform == "darwin":
        return _darwin_start_identity(pid)
    return None


def process_snapshot(pid: int) -> dict[str, Any] | None:
    """Return OS-observed identity, or None when the process cannot be identified."""
    try:
        pid = int(pid)
        if pid <= 0:
            return None
        ppid = _ps_field(pid, "ppid")
        pgid = _ps_field(pid, "pgid")
        uid = _ps_field(pid, "uid")
        start_time = _start_identity(pid)
        command = _ps_field(pid, "command")
        if None in (ppid, pgid, uid, start_time, command):
            return None
        if _start_identity(pid) != start_time:
            return None
        return {"pid": pid, "ppid": int(ppid), "pgid": int(pgid), "uid": int(uid),
                "start_time": start_time, "boot_id": boot_identity(), "command": command}
    except (TypeError, ValueError, OverflowError):
        return None


def capture_process_record(pid: int, *, role: str, root: str, port: int,
                           target_process: dict[str, Any] | None = None,
                           attempts: int = 20, delay: float = 0.05) -> dict[str, Any] | None:
    for _ in range(max(1, attempts)):
        observed = process_snapshot(pid)
        if observed:
            return {"identity_version": IDENTITY_VERSION, **observed, "role": role,
                    "root": str(Path(root).resolve()), "port": int(port),
                    "target_process": target_process}
        time.sleep(delay)
    return None


def process_identity_matches(record: Any, observed: dict[str, Any] | None) -> bool:
    if not isinstance(record, dict) or not isinstance(observed, dict):
        return False
    if record.get("identity_version") != IDENTITY_VERSION:
        return False
    if any(key not in record or record.get(key) in (None, "") for key in PROCESS_FIELDS):
        return False
    return all(record.get(key) == observed.get(key) for key in PROCESS_FIELDS)


def owned_process_matches(
    record: Any,
    observed: dict[str, Any] | None,
    *,
    role: str,
    root: str,
    port: int,
) -> bool:
    """Require both OS identity and the recorded Section9 ownership tuple."""
    return (
        process_identity_matches(record, observed)
        and record.get("role") == role
        and record.get("root") == str(Path(root).resolve())
        and record.get("port") == int(port)
        and record.get("pid") == record.get("pgid")
    )


def command_has_tokens(command: str, *tokens: str) -> bool:
    """Check argv tokens instead of accepting a substring in an unrelated command."""
    try:
        argv = shlex.split(command)
    except ValueError:
        return False
    return all(token in argv for token in tokens)


def target_process_matches(record: Any, observed: dict[str, Any] | None) -> bool:
    if not isinstance(record, dict):
        return False
    target = record.get("target_process")
    return process_identity_matches(target, observed)


def load_json_record(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def write_json_record(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def parse_pid_file(path: Path) -> int | None:
    try:
        raw = path.read_text(encoding="ascii").strip()
        if not raw.isdecimal():
            return None
        pid = int(raw)
        return pid if 0 < pid < 2**31 else None
    except (OSError, UnicodeError, ValueError):
        return None
