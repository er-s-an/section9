#!/usr/bin/env python3
"""One bounded real-model muted decision; outcomes are retained, never preset."""
import json
import time
from pathlib import Path

import httpx

out = Path('artifacts/product-stage1') / ('muted-' + str(time.time_ns()))
out.mkdir(parents=True)
report = {'kind': 'real_remote_model_muted_decision', 'passed': False}
with httpx.Client(base_url='http://127.0.0.1:9019', timeout=30, trust_env=False) as client:
    def get(path):
        r = client.get(path)
        r.raise_for_status()
        return r.json()

    def post(path, body):
        r = client.post(path, json=body)
        r.raise_for_status()
        return r.json()

    def put(path, body):
        r = client.put(path, json=body)
        r.raise_for_status()
        return r.json()

    before = get('/api/state')
    assert not before.get('incident'), 'reset primary before this dedicated proof'
    pair = get('/api/pairs/active').get('active_pair')
    assert not pair or pair['status'] != 'running', 'do not mix model loads with a Pair'
    report['identity'] = get('/api/health')['identity']
    try:
        put('/api/communication', {'muted': True})
        injected = post('/api/inject', {'scenario': 'prompt', 'condition': 'muted', 'seed': 42})
        rid = injected.get('run_id') or injected.get('id')
        assert rid
        report['run_id'] = rid
        deadline = time.monotonic() + 195
        while time.monotonic() < deadline:
            run = get('/api/runs/' + rid)
            if run['status'] in {'resolved', 'failed', 'reset', 'timeout'}:
                break
            time.sleep(.5)
        else:
            raise AssertionError('bounded muted run exceeded deadline')
        report['run'] = run
        events = run['events']
        repair_responses = [e for e in events if e['event_type'] == 'model.completed'
                            and e['payload'].get('purpose') == 'repair' and e['payload'].get('returned_model')]
        report['checks'] = {
            'real_repair_model_response': bool(repair_responses),
            'messages_dropped': any(e['event_type'] == 'dialog.dropped' for e in events),
            'no_peer_delivery': not any(e['event_type'] == 'dialog.received' for e in events),
            'not_scripted_missing_peer_block': not any('blocked: missing peer evidence' in str(e['payload']) for e in events),
            'terminal_outcome_retained': run['status'] in {'resolved', 'failed', 'timeout'},
        }
        report['passed'] = all(report['checks'].values())
    except Exception as exc:
        report['error'] = type(exc).__name__ + ': ' + str(exc)
    finally:
        if report.get('run_id') and 'run' not in report:
            report['run'] = get('/api/runs/' + report['run_id'])
        post('/api/reset', {})
        put('/api/communication', {'muted': before['muted']})
        (out / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
print(json.dumps({'output': str(out), 'passed': report['passed'], 'status': report.get('run', {}).get('status'),
                  'elapsed_s': report.get('run', {}).get('elapsed_s'), 'error': report.get('error')}, ensure_ascii=False))
raise SystemExit(0 if report['passed'] else 1)
