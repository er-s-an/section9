#!/usr/bin/env python3
"""Real isolated startup from source/templates, using installed local dependencies.

No provider calls, no copied credentials, no Docker changes. This is not a test
of a fresh download/install of Python or npm dependencies.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def main():
    target = ROOT / 'data' / 'clean-start' / str(time.time_ns())
    target.mkdir(parents=True)
    for directory in ('s9', 'scripts', 'assets/xiaozhi', 'integrations/gep', 'docs'):
        shutil.copytree(ROOT / directory, target / directory,
                        ignore=shutil.ignore_patterns('__pycache__', 'node_modules', '*.pyc'))
    for name in ('.env.example', 'uv.lock', 'pyproject.toml'):
        shutil.copy2(ROOT / name, target / name)
    (target / '.venv').symlink_to(ROOT / '.venv', target_is_directory=True)
    (target / 'frontend').mkdir()
    (target / 'frontend/dist').symlink_to(ROOT / 'frontend/dist', target_is_directory=True)
    shutil.copy2(ROOT / 'frontend/package-lock.json', target / 'frontend/package-lock.json')
    (target / 'integrations/gep/node_modules').symlink_to(ROOT / 'integrations/gep/node_modules', target_is_directory=True)
    # Empty optional values specifically exercise config parsing without copying secrets.
    (target / '.env').write_text('EVOMAP_MODEL_API_KEY=\nS9_PORT=19039\nS9_MODEL_TIMEOUT=\nS9_DATA_DIR=\nS9_MODEL_CONCURRENCY=\n')
    (target / '.env').chmod(0o600)
    env = {k: v for k, v in os.environ.items() if not any(x in k for x in ('S9_', 'EVOMAP', 'LANGFUSE', 'OTEL', 'PYTHONPATH'))}
    python = str(target / '.venv/bin/python')
    report = {'origin': 'real_local_server_no_provider', 'checkout': str(target),
              'dependencies': 'reused local venv, GEP node_modules and built frontend; no credential copies', 'checks': []}
    def service(cmd):
        return subprocess.run([python, 'scripts/service.py', cmd], cwd=target, env=env,
                              text=True, capture_output=True, timeout=40)
    try:
        first = service('start')
        assert first.returncode == 0, first.stdout + first.stderr
        with urllib.request.urlopen('http://127.0.0.1:19039/api/health', timeout=5) as response:
            health = json.load(response)
        assert health['identity']['checkout'] == str(target)
        assert health['dependencies']['model']['status'] == 'unconfigured'
        report['identity'] = health['identity']
        report['checks'].append('new isolated checkout with empty optional environment started; model honestly unconfigured')
        assert service('start').returncode == 0
        source = target / 's9/victim.py'
        source.write_text(source.read_text() + '\n# startup mismatch regression probe\n')
        mismatch = service('start')
        assert mismatch.returncode != 0 and 'mismatched identity' in mismatch.stderr
        report['checks'].append('source mutation rejected existing listener; running health retained original identity')
        with urllib.request.urlopen('http://127.0.0.1:19039/api/health', timeout=5) as response:
            assert json.load(response)['identity'] == health['identity']
        report['passed'] = True
    except Exception as exc:
        report.update(passed=False, error=str(exc))
        raise
    finally:
        stopped = service('stop')
        report['stop_returncode'] = stopped.returncode
        report['stop_output'] = stopped.stdout + stopped.stderr
        path = ROOT / 'artifacts/audit-remediation/clean-start.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(json.dumps({'passed': report.get('passed'), 'stop_returncode': stopped.returncode, 'evidence': str(path)}))


if __name__ == '__main__':
    main()
