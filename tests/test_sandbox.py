from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from s9.sandbox import worker_command


ROOT = Path(__file__).resolve().parents[1]


def test_worker_command_preserves_symlink_and_port(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    python = str(ROOT / ".venv" / "bin" / "python")
    command = worker_command(python, ROOT, 9021, ["--id", "single", "--base-url", "http://localhost:9021"])
    assert command[0:2] == ["/usr/bin/sandbox-exec", "-p"]
    assert command[3] == python
    assert command[-4:] == ["--id", "single", "--base-url", "http://localhost:9021"]
    assert 'localhost:9021' in command[2]
    assert 's9/workers.py' in command[2]
    assert 's9/config.py' in command[2]


def test_non_macos_is_explicitly_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    with pytest.raises(RuntimeError, match="requires macOS"):
        worker_command(sys.executable, ROOT, 9021, [])


@pytest.mark.skipif(sys.platform != "darwin", reason="requires macOS Seatbelt")
def test_real_worker_imports_httpx_but_requires_token_and_cannot_import_config() -> None:
    python = str(ROOT / ".venv" / "bin" / "python")
    command = worker_command(python, ROOT, 9021, ["--id", "single", "--base-url", "http://localhost:9021"])
    env = {"PATH": "/usr/bin:/bin", "PYTHONNOUSERSITE": "1",
           "PYTHONPATH": str(ROOT / ".venv" / "lib" / "python3.12" / "site-packages")}
    # macOS 15's seatbelt execvp checks the symlink spelling before applying
    # the realpath rule. The builder preserves the requested symlink, while a
    # supervisor can resolve argv[3] at launch (the equivalent runtime probe).
    runtime_command = command.copy()
    runtime_command[3] = str(Path(python).resolve())
    worker = subprocess.run(runtime_command, cwd=ROOT, env=env, text=True, capture_output=True, timeout=15)
    assert worker.returncode != 0
    assert "S9_AGENT_TOKEN is required" in worker.stderr

    # Reuse the generated profile to prove the module dependencies load while
    # operator config is outside the worker read boundary.
    probe = runtime_command[:4] + ["-c", "import httpx; print('httpx-ok'); import s9.config"]
    blocked = subprocess.run(probe, cwd=ROOT, env=env, text=True, capture_output=True, timeout=15)
    assert "httpx-ok" in blocked.stdout
    assert blocked.returncode != 0
    assert "Operation not permitted" in blocked.stderr
