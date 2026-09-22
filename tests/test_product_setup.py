from pathlib import Path
import importlib.util
import subprocess

ROOT = Path(__file__).resolve().parents[1]

def run(script, *args):
    return subprocess.run(["python3", str(ROOT / "scripts" / script), *args], cwd=ROOT, text=True, capture_output=True)

def test_setup_entrypoints_help_without_service():
    for script in ("doctor.py", "readiness.py"):
        result = run(script, "--help")
        assert result.returncode == 0
        assert "usage:" in result.stdout

def test_start_is_install_only_and_does_not_sync():
    text = (ROOT / "scripts/start.sh").read_text()
    assert "uv sync" not in text
    assert "npm" not in text
    assert "fetch-office-assets" not in text

def test_stop_has_app_only_mode_and_fixed_compose_scope():
    text = (ROOT / "scripts/stop.sh").read_text()
    assert "--app-only" in text
    assert "infra-stop.sh" in text

def test_readiness_does_not_probe_business_by_default():
    result = run("readiness.py", "--base-url", "http://127.0.0.1:1")
    assert "business_probe" in result.stdout
    assert "not_requested" in result.stdout

def test_doctor_rejects_port_that_cannot_fit_operator_and_agent_ingress():
    spec = importlib.util.spec_from_file_location("section9_doctor", ROOT / "scripts/doctor.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.check_port(10**100) == "invalid"

def test_empty_model_key_is_allowed_for_runtime_history_start():
    text = (ROOT / "scripts/start.sh").read_text()
    assert "--check-runtime" in text
    assert "--require-model-key" not in text


def test_generated_empty_config_runtime_vs_business_checks_are_read_only(tmp_path):
    import os
    import shutil
    root = tmp_path / 'checkout'
    (root / 'infra').mkdir(parents=True)
    shutil.copy(ROOT / 'infra/docker-compose.yml', root / 'infra/docker-compose.yml')
    initialized = run('init-local-config.py', '--root', str(root))
    assert initialized.returncode == 0, initialized.stdout
    before = {name: (root / name).read_bytes() for name in ['.env', 'infra/.env']}
    runtime = run('init-local-config.py', '--root', str(root), '--check-runtime')
    strict = run('init-local-config.py', '--root', str(root), '--check')
    assert runtime.returncode == 0
    assert strict.returncode == 1 and 'is empty' in strict.stdout
    assert before == {name: (root / name).read_bytes() for name in before}
    config = root / '.env'
    config.write_text(config.read_text().replace('S9_PORT=9019', 'S9_PORT=65534'))
    os.chmod(config, 0o600)
    invalid = run('init-local-config.py', '--root', str(root), '--check-runtime')
    assert invalid.returncode == 1 and 'S9_PORT must' in invalid.stdout
