#!/usr/bin/env python3
"""Create the local, reproducible Section9 model-adapter worktree.

The upstream checkout stays immutable.  This command creates a separate
detached worktree, applies the reviewed adapter patch, installs the adapter
dependency, and commits only the adapter overlay locally.  It never prints or
persists credential values.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess


def run(args: list[str], *, cwd: Path | None = None, capture: bool = False) -> str:
    result = subprocess.run(args, cwd=cwd, check=True, text=True, capture_output=capture)
    return result.stdout.strip() if capture else ""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path(".runtime/external-projects/support-agent"))
    parser.add_argument("--target", type=Path, default=Path(".runtime/external-projects/support-agent-adapted"))
    parser.add_argument("--patch", type=Path, default=Path("integrations/support_agent/model_adapter.patch"))
    parser.add_argument("--budget-patch", type=Path, default=Path("integrations/support_agent/model_budget.patch"))
    parser.add_argument("--base-ref", default="860a29ff80f6e9b866e4cf1098c042e0da425f24")
    args = parser.parse_args()
    repo = args.repo.resolve()
    target = args.target.resolve()
    patch = args.patch.resolve()
    budget_patch = args.budget_patch.resolve()
    if run(["git", "rev-parse", "HEAD"], cwd=repo, capture=True) != args.base_ref:
        raise SystemExit("upstream checkout is not at the pinned candidate commit")
    if run(["git", "status", "--porcelain"], cwd=repo, capture=True):
        raise SystemExit("upstream checkout is dirty; refusing to derive an adapter")
    if target.exists():
        raise SystemExit(f"adapter target already exists: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    run(["git", "worktree", "add", "--detach", str(target), args.base_ref], cwd=repo)
    try:
        run(["git", "apply", "--whitespace=error", str(patch)], cwd=target)
        run(["git", "apply", "--whitespace=error", str(budget_patch)], cwd=target)
        run(["uv", "venv", "--python", "3.11", str(target / ".venv")], cwd=target)
        run(["uv", "pip", "install", "--python", str(target / ".venv/bin/python"), "-r", "requirements.txt", "langchain-openai"], cwd=target)
        run([str(target / ".venv/bin/python"), "-m", "pytest", "-q"], cwd=target)
        run(["git", "add", "src/llm.py", "src/agents/base.py", "src/orchestrator.py"], cwd=target)
        run(["git", "-c", "user.name=Section9 local adapter", "-c", "user.email=section9-adapter@invalid", "commit", "-m", "section9: add bounded model adapter"], cwd=target)
        adapter_commit = run(["git", "rev-parse", "HEAD"], cwd=target, capture=True)
        package_version = run([str(target / ".venv/bin/python"), "-c", "import importlib.metadata as m; print(m.version('langchain-openai'))"], cwd=target, capture=True)
        patch_sha256 = hashlib.sha256(patch.read_bytes()).hexdigest()
        metadata = {
            "schema_version": "support-agent-adapter-v2",
            "base_commit": args.base_ref,
            "adapter_commit": adapter_commit,
            "patch": "integrations/support_agent/model_adapter.patch",
            "patch_sha256": patch_sha256,
            "budget_patch": "integrations/support_agent/model_budget.patch",
            "budget_patch_sha256": hashlib.sha256(budget_patch.read_bytes()).hexdigest(),
            "package": "langchain-openai",
            "package_version": package_version,
            "model_limits": {"max_output_tokens_per_call": 512, "max_tool_rounds": 2,
                             "orchestrator_retry_attempts": 1},
            "credential_refs": ["SECTION9_MODEL_API_KEY", "SECTION9_MODEL_URL", "SECTION9_MODEL"],
        }
        (target.parent / "support-agent-adapted-manifest.json").write_text(
            json.dumps(metadata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        print(json.dumps({k: v for k, v in metadata.items() if k != "patch"}, ensure_ascii=False, sort_keys=True))
    except Exception:
        run(["git", "worktree", "remove", "--force", str(target)], cwd=repo)
        raise
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
