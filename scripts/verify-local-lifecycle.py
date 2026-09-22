#!/usr/bin/env python3
"""Stop/restart only Section9, preserving observability volumes; no inference."""
import json
import subprocess
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'artifacts/product-stage1' / ('lifecycle-' + str(time.time_ns()))
OUT.mkdir(parents=True)
report = {'kind': 'same_mac_warm_images_full_stop_restart', 'passed': False}

def command(args):
    return subprocess.check_output(args, cwd=ROOT, text=True).splitlines()

def volumes():
    return sorted(command(['docker', 'volume', 'ls', '--filter', 'label=com.docker.compose.project=section9-observe', '--format', '{{.Name}}']))

def containers():
    return set(command(['docker', 'ps', '--format', '{{.ID}} {{.Names}}']))

with (OUT / 'commands.log').open('w') as log:
    def run(args):
        subprocess.run(args, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, timeout=90, check=True)
    before_volumes = volumes()
    other_before = {line for line in containers() if 'section9-observe' not in line}
    try:
        start = time.monotonic()
        run(['./scripts/stop.sh'])
        report['stop_s'] = round(time.monotonic() - start, 3)
        report['volumes_preserved'] = before_volumes == volumes()
        report['project_containers_stopped'] = not any('section9-observe' in line for line in containers())
        with httpx.Client(trust_env=False, timeout=2) as c:
            try:
                c.get('http://127.0.0.1:9019/api/health')
                report['app_stopped'] = False
            except httpx.ConnectError:
                report['app_stopped'] = True
        report['other_running_containers_unchanged'] = other_before.issubset(containers())
        start = time.monotonic()
        run(['./scripts/infra-start.sh', '--pull', 'never'])
        run(['./scripts/start.sh'])
        with httpx.Client(trust_env=False, timeout=10) as c:
            health = c.get('http://127.0.0.1:9019/api/health').json()
            report['identity'] = health['identity']
            deadline = time.monotonic() + 60
            deps = {}
            report['readiness_poll_errors'] = []
            while time.monotonic() < deadline:
                try:
                    state = c.get('http://127.0.0.1:9019/api/state').json()
                    deps = state['dependencies']
                except httpx.HTTPError as exc:
                    report['readiness_poll_errors'].append(type(exc).__name__)
                    continue
                if all(deps.get(k, {}).get('status') == 'available' for k in ['langfuse', 'collector']):
                    break
                time.sleep(1)
            report['dependencies'] = {k: deps.get(k, {}).get('status') for k in ['langfuse', 'collector']}
        report['warm_start_s'] = round(time.monotonic() - start, 3)
        report['passed'] = (all(report[k] for k in ['volumes_preserved', 'project_containers_stopped',
                           'app_stopped', 'other_running_containers_unchanged']) and
                           all(v == 'available' for v in report['dependencies'].values()))
    except Exception as exc:
        report['error'] = type(exc).__name__ + ': ' + str(exc)
        # Restore this project's application even if optional observability fails.
        subprocess.run(['./scripts/start.sh'], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, timeout=60)
    finally:
        report['volume_names'] = before_volumes
        (OUT / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
print(json.dumps({'output': str(OUT), 'passed': report['passed'], 'error': report.get('error')}))
raise SystemExit(0 if report['passed'] else 1)
