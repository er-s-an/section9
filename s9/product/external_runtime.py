"""Owned local runtime for a declared external project.

Only the manifest's fixed, argv-style commands are allowed.  The runtime never
accepts a shell string, never receives Section9's worker token, and keeps its
pid/log/data paths inside the declared candidate checkout/runtime boundary.
"""

from __future__ import annotations

import json
import os
import signal
import struct
import subprocess
import sys
import tempfile
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from s9.connectors.http_app import ConnectorConfig, ExternalHTTPConnector, SourceBinding


class RuntimeErrorState(RuntimeError):
    pass


@dataclass(frozen=True)
class _ProcessSnapshot:
    pid: int
    start_identity: str
    executable: str
    argv: tuple[str, ...]
    cwd: str


@dataclass
class ExternalRuntime:
    root: Path
    expected_commit: str
    port: int
    runtime_dir: Path
    log_path: Path
    data_path: Path
    process: subprocess.Popen[bytes] | None = None

    @property
    def pid_path(self) -> Path:
        return self.runtime_dir / "support-agent.pid"

    def git_state(self) -> dict[str, Any]:
        def run(*args: str) -> str:
            return subprocess.check_output(["git", *args], cwd=self.root, text=True, stderr=subprocess.STDOUT).strip()

        head = run("rev-parse", "HEAD")
        dirty = bool(run("status", "--porcelain"))
        return {"commit": head, "expected_commit": self.expected_commit, "clean": not dirty,
                "matches_expected": head == self.expected_commit, "dirty": dirty}

    def source_binding(self, project_id: str, environment_id: str) -> SourceBinding:
        state = self.git_state()
        return SourceBinding(project=project_id, environment=environment_id, source=str(self.root), commit=state["commit"])

    def _command(self) -> list[str]:
        python = self.root / ".venv" / "bin" / "python"
        uvicorn = self.root / ".venv" / "bin" / "uvicorn"
        if not python.exists() or not uvicorn.exists():
            raise RuntimeErrorState("external runtime dependencies are not installed")
        return [str(python), "-m", "uvicorn", "api:app", "--host", "127.0.0.1", "--port", str(self.port)]

    def _expected_process(self) -> tuple[str, tuple[str, ...], str]:
        command = self._command()
        return str(Path(command[0]).resolve()), tuple(command), str(self.root.resolve())

    def _linux_process_snapshot(self, pid: int) -> _ProcessSnapshot | None:
        try:
            proc = Path("/proc") / str(pid)
            stat = (proc / "stat").read_text(encoding="ascii")
            # comm is parenthesized and may itself contain spaces or ')'.
            stat_pid, _, _ = stat.partition(" ")
            _, _, fields = stat.rpartition(")")
            values = fields.split()
            if int(stat_pid) != pid:
                return None
            start_ticks = values[19]  # field 22 (starttime), after field 2 (comm)
            boot_id = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
            executable = os.path.realpath(proc / "exe")
            raw_argv = (proc / "cmdline").read_bytes().split(b"\0")
            argv = tuple(os.fsdecode(part) for part in raw_argv if part)
            cwd = os.path.realpath(proc / "cwd")
            stat_after = (proc / "stat").read_text(encoding="ascii")
            stat_after_pid, _, _ = stat_after.partition(" ")
            _, _, fields_after = stat_after.rpartition(")")
            values_after = fields_after.split()
            if int(stat_after_pid) != pid or values_after[19] != start_ticks:
                return None
        except (OSError, IndexError, UnicodeError):
            return None
        except ValueError:
            return None
        if not argv:
            return None
        return _ProcessSnapshot(pid, f"linux:{boot_id}:{start_ticks}", executable, argv, cwd)

    @staticmethod
    def _darwin_proc_pidinfo(pid: int, flavor: int, size: int) -> bytes | None:
        try:
            import ctypes

            libproc = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
            proc_pidinfo = libproc.proc_pidinfo
            proc_pidinfo.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_int]
            proc_pidinfo.restype = ctypes.c_int
            buffer = ctypes.create_string_buffer(size)
            result = proc_pidinfo(pid, flavor, 0, buffer, size)
        except (AttributeError, OSError):
            return None
        return buffer.raw[:result] if result == size else None

    def _darwin_process_snapshot(self, pid: int) -> _ProcessSnapshot | None:
        """Read exact argv, executable, cwd, and microsecond process start time via libproc/sysctl."""
        try:
            import ctypes

            # PROC_PIDTBSDINFO / PROC_PIDVNODEPATHINFO from libproc.h.
            bsd = self._darwin_proc_pidinfo(pid, 3, 136)
            # vinfo_stat is 136 bytes, followed by the vnode type/padding and
            # fsid (16 bytes total); MAXPATHLEN is 1024 on supported macOS.
            vnode_path_offset = 152
            vnode_path_size = vnode_path_offset + 1024
            vnode_info_path_size = vnode_path_size
            vnode = self._darwin_proc_pidinfo(pid, 9, vnode_info_path_size * 2)
            if bsd is None or vnode is None:
                return None
            if struct.unpack_from("=I", bsd, 12)[0] != pid:
                return None
            start_sec, start_usec = struct.unpack_from("=QQ", bsd, 120)
            executable_buffer = ctypes.create_string_buffer(4096)
            libproc = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
            proc_pidpath = libproc.proc_pidpath
            proc_pidpath.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
            proc_pidpath.restype = ctypes.c_int
            executable_size = proc_pidpath(pid, executable_buffer, len(executable_buffer))
            if executable_size <= 0:
                return None
            executable = os.fsdecode(executable_buffer.value)

            libc = ctypes.CDLL(None, use_errno=True)
            sysctl = libc.sysctl
            sysctl.argtypes = [ctypes.POINTER(ctypes.c_int), ctypes.c_uint, ctypes.c_void_p,
                               ctypes.POINTER(ctypes.c_size_t), ctypes.c_void_p, ctypes.c_size_t]
            sysctl.restype = ctypes.c_int
            mib = (ctypes.c_int * 3)(1, 49, pid)  # CTL_KERN, KERN_PROCARGS2, pid
            length = ctypes.c_size_t(0)
            if sysctl(mib, 3, None, ctypes.byref(length), None, 0) != 0 or length.value < 5:
                return None
            arguments = ctypes.create_string_buffer(length.value)
            if sysctl(mib, 3, arguments, ctypes.byref(length), None, 0) != 0:
                return None
            raw = arguments.raw[:length.value]
            argc = struct.unpack_from("=i", raw, 0)[0]
            if argc <= 0 or argc > 4096:
                return None
            position = raw.find(b"\0", 4)
            if position < 0:
                return None
            position += 1
            while position < len(raw) and raw[position] == 0:
                position += 1
            argv: list[str] = []
            for _ in range(argc):
                end = raw.find(b"\0", position)
                if end < 0:
                    return None
                argv.append(os.fsdecode(raw[position:end]))
                position = end + 1

            # proc_vnodepathinfo starts with pvi_cdir, a vnode_info plus vip_path.
            cwd_raw = vnode[vnode_path_offset:vnode_path_size].split(b"\0", 1)[0]
            cwd = os.fsdecode(cwd_raw)
            bsd_after = self._darwin_proc_pidinfo(pid, 3, 136)
            if bsd_after is None or struct.unpack_from("=I", bsd_after, 12)[0] != pid:
                return None
            if struct.unpack_from("=QQ", bsd_after, 120) != (start_sec, start_usec):
                return None
        except (AttributeError, OSError, ValueError, struct.error, UnicodeError):
            return None
        if not argv or not executable or not cwd:
            return None
        return _ProcessSnapshot(pid, f"darwin:{start_sec}:{start_usec}", executable, tuple(argv), cwd)

    def _process_snapshot(self, pid: int) -> _ProcessSnapshot | None:
        if sys.platform.startswith("linux"):
            return self._linux_process_snapshot(pid)
        if sys.platform == "darwin":
            return self._darwin_process_snapshot(pid)
        return None

    @staticmethod
    def _record_keys() -> set[str]:
        return {"version", "pid", "start_identity", "executable", "argv", "cwd", "root", "port"}

    def _read_pid_record(self) -> dict[str, Any] | None:
        def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("duplicate PID record key")
                result[key] = value
            return result

        try:
            record = json.loads(self.pid_path.read_text(encoding="utf-8"), object_pairs_hook=unique_object)
        except (FileNotFoundError, OSError, UnicodeError, ValueError, json.JSONDecodeError):
            return None
        if not isinstance(record, dict) or set(record) != self._record_keys():
            return None
        if (type(record["version"]) is not int or record["version"] != 1
                or type(record["pid"]) is not int or record["pid"] <= 0
                or type(record["port"]) is not int
                or not isinstance(record["start_identity"], str) or not record["start_identity"]
                or not isinstance(record["executable"], str) or not record["executable"]
                or not isinstance(record["cwd"], str) or not record["cwd"]
                or not isinstance(record["root"], str) or not record["root"]
                or not isinstance(record["argv"], list)
                or any(not isinstance(arg, str) for arg in record["argv"])):
            return None
        return record

    def _record_for_snapshot(self, snapshot: _ProcessSnapshot) -> dict[str, Any]:
        return {
            "version": 1,
            "pid": snapshot.pid,
            "start_identity": snapshot.start_identity,
            "executable": snapshot.executable,
            "argv": list(snapshot.argv),
            "cwd": snapshot.cwd,
            "root": str(self.root.resolve()),
            "port": self.port,
        }

    def _record_matches(self, record: Mapping[str, Any]) -> bool:
        try:
            expected_executable, expected_argv, expected_root = self._expected_process()
        except (OSError, RuntimeErrorState):
            return False
        if (record.get("root") != expected_root or record.get("port") != self.port
                or record.get("executable") != expected_executable
                or record.get("argv") != list(expected_argv) or record.get("cwd") != expected_root):
            return False
        snapshot = self._process_snapshot(record["pid"])
        return snapshot is not None and self._record_for_snapshot(snapshot) == dict(record)

    def _write_pid_record(self, record: Mapping[str, Any]) -> None:
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        fd, temporary_name = tempfile.mkstemp(prefix=f"{self.pid_path.name}.", dir=self.runtime_dir)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as temporary:
                json.dump(dict(record), temporary, sort_keys=True, separators=(",", ":"))
                temporary.write("\n")
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_name, self.pid_path)
        finally:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass

    def _unlink_matching_record(self, record: Mapping[str, Any]) -> None:
        if self._read_pid_record() == dict(record):
            self.pid_path.unlink(missing_ok=True)

    def _send_signal_if_owned(self, record: Mapping[str, Any], signum: int) -> bool:
        """Signal only after rechecking the persisted identity against a live snapshot.

        Linux pidfds bind the signal to the process instance, closing the PID reuse
        race between the final snapshot and signal delivery. Other supported systems
        recheck immediately before os.kill and fail closed when process data is absent.
        """
        pid = record["pid"]
        if not self._record_matches(record):
            return False
        pidfd_open = getattr(os, "pidfd_open", None)
        pidfd_send_signal = getattr(signal, "pidfd_send_signal", None)
        if pidfd_open is not None and pidfd_send_signal is not None:
            try:
                pidfd = pidfd_open(pid, 0)
            except OSError:
                return False
            try:
                if not self._record_matches(record):
                    return False
                pidfd_send_signal(pidfd, signum, None, 0)
                return True
            except (OSError, ProcessLookupError):
                return False
            finally:
                os.close(pidfd)
        if not self._record_matches(record):
            return False
        try:
            os.kill(pid, signum)
            return True
        except (OSError, ProcessLookupError):
            return False

    def _wait_for_record_exit(self, record: Mapping[str, Any], deadline: float) -> bool:
        while time.monotonic() < deadline:
            if not self._record_matches(record):
                return True
            time.sleep(0.1)
        return not self._record_matches(record)

    def status(self) -> dict[str, Any]:
        process = self.process
        if process is not None and process.poll() is None:
            record = self._read_pid_record()
            if record is not None and record["pid"] == process.pid and self._record_matches(record):
                return {"status": "running", "pid": process.pid, "port": self.port, "owned": True}
            return {"status": "unknown", "pid": process.pid, "port": self.port, "owned": False}
        record = self._read_pid_record()
        if record is not None and self._record_matches(record):
            return {"status": "running", "pid": record["pid"], "port": self.port,
                    "owned": True, "recovered": True}
        return {"status": "stopped", "pid": None, "port": self.port, "owned": False}

    def start(self, *, model_env: dict[str, str] | None = None, require_clean: bool = True) -> dict[str, Any]:
        if self.process and self.process.poll() is None:
            persisted = self._read_pid_record()
            if persisted is not None and persisted["pid"] == self.process.pid and self._record_matches(persisted):
                return {"status": "running", "pid": self.process.pid, "port": self.port}
            raise RuntimeErrorState("external runtime process ownership cannot be verified")
        persisted = self._read_pid_record()
        if persisted is not None and self._record_matches(persisted):
            return {"status": "running", "pid": persisted["pid"], "port": self.port, "recovered": True}
        state = self.git_state()
        if not state["matches_expected"]:
            raise RuntimeErrorState("external source binding does not match the pinned commit")
        if require_clean and not state["clean"]:
            raise RuntimeErrorState("external checkout is dirty; use a separately recorded adapter workspace")
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.data_path.parent.mkdir(parents=True, exist_ok=True)
        env = {"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", ""),
               "PYTHONUNBUFFERED": "1", "PYTHONNOUSERSITE": "1", "SUPPORT_AGENT_DB_URL": f"sqlite:///{self.data_path}"}
        if model_env:
            env.update(model_env)
        with self.log_path.open("ab") as log:
            self.process = subprocess.Popen(self._command(), cwd=self.root, env=env, stdout=log, stderr=subprocess.STDOUT,
                                            start_new_session=True)
        expected_executable, expected_argv, expected_root = self._expected_process()
        snapshot: _ProcessSnapshot | None = None
        for _ in range(40):
            snapshot = self._process_snapshot(self.process.pid)
            if (snapshot is not None and snapshot.executable == expected_executable
                    and snapshot.argv == expected_argv and snapshot.cwd == expected_root):
                break
            if self.process.poll() is not None:
                break
            time.sleep(0.05)
        if (snapshot is None or snapshot.executable != expected_executable
                or snapshot.argv != expected_argv or snapshot.cwd != expected_root):
            # The child was created by this Popen handle, but no durable identity
            # could be established. Keep the handle for inspection; never write a
            # PID-only record that could authorize later recovery.
            raise RuntimeErrorState("external runtime process identity could not be established")
        self._write_pid_record(self._record_for_snapshot(snapshot))
        return {"status": "starting", "pid": self.process.pid, "port": self.port, "command": self._command()}

    def stop(self, timeout: float = 8.0) -> dict[str, Any]:
        record = self._read_pid_record()
        process = self.process
        if record is None:
            return {"status": "stopped", "owned": False}
        if process is not None and record["pid"] != process.pid:
            return {"status": "unknown", "owned": False, "stale_pid": record["pid"]}
        if process is not None and process.poll() is not None:
            self.process = None
            self._unlink_matching_record(record)
            return {"status": "stopped", "owned": True}
        if not self._record_matches(record):
            return {"status": "stopped", "owned": False, "stale_pid": record["pid"]}
        if not self._send_signal_if_owned(record, signal.SIGTERM):
            return {"status": "unknown", "owned": False, "stale_pid": record["pid"]}

        deadline = time.monotonic() + timeout
        if not self._wait_for_record_exit(record, deadline):
            # This helper revalidates the exact start identity and uses a pidfd
            # where available, so a reused PID is never escalated to SIGKILL.
            self._send_signal_if_owned(record, signal.SIGKILL)
            self._wait_for_record_exit(record, time.monotonic() + 2.0)

        if process is not None and process.poll() is not None:
            self.process = None
        if not self._record_matches(record):
            self._unlink_matching_record(record)
            return {"status": "stopped", "owned": True}
        return {"status": "unknown", "owned": True, "pid": record["pid"]}

    async def probe(self, project_id: str, environment_id: str, *, base_url: str | None = None) -> list[dict[str, Any]]:
        source = self.source_binding(project_id, environment_id)
        cfg = ConnectorConfig(base_url or f"http://127.0.0.1:{self.port}", project_id, environment_id, source,
                              timeout_seconds=8.0)
        async with ExternalHTTPConnector(cfg) as connector:
            return [await connector.health(), await connector.readiness()]

    def run_tests(self, timeout: float = 180.0) -> dict[str, Any]:
        command = [str(self.root / ".venv" / "bin" / "python"), "-m", "pytest", "-q"]
        started = time.monotonic()
        try:
            result = subprocess.run(command, cwd=self.root, env={"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", ""), "PYTHONNOUSERSITE": "1"},
                                    capture_output=True, text=True, timeout=timeout, check=False)
        except subprocess.TimeoutExpired as exc:
            return {"status": "unknown", "timeout": True, "elapsed_s": time.monotonic() - started,
                    "stdout": (exc.stdout or "")[-4000:], "stderr": (exc.stderr or "")[-4000:]}
        return {"status": "passed" if result.returncode == 0 else "failed", "returncode": result.returncode,
                "elapsed_s": round(time.monotonic() - started, 3), "stdout": result.stdout[-8000:], "stderr": result.stderr[-4000:]}


def runtime_from_manifest(manifest: dict[str, Any], root: Path, runtime_dir: Path, data_path: Path,
                          log_path: Path) -> ExternalRuntime:
    return ExternalRuntime(root=root, expected_commit=str(manifest["commit"]), port=int(manifest["port"]),
                           runtime_dir=runtime_dir, log_path=log_path, data_path=data_path)
