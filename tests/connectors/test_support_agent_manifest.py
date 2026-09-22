import json
import subprocess
import sys

from integrations.support_agent.manifest import COMMIT, get_manifest


def test_manifest_is_pinned_and_contains_operational_boundaries() -> None:
    manifest = get_manifest()
    assert manifest["commit"] == "860a29ff80f6e9b866e4cf1098c042e0da425f24" == COMMIT
    assert manifest["license"] == "MIT"
    assert "s9/api.py" in manifest["protected_paths"]
    assert set(manifest["model_credential_refs"]) == {"GEMINI_API_KEY", "GEMINI_API_KEYS", "GROQ_API_KEY"}
    assert set(manifest["adapter_credential_refs"]) == {"SECTION9_MODEL_API_KEY", "SECTION9_MODEL_URL", "SECTION9_MODEL"}
    assert all("=" not in value for value in manifest["model_credential_refs"])


def test_manifest_report_is_runnable_without_network() -> None:
    result = subprocess.run([sys.executable, "-m", "integrations.support_agent.report"], capture_output=True, text=True, check=True)
    output = json.loads(result.stdout)
    assert output["repo_url"].startswith("https://")
    assert output["commit"] == COMMIT
    assert "must-not-leak" not in result.stdout
