#!/usr/bin/env python3
"""Apply and locally commit the bounded-inference overlay to the adapter worktree."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys


def run(args: list[str], *, cwd: Path, capture: bool = False, env: dict[str, str] | None = None) -> str:
    result = subprocess.run(args, cwd=cwd, check=True, text=True, capture_output=capture, env=env)
    return result.stdout.strip() if capture else ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adapter", type=Path, default=Path(".runtime/external-projects/support-agent-adapted"))
    parser.add_argument("--patch", type=Path, default=Path("integrations/support_agent/model_budget.patch"))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    adapter = (root / args.adapter).resolve()
    patch = (root / args.patch).resolve()
    metadata_path = adapter.parent / "support-agent-adapted-manifest.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    current = run(["git", "rev-parse", "HEAD"], cwd=adapter, capture=True)
    if current != metadata.get("adapter_commit"):
        raise SystemExit("adapter HEAD does not match its recorded manifest")
    if run(["git", "status", "--porcelain"], cwd=adapter, capture=True):
        raise SystemExit("adapter worktree is dirty; refusing to overwrite local changes")

    run(["git", "apply", "--whitespace=error", str(patch)], cwd=adapter)
    safe_env = {"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", ""), "PYTHONNOUSERSITE": "1"}
    test = subprocess.run([str(adapter / ".venv/bin/python"), "-m", "pytest", "-q"], cwd=adapter,
                          text=True, capture_output=True, env=safe_env, check=False)
    if test.returncode:
        print(test.stdout[-6000:], file=sys.stderr)
        print(test.stderr[-4000:], file=sys.stderr)
        raise SystemExit("adapted candidate tests failed; changes remain isolated and uncommitted")
    run(["git", "add", "src/llm.py", "src/agents/base.py", "src/orchestrator.py"], cwd=adapter)
    run(["git", "-c", "user.name=Section9 local adapter", "-c", "user.email=section9-adapter@invalid",
         "commit", "-m", "section9: bound candidate model inference"], cwd=adapter)
    adapter_commit = run(["git", "rev-parse", "HEAD"], cwd=adapter, capture=True)
    metadata.update({"schema_version": "support-agent-adapter-v2", "adapter_commit": adapter_commit,
                     "budget_patch": "integrations/support_agent/model_budget.patch",
                     "budget_patch_sha256": hashlib.sha256(patch.read_bytes()).hexdigest(),
                     "model_limits": {"max_output_tokens_per_call": 512, "max_tool_rounds": 2,
                                      "orchestrator_retry_attempts": 1}})
    metadata_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"adapter_commit": adapter_commit, "candidate_tests": "passed",
                      "model_limits": metadata["model_limits"]}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
