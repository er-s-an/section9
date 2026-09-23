#!/usr/bin/env python3
"""Install the reviewed read-only business policy into the adapted checkout.

The installer never edits the upstream checkout or runs services. With --record
it tests and records a local adapter commit; it never commits the main repository.
It requires a clean adapter worktree matching its recorded local revision.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def git(adapter: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=adapter, text=True, capture_output=True, check=check)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adapter", type=Path, default=Path(".runtime/external-projects/support-agent-adapted"))
    parser.add_argument("--check-only", action="store_true", help="verify applicability without changing files")
    parser.add_argument("--record", action="store_true", help="run adapter tests and record a local adapter commit")
    args = parser.parse_args()
    adapter = (ROOT / args.adapter).resolve()
    metadata_path = adapter.parent / "support-agent-adapted-manifest.json"
    module = ROOT / "integrations/support_agent/business_policy.py"
    patch = ROOT / "integrations/support_agent/business_policy.patch"
    if not metadata_path.is_file() or not (adapter / "src/orchestrator.py").is_file():
        raise SystemExit("adapted support-agent checkout or manifest is missing")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    head = git(adapter, "rev-parse", "HEAD").stdout.strip()
    if head != metadata.get("adapter_commit"):
        raise SystemExit("adapter HEAD does not match its recorded manifest")

    installed_module = adapter / "src/section9_business_policy.py"
    fingerprint = hashlib.sha256(module.read_bytes() + patch.read_bytes()).hexdigest()
    status = git(adapter, "status", "--porcelain").stdout
    if installed_module.exists():
        reverse = git(adapter, "apply", "--reverse", "--check", str(patch), check=False)
        if reverse.returncode:
            raise SystemExit("recorded business-policy overlay does not match the current patch; refusing to overwrite it")
        if status:
            raise SystemExit("adapter worktree is dirty; refusing to overwrite local changes")
        source_matches = installed_module.read_bytes() == module.read_bytes()
        recorded_fingerprint = metadata.get("business_policy_sha256")
        if source_matches and recorded_fingerprint == fingerprint:
            print(json.dumps({"status": "already_recorded", "policy_sha256": fingerprint,
                              "adapter_commit": head}, sort_keys=True))
            return 0
        if not recorded_fingerprint:
            raise SystemExit("installed policy is not recorded in the adapter manifest; refusing an unverified upgrade")
        if args.check_only or not args.record:
            print(json.dumps({"status": "upgrade_available", "policy_sha256": fingerprint,
                              "adapter_commit": head}, sort_keys=True))
            return 0
        installed_module.write_bytes(module.read_bytes())
        return record(adapter, metadata_path, metadata, fingerprint)
    if status:
        raise SystemExit("adapter worktree is dirty; refusing to overwrite local changes")
    check = git(adapter, "apply", "--check", str(patch), check=False)
    if check.returncode:
        sys.stderr.write(check.stderr)
        raise SystemExit("business-policy patch does not apply cleanly")
    if args.check_only:
        print(json.dumps({"status": "applicable", "policy_sha256": fingerprint}, sort_keys=True))
        return 0

    installed_module.write_bytes(module.read_bytes())
    try:
        git(adapter, "apply", str(patch))
    except subprocess.CalledProcessError:
        installed_module.unlink(missing_ok=True)
        raise
    if args.record:
        return record(adapter, metadata_path, metadata, fingerprint)
    print(json.dumps({"status": "installed", "policy_sha256": fingerprint,
                      "adapter_head_unchanged": True, "commit_created": False,
                      "next_command": "rerun with --record to test and record the adapter overlay"}, sort_keys=True))
    return 0


def record(adapter: Path, metadata_path: Path, metadata: dict, fingerprint: str) -> int:
    allowed = {"api.py", "src/orchestrator.py", "src/agents/base.py", "src/section9_business_policy.py",
               "tests/test_api.py", "tests/test_routing.py"}
    changed = set(git(adapter, "status", "--porcelain").stdout.splitlines())
    paths = {line[3:] for line in changed if len(line) >= 4}
    if not paths.issubset(allowed):
        raise SystemExit(f"adapter has unrelated changes; refusing to record: {sorted(paths - allowed)}")
    python = adapter / ".venv/bin/python"
    if not python.is_file():
        raise SystemExit("adapted checkout virtualenv is missing")
    safe_env = {"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", ""),
                "PYTHONNOUSERSITE": "1", "LANGFUSE_TRACING_ENABLED": "false"}
    test = subprocess.run([str(python), "-m", "pytest", "-q"], cwd=adapter, env=safe_env,
                          text=True, capture_output=True, check=False)
    if test.returncode:
        sys.stderr.write(test.stdout[-6000:])
        sys.stderr.write(test.stderr[-4000:])
        raise SystemExit("adapted candidate tests failed; policy remains installed and uncommitted")
    previous = git(adapter, "rev-parse", "HEAD").stdout.strip()
    files = sorted(paths)
    git(adapter, "add", *files)
    subprocess.run(["git", "-c", "user.name=Section9 local adapter",
                    "-c", "user.email=section9-adapter@invalid", "commit", "-m",
                    "section9: add guarded read-only business policy"], cwd=adapter, check=True)
    new_head = git(adapter, "rev-parse", "HEAD").stdout.strip()
    metadata.update(schema_version="support-agent-adapter-v4", previous_adapter_commit=previous,
                    adapter_commit=new_head, business_policy_sha256=fingerprint,
                    business_policy_patch="integrations/support_agent/business_policy.patch")
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "recorded", "previous_adapter_commit": previous,
                      "adapter_commit": new_head, "policy_sha256": fingerprint,
                      "candidate_tests": "passed", "committed_files": files}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
