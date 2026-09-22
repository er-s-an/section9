#!/usr/bin/env python3
"""Bounded real-provider acceptance for additional frozen paired scenarios."""
import argparse
import json
import time
from pathlib import Path

import httpx

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--scenarios', nargs='+', choices=['prompt', 'cost', 'loop', 'composite'], default=['prompt', 'cost', 'loop'])
args = parser.parse_args()
out = Path('artifacts/showcase-cases') / str(time.time_ns())
out.mkdir(parents=True)
report = {'kind': 'real_services_remote_model', 'cases': [], 'passed': False}
with httpx.Client(base_url='http://127.0.0.1:9019', timeout=30, trust_env=False) as client:
    def get(path):
        r = client.get(path)
        r.raise_for_status()
        return r.json()

    def post(path, data):
        r = client.post(path, json=data)
        r.raise_for_status()
        return r.json()

    report['identity'] = get('/api/health')['identity']
    for scenario in args.scenarios:
        item = {'scenario': scenario, 'passed': False}
        report['cases'].append(item)
        try:
            pair = post('/api/pairs', {'scenario': scenario, 'seed': 42})
            pid = pair['pair_id']
            item['pair_id'] = pid
            item['prepared'] = {a: get(f'/api/pairs/{pid}/arms/{a}') for a in ['swarm', 'baseline']}
            assert item['prepared']['swarm']['config']['current_hash'] == item['prepared']['baseline']['config']['current_hash']
            post(f'/api/pairs/{pid}/start', {'expected_spec_hash': pair['spec_hash']})
            deadline = time.monotonic() + 205
            while time.monotonic() < deadline:
                current = get('/api/pairs/' + pid)
                if current['status'] not in ['preparing', 'ready', 'running']:
                    break
                time.sleep(.5)
            else:
                post(f'/api/pairs/{pid}/reset', {})
                raise AssertionError('Pair exceeded bounded acceptance interval; interrupted and retained')
            item['pair'] = current
            item['arms'] = {a: get(f'/api/pairs/{pid}/arms/{a}') for a in ['swarm', 'baseline']}
            item['passed'] = all(s['run']['status'] == 'resolved' and s['verification']['passed'] is True
                                 and s['usage']['unknown_count'] == 0 for s in item['arms'].values()) and current['comparison_integrity']['eligible']
            print(json.dumps({'scenario': scenario, 'pair_id': pid, 'passed': item['passed'],
                              'arms': {a: {'status': s['run']['status'], 'elapsed_s': s['run'].get('elapsed_s'),
                                           'known_tokens': s['usage']['known_tokens'], 'unknown': s['usage']['unknown_count']}
                                       for a, s in item['arms'].items()}}, ensure_ascii=False), flush=True)
        except Exception as exc:
            item['error'] = type(exc).__name__ + ': ' + str(exc)
        finally:
            (out / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    report['passed'] = all(item['passed'] for item in report['cases'])
    (out / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
print(json.dumps({'evidence': str(out), 'passed': report['passed']}), flush=True)
raise SystemExit(0 if report['passed'] else 1)
