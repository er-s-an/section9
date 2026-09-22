"""macOS Seatbelt command builder for isolated Section9 workers."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def _quote(path: Path | str) -> str:
    value = str(path).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{value}"'


def _runtime_paths(python: Path) -> tuple[Path, Path, Path, Path]:
    """Return executable realpath, stdlib, venv, and interpreter runtime root."""
    real = python.resolve()
    if python.exists():
        def ask(expression: str) -> Path:
            return Path(subprocess.check_output([str(python), "-c", expression], text=True).strip())

        stdlib = ask("import sysconfig; print(sysconfig.get_path('stdlib'))")
        venv = ask("import sys; print(sys.prefix)")
        runtime = ask("import sys; print(sys.base_prefix)")
    else:
        venv = python.parent.parent
        runtime = Path(sys.base_prefix)
        stdlib = Path(sysconfig_path())
    return real, stdlib, venv, runtime


def sysconfig_path() -> str:
    import sysconfig
    return sysconfig.get_path("stdlib")


def _profile(root: Path, python: Path, agent_port: int) -> str:
    if not 1 <= agent_port <= 65535:
        raise ValueError("agent_port must be 1..65535")
    real, stdlib, venv, runtime = _runtime_paths(python)
    workers = root / "s9" / "workers.py"
    init = root / "s9" / "__init__.py"
    # site-packages lives below the venv, while Python runtime libraries live
    # below runtime/stdlib. System paths are required for dyld and certificates.
    lines = [
        "(version 1)",
        '(import "system.sb")',
        "(deny default)",
        "(allow process-fork)",
        # Keep both forms: execvp checks the caller-provided symlink while
        # dyld ultimately executes the resolved binary.
        "(allow process-exec* (subpath " + _quote(python.parent) + "))",
        "(allow process-exec* (literal " + _quote(real) + "))",
        "(allow file-read* (subpath \"/System\"))",
        "(allow file-read* (subpath \"/usr\"))",
        "(allow file-read* (subpath \"/Library\"))",
        "(allow file-read* (subpath " + _quote(stdlib) + "))",
        "(allow file-read* (subpath " + _quote(venv) + "))",
        "(allow file-read* (subpath " + _quote(runtime) + "))",
        # Python must search the cwd for the ``s9`` package. Sensitive and
        # non-worker paths below are explicitly denied after this search rule.
        "(allow file-read* (literal " + _quote(root) + "))",
        "(allow file-read* (literal " + _quote(root / 's9') + "))",
        "(allow file-read-metadata (literal " + _quote(root) + "))",
        "(allow file-read-metadata (subpath " + _quote(root / "s9") + "))",
        "(allow file-read* (literal " + _quote(workers) + "))",
        "(allow file-read* (literal " + _quote(init) + "))",
        f'(allow network-outbound (remote tcp "localhost:{agent_port}"))',
    ]
    # Keep these explicit: a future broad runtime read must not expose project
    # credentials, operator claims, logs, memory, or infrastructure secrets.
    for forbidden in (".env", "CLAIMS.md", "data", "logs", "memory", "infra/.env", "s9/config.py", "s9/core.py", "s9/api.py", "s9/server.py"):
        lines.append("(deny file-read* (subpath " + _quote(root / forbidden) + "))")
    return "\n".join(lines) + "\n"


def worker_command(python: str, root: Path, agent_port: int, worker_args: list[str]) -> list[str]:
    """Build a sandboxed ``python -m s9.workers`` command.

    The original *python* argument is intentionally retained so its venv
    symlink and site-packages remain active. The profile separately whitelists
    its resolved executable. Non-macOS callers fail explicitly.
    """
    if sys.platform != "darwin":
        raise RuntimeError("worker sandbox requires macOS sandbox-exec")
    root = Path(root).resolve()
    interpreter = Path(python)
    profile = _profile(root, interpreter, agent_port)
    return ["/usr/bin/sandbox-exec", "-p", profile, python, "-m", "s9.workers", *worker_args]
