#!/usr/bin/env python3
"""Probe the proposed macOS sandbox boundary for Section9 workers.

This is a diagnostic, not the worker launcher. It creates a temporary seatbelt
profile, runs short no-secret probes, prints actual exit codes/stderr, and
removes the profile. It never changes global macOS policy.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKERS = ROOT / "s9" / "workers.py"
VENV_PYTHON = ROOT / ".venv" / "bin" / "python"


def python_paths() -> tuple[Path, Path, Path, Path, Path]:
    python = VENV_PYTHON if VENV_PYTHON.exists() else Path(sys.executable)
    resolved = python.resolve()
    prefix = Path(subprocess.check_output([str(python), "-c", "import sys; print(sys.prefix)"], text=True).strip())
    base_prefix = Path(subprocess.check_output([str(python), "-c", "import sys; print(sys.base_prefix)"], text=True).strip())
    libdir = Path(subprocess.check_output([str(python), "-c", "import sysconfig; print(sysconfig.get_config_var('LIBDIR'))"], text=True).strip())
    return python, resolved, prefix, base_prefix, libdir


def atom(path: Path) -> str:
    # Seatbelt string literals use backslash quoting.
    return '"' + str(path).replace("\\", "\\\\").replace('"', '\\"') + '"'


def profile() -> str:
    python, resolved, prefix, base_prefix, libdir = python_paths()
    stdlib = Path(subprocess.check_output([str(python), "-c", "import sysconfig; print(sysconfig.get_path('stdlib'))"], text=True).strip())
    # CPython on macOS may load the standard library from its framework path;
    # this path is only a read allow, while workspace secrets stay denied.
    lines = [
        "(version 1)",
        # system.sb supplies the macOS runtime's required process bookkeeping;
        # the deny below then narrows file/network access again.
        '(import "system.sb")',
        "(deny default)",
        "(allow process-fork)",
        "(allow process-exec* (literal " + atom(resolved) + "))",
        "(allow file-read* (subpath \"/System\"))",
        "(allow file-read* (subpath \"/usr\"))",
        "(allow file-read* (subpath \"/Library\"))",
        "(allow file-read* (subpath " + atom(stdlib) + "))",
        "(allow file-read* (subpath " + atom(prefix) + "))",
        "(allow file-read* (subpath " + atom(base_prefix) + "))",
        "(allow file-read* (subpath " + atom(libdir) + "))",
        "(allow file-read* (literal " + atom(ROOT / "s9" / "__init__.py") + "))",
        "(allow file-read* (literal " + atom(WORKERS) + "))",
        # The API port is intentionally separate from the normal 9019 server.
        "(allow network-outbound (remote tcp \"localhost:9021\"))",
    ]
    # Explicit denies document the security intent and protect against a
    # future broad read rule accidentally exposing these workspace paths.
    for forbidden in (ROOT / ".env", ROOT / "CLAIMS.md", ROOT / "data", ROOT / "logs", ROOT / "memory"):
        lines.append("(deny file-read* (subpath " + atom(forbidden) + "))")
    return "\n".join(lines) + "\n"


def run_probe(profile_path: Path, label: str, code: str, *, env: dict[str, str]) -> None:
    python = (VENV_PYTHON if VENV_PYTHON.exists() else Path(sys.executable)).resolve()
    command = ["/usr/bin/sandbox-exec", "-f", str(profile_path), str(python), "-I", "-c", code]
    result = subprocess.run(command, cwd=ROOT, env=env, text=True, capture_output=True, timeout=10)
    stderr = result.stderr.strip().replace("\n", " | ")
    stdout = result.stdout.strip().replace("\n", " | ")
    print(f"{label}: exit={result.returncode} stdout={stdout!r} stderr={stderr!r}")


def main() -> int:
    if sys.platform != "darwin":
        print("sandbox-exec probe unavailable: this host is not macOS", file=sys.stderr)
        return 2
    if not Path("/usr/bin/sandbox-exec").exists():
        print("sandbox-exec probe unavailable: /usr/bin/sandbox-exec missing", file=sys.stderr)
        return 2
    env = {"PATH": "/usr/bin:/bin", "PYTHONNOUSERSITE": "1"}
    with tempfile.TemporaryDirectory(prefix="s9-sandbox-") as directory:
        profile_path = Path(directory) / "worker.sb"
        profile_path.write_text(profile(), encoding="utf-8")
        print(f"profile={profile_path} (temporary; removed on exit)")
        run_probe(profile_path, "allowed workers.py read", f"open({WORKERS.as_posix()!r}).read(1); print('allowed')", env=env)
        run_probe(profile_path, "denied .env read", f"open({(ROOT / '.env').as_posix()!r}).read(1); print('unexpected')", env=env)
        run_probe(profile_path, "denied command execution", "import subprocess; subprocess.run(['/bin/sh', '-c', 'true'], check=True)", env=env)
        run_probe(profile_path, "localhost:9021 network boundary", "import socket; s=socket.create_connection(('127.0.0.1',9021),1); s.close(); print('connected')", env=env)
        run_probe(profile_path, "other-port network denied", "import socket; s=socket.create_connection(('127.0.0.1',9022),1); s.close(); print('unexpected')", env=env)
    print("A permitted 9021 probe may still fail with connection refused when root is not listening; that is distinct from sandbox denial.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
