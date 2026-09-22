"""Stable startup identity for the local Section9 checkout."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parent.parent


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _tree_hash(paths: Iterable[Path], *, suffixes: set[str] | None = None) -> str:
    digest = hashlib.sha256()
    files = []
    for base in paths:
        if base.is_file():
            files.append((base.name, base))
        elif base.is_dir():
            files.extend(
                (str(path.relative_to(ROOT)), path)
                for path in base.rglob("*")
                if path.is_file()
                and "__pycache__" not in path.parts
                and path.suffix != ".pyc"
                and (suffixes is None or path.suffix in suffixes)
            )
    for name, path in sorted(files):
        digest.update(name.encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _git(*args: str) -> str:
    try:
        return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True, text=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _dirty_hash(status: str) -> str:
    """Hash tracked diff plus untracked source/config files, without caches."""
    digest = hashlib.sha256()
    digest.update(_git("diff", "--binary", "HEAD").encode())
    digest.update(status.encode())
    try:
        raw = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard", "-z"],
            cwd=ROOT, check=True, capture_output=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        raw = b""
    for item in sorted(part for part in raw.split(b"\0") if part):
        path = ROOT / item.decode()
        if (not path.is_file() or "__pycache__" in path.parts or path.suffix == ".pyc"
                or any(part in {"artifacts", ".runtime", "data", "node_modules"} for part in path.relative_to(ROOT).parts)):
            continue
        digest.update(item)
        digest.update(path.read_bytes())
    return digest.hexdigest()


def capture_identity() -> dict[str, object]:
    """Capture immutable process identity once at application startup."""
    status = _git("status", "--porcelain=v1", "--untracked-files=all")
    return {
        "checkout": str(ROOT),
        "git_commit": _git("rev-parse", "HEAD"),
        "dirty": bool(status),
        "dirty_diff_sha256": _dirty_hash(status),
        "backend_source_hash": _tree_hash([ROOT / "s9"], suffixes={".py"}),
        "frontend_build_hash": _tree_hash([ROOT / "frontend" / "dist"]),
        "dependency_lock_hash": _tree_hash([
            ROOT / "uv.lock", ROOT / "frontend" / "package-lock.json", ROOT / "integrations" / "gep" / "package-lock.json",
        ]),
        "fixture_hash": _tree_hash([ROOT / "assets" / "xiaozhi"]),
        "acceptance_contract_hash": _tree_hash([
            ROOT / "s9" / "victim.py", ROOT / "s9" / "contracts.py", ROOT / "docs" / "CONTRACTS.md",
            ROOT / "tests" / "test_acceptance_contract.py",
        ]),
    }


IDENTITY_KEYS = (
    "checkout",
    "git_commit",
    "backend_source_hash",
    "frontend_build_hash",
    "dependency_lock_hash",
    "fixture_hash",
    "acceptance_contract_hash",
)

DIAGNOSTIC_KEYS = ("dirty", "dirty_diff_sha256")


def identity_mismatches(expected: dict, observed: dict) -> dict[str, dict[str, object]]:
    return {
        key: {"expected": expected.get(key), "observed": observed.get(key)}
        for key in IDENTITY_KEYS
        if expected.get(key) != observed.get(key)
    }
