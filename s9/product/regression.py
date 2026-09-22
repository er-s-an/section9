"""Independent, protected runner for the first external repair candidate."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from s9.product.models import CandidateChange, VerificationReceipt


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _sha256(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode()
    return hashlib.sha256(data).hexdigest()


class RegressionRunner:
    """Compare immutable upstream worktrees; no network or external side effect."""

    def __init__(self, repo: Path, python: Path, acceptance_test: Path, work_root: Path):
        self.repo = Path(repo)
        self.python = Path(python)
        self.acceptance_test = Path(acceptance_test)
        self.work_root = Path(work_root)

    def _git(self, *args: str, cwd: Path | None = None) -> str:
        return subprocess.check_output(["git", *args], cwd=cwd or self.repo, text=True, stderr=subprocess.STDOUT).strip()

    def _worktree(self, ref: str, name: str) -> Path:
        path = self.work_root / name
        if path.exists():
            subprocess.run(["git", "worktree", "remove", "--force", str(path)], cwd=self.repo, check=False,
                           capture_output=True)
            shutil.rmtree(path, ignore_errors=True)
        subprocess.run(["git", "worktree", "prune"], cwd=self.repo, check=False, capture_output=True)
        path.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(["git", "worktree", "add", "--detach", str(path), ref], cwd=self.repo,
                                check=False, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"git worktree add failed: {result.stderr.strip()[-500:]}")
        return path

    def _run(self, cwd: Path, args: list[str], timeout: float) -> dict[str, Any]:
        env = {"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", ""),
               "PYTHONNOUSERSITE": "1", "PYTHONPATH": str(cwd)}
        started = time.monotonic()
        try:
            result = subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout, check=False)
        except subprocess.TimeoutExpired as exc:
            return {"status": "unknown", "timeout": True, "elapsed_s": round(time.monotonic() - started, 3),
                    "stdout": (exc.stdout or "")[-6000:], "stderr": (exc.stderr or "")[-6000:]}
        return {"status": "passed" if result.returncode == 0 else "failed", "returncode": result.returncode,
                "timeout": False, "elapsed_s": round(time.monotonic() - started, 3),
                "stdout": result.stdout[-6000:], "stderr": result.stderr[-6000:]}

    def run(self, *, project_id: str, environment_id: str, incident_id: str, configuration_sha256: str,
            baseline_ref: str = "708fd1d^", candidate_ref: str = "860a29ff80f6e9b866e4cf1098c042e0da425f24",
            root_cause_ref: str = "708fd1d") -> dict[str, Any]:
        self.work_root.mkdir(parents=True, exist_ok=True)
        baseline_path = candidate_path = None
        try:
            baseline_path = self._worktree(baseline_ref, "baseline")
            candidate_path = self._worktree(candidate_ref, "candidate")
            regression_args = [str(self.python), "-m", "pytest", "-q", "--disable-warnings", str(self.acceptance_test)]
            baseline = self._run(baseline_path, regression_args, 90)
            candidate = self._run(candidate_path, regression_args, 90)
            full_candidate = self._run(candidate_path, [str(self.python), "-m", "pytest", "-q", "--disable-warnings"], 180)
            protected = ["api.py", "src/backend", "src/tools.py", "src/agents/order_agent.py",
                         "src/agents/refund_agent.py", "src/agents/payment_agent.py"]
            protected_unchanged = subprocess.run(["git", "diff", "--quiet", f"{root_cause_ref}^", root_cause_ref, "--", *protected],
                                                 cwd=self.repo, check=False).returncode == 0
            patch = self._git("diff", f"{root_cause_ref}^", root_cause_ref)
            contract = {"name": "support-agent-loop-regression", "test": str(self.acceptance_test),
                        "protected_paths": protected, "baseline_ref": baseline_ref, "candidate_ref": candidate_ref,
                        "root_cause_ref": root_cause_ref, "configuration_sha256": configuration_sha256}
            contract_hash = _sha256(json.dumps(contract, sort_keys=True, separators=(",", ":")))
            patch_hash = _sha256(patch)
            scope = {"project_id": project_id, "environment_id": environment_id, "incident_id": incident_id}
            change = CandidateChange(
                **scope, change_id="change_support_agent_loop_708fd1d", proposed_at=_timestamp(),
                summary="恢复重复工具调用时的非空业务回退，保留外部候选原有工具与权限边界",
                base_ref=baseline_ref, candidate_ref=candidate_ref, patch_sha256=patch_hash,
                changed_paths=["src/agents/base.py", "tests/test_agent_base.py"], protected_paths_unchanged=protected_unchanged,
                verification_contract_sha256=contract_hash, evidence_ids=[], status="proposed",
            )
            receipt = VerificationReceipt(
                **scope, receipt_id=f"receipt_{int(time.time_ns())}", verified_at=_timestamp(),
                status="ready" if baseline["status"] == "failed" and candidate["status"] == "passed" and full_candidate["status"] == "passed" and protected_unchanged else "degraded",
                evidence_ids=[], candidate_id=change.change_id, configuration_sha256=configuration_sha256,
                base_ref=baseline_ref, candidate_ref=candidate_ref,
                original_failed=baseline["status"] == "failed", candidate_passed=candidate["status"] == "passed",
                no_regressions=full_candidate["status"] == "passed" and protected_unchanged,
                checks=[{"name": "protected_acceptance", "passed": baseline["status"] == "failed" and candidate["status"] == "passed",
                         "baseline": baseline, "candidate": candidate},
                        {"name": "upstream_full_suite", "passed": full_candidate["status"] == "passed", "result": full_candidate},
                        {"name": "protected_paths_unchanged", "passed": protected_unchanged,
                         "root_cause_ref": root_cause_ref}],
                result_sha256=_sha256(repr((baseline, candidate, full_candidate))),
                detail="基线、候选与独立保护测试结果分别保留；该修复本身不改数据库或外部系统。",
            )
            return {"change": change.model_dump(mode="json"), "verification": receipt.model_dump(mode="json"),
                    "contract": contract, "patch_sha256": patch_hash, "baseline": baseline,
                    "candidate": candidate, "full_candidate": full_candidate, "protected_paths_unchanged": protected_unchanged}
        finally:
            for path in (baseline_path, candidate_path):
                if path:
                    subprocess.run(["git", "worktree", "remove", "--force", str(path)], cwd=self.repo, check=False,
                                   capture_output=True)
                    shutil.rmtree(path, ignore_errors=True)
