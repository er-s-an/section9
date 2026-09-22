"""Owned local runtime for a declared external project.

Only the manifest's fixed, argv-style commands are allowed.  The runtime never
accepts a shell string, never receives Section9's worker token, and keeps its
pid/log/data paths inside the declared candidate checkout/runtime boundary.
"""

from __future__ import annotations

import os
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from s9.connectors.http_app import ConnectorConfig, ExternalHTTPConnector, SourceBinding


class RuntimeErrorState(RuntimeError):
    pass


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
        python = self.root / ".venv" / "bin" / "uvicorn"
        if not python.exists():
            raise RuntimeErrorState("external runtime dependencies are not installed")
        return [str(python), "api:app", "--host", "127.0.0.1", "--port", str(self.port)]

    def _pid_from_file(self) -> int | None:
        try:
            return int(self.pid_path.read_text(encoding="utf-8").strip())
        except (FileNotFoundError, ValueError, OSError):
            return None

    def _owned_pid(self, pid: int) -> bool:
        """Verify a persisted pid still belongs to this declared runtime.

        The service can be restarted independently of the external process, so
        a pid file alone is never enough authority to send a signal.
        """
        try:
            command = subprocess.check_output(
                ["ps", "-p", str(pid), "-o", "command="], text=True, stderr=subprocess.DEVNULL
            ).strip()
        except (OSError, subprocess.CalledProcessError):
            return False
        return str(self.root / ".venv") in command and "api:app" in command and f"--port {self.port}" in command

    def status(self) -> dict[str, Any]:
        if self.process and self.process.poll() is None:
            return {"status": "running", "pid": self.process.pid, "port": self.port, "owned": True}
        pid = self._pid_from_file()
        if pid is not None and self._owned_pid(pid):
            return {"status": "running", "pid": pid, "port": self.port, "owned": True, "recovered": True}
        if pid is not None:
            self.pid_path.unlink(missing_ok=True)
        return {"status": "stopped", "pid": None, "port": self.port, "owned": False}

    def start(self, *, model_env: dict[str, str] | None = None, require_clean: bool = True) -> dict[str, Any]:
        if self.process and self.process.poll() is None:
            return {"status": "running", "pid": self.process.pid, "port": self.port}
        persisted = self._pid_from_file()
        if persisted is not None and self._owned_pid(persisted):
            return {"status": "running", "pid": persisted, "port": self.port, "recovered": True}
        self.pid_path.unlink(missing_ok=True)
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
        self.pid_path.write_text(str(self.process.pid), encoding="utf-8")
        return {"status": "starting", "pid": self.process.pid, "port": self.port, "command": self._command()}

    def stop(self, timeout: float = 8.0) -> dict[str, Any]:
        process = self.process
        if process is None and self.pid_path.exists():
            pid = self._pid_from_file()
            if pid is None or not self._owned_pid(pid):
                self.pid_path.unlink(missing_ok=True)
                return {"status": "stopped", "owned": False, "stale_pid": pid}
            try:
                os.kill(pid, signal.SIGTERM)
                deadline = time.monotonic() + timeout
                while time.monotonic() < deadline:
                    try:
                        os.kill(pid, 0)
                    except ProcessLookupError:
                        break
                    time.sleep(0.1)
                else:
                    os.kill(pid, signal.SIGKILL)
            except (OSError, ProcessLookupError):
                pass
            self.pid_path.unlink(missing_ok=True)
            return {"status": "stopped", "owned": True}
        if process is None:
            return {"status": "stopped", "owned": False}
        if process.poll() is None:
            process.send_signal(signal.SIGTERM)
            deadline = time.monotonic() + timeout
            while process.poll() is None and time.monotonic() < deadline:
                time.sleep(0.1)
            if process.poll() is None:
                process.kill()
                process.wait(timeout=2)
        self.process = None
        self.pid_path.unlink(missing_ok=True)
        return {"status": "stopped", "owned": True}

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
