#!/usr/bin/env python3
"""Install the reproducible Langfuse overlay in the existing adapted checkout.

No extra checkout, no upstream modifications, no credentials on disk. A local
commit records this adapter revision so source-binding checks stay meaningful.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import subprocess


ROOT = Path(__file__).resolve().parents[1]


def instrument(adapter: Path) -> dict:
    metadata_path = adapter.parent / "support-agent-adapted-manifest.json"
    metadata = json.loads(metadata_path.read_text())

    def git(*args: str) -> str:
        return subprocess.check_output(["git", *args], cwd=adapter, text=True).strip()

    module = ROOT / "integrations/support_agent/observability.py"
    patch = ROOT / "integrations/support_agent/observability.patch"
    fingerprint = hashlib.sha256(module.read_bytes() + patch.read_bytes()).hexdigest()
    if git("rev-parse", "HEAD") != metadata["adapter_commit"] or git("status", "--porcelain"):
        raise SystemExit("Adapter must be clean and match its recorded revision")
    if metadata.get("observability_sha256") == fingerprint:
        return {"status": "already_installed", "adapter_commit": metadata["adapter_commit"]}
    installed = bool(metadata.get("observability_sha256"))
    if not installed:
        subprocess.run(["git", "apply", "--check", str(patch)], cwd=adapter, check=True)
    subprocess.run(["uv", "pip", "install", "--python", str(adapter / ".venv/bin/python"),
                    "langfuse==4.15.4"], cwd=adapter, check=True)
    if not installed:
        subprocess.run(["git", "apply", str(patch)], cwd=adapter, check=True)
    shutil.copyfile(module, adapter / "src/section9_observability.py")
    api_path = adapter / "api.py"
    api_text = api_path.read_text()
    if 'def section9_live_observations(' not in api_text:
        api_path.write_text(api_text + '\n\n@app.get("/observations/{session_id}")\n'
                            'def section9_live_observations(session_id: str):\n'
                            '    from src.section9_observability import live_observations\n'
                            '    return live_observations(session_id)\n')
    # Tests use a separate process without the operator's model or telemetry keys.
    import os
    safe_env = {"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", ""),
                "PYTHONNOUSERSITE": "1", "LANGFUSE_TRACING_ENABLED": "false"}
    subprocess.run([str(adapter / ".venv/bin/python"), "-m", "pytest", "-q"],
                   cwd=adapter, env=safe_env, check=True)
    git("add", "api.py", "src/orchestrator.py", "src/section9_observability.py")
    git("-c", "user.name=Section9 local adapter", "-c", "user.email=section9-adapter@invalid",
        "commit", "-m", "section9: trace target graph with native Langfuse callbacks")
    previous = metadata["adapter_commit"]
    metadata.update(schema_version="support-agent-adapter-v3", adapter_commit=git("rev-parse", "HEAD"),
                    previous_adapter_commit=previous, observability_sha256=fingerprint,
                    observability_package="langfuse==4.15.4",
                    observability_patch="integrations/support_agent/observability.patch")
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n")
    return {"status": "installed", "adapter_commit": metadata["adapter_commit"],
            "previous_adapter_commit": previous}


if __name__ == "__main__":
    print(json.dumps(instrument(ROOT / ".runtime/external-projects/support-agent-adapted")))
