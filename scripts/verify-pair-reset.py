#!/usr/bin/env python3
"""Bounded real-provider isolation experiment; retains interrupted arm evidence."""
import json
import time
from pathlib import Path

import httpx

base = 'http://127.0.0.1:9019'
out = Path('artifacts/showcase-reset') / str(time.time_ns())
out.mkdir(parents=True)
report = {'origin': 'real_local_services_remote_provider', 'checks': [], 'pair_id': None}
with httpx.Client(base_url=base, timeout=45, trust_env=False) as client:
    def get(path):
        r = client.get(path)
        r.raise_for_status()
        return r.json()

    def post(path, body):
        r = client.post(path, json=body)
        r.raise_for_status()
        return r.json()

    def snapshots(pair):
        return {arm: get(f"/api/pairs/{pair['pair_id']}/arms/{arm}") for arm in ('swarm', 'baseline')}

    try:
        report['identity'] = get('/api/health')['identity']
        pair = post('/api/pairs', {'scenario': 'prompt', 'seed': 42})
        report['pair_id'] = pair['pair_id']
        post(f"/api/pairs/{pair['pair_id']}/start", {'expected_spec_hash': pair['spec_hash']})
        deadline = time.monotonic() + 35
        while time.monotonic() < deadline:
            before = snapshots(pair)
            # A started request with no terminal event is actually in flight.
            active = {}
            for arm, snap in before.items():
                sent = {e['payload']['request_id'] for e in snap['events'] if e['event_type'] == 'model.provider_started'}
                ended = {e['payload'].get('request_id') for e in snap['events'] if e['event_type'] in {'model.completed', 'model.failed', 'model.cancelled'}}
                active[arm] = sorted(sent - ended)
            if all(active.values()):
                break
            time.sleep(.05)
        else:
            raise AssertionError('Did not observe both arms in flight; no isolation claim made')
        assert before['swarm']['run']['generation'] == before['baseline']['run']['generation'] == '1'
        started = time.monotonic()
        response = post(f"/api/pairs/{pair['pair_id']}/arms/swarm/reset", {})
        report['reset_s'] = time.monotonic() - started
        after = snapshots(pair)
        assert after['swarm']['run']['status'] == 'reset'
        assert after['swarm']['config']['current_hash'] == pair['immutable_spec']['baseline_config_hash']
        assert after['baseline']['run']['generation'] == '1'
        assert not any(e['event_type'] in {'system.reset', 'model.cancelled'} for e in after['baseline']['events'])
        assert after['baseline']['config']['current']['generation'] == before['baseline']['config']['current']['generation']
        report['checks'].append({'name': 'same_generation_both_inflight_scoped_reset', 'passed': True, 'request_ids': active})
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            final = snapshots(pair)
            if final['baseline']['run']['status'] in {'resolved', 'failed', 'reset'}:
                break
            time.sleep(.5)
        else:
            raise AssertionError('Baseline did not terminate in bounded interval')
        assert all(any(e['event_type']=='model.completed' and e['payload'].get('request_id')==rid for e in final['baseline']['events']) for rid in active['baseline'])
        assert final['swarm']['usage']['unknown_count'] >= 1
        assert final['baseline']['usage']['unknown_count'] == 0
        report['checks'].append({'name': 'baseline_original_request_completed_and_independent_usage', 'passed': True,
                                 'baseline_result': final['baseline']['run']['status']})
        changed = post('/api/pairs', {'scenario': 'prompt', 'seed': 42, 'previous_pair_id': pair['pair_id']})
        assert changed['pair_id'] != pair['pair_id'] and changed['swarm_run_id'] != pair['swarm_run_id'] and changed['baseline_run_id'] != pair['baseline_run_id']
        # Explicitly leave the newly prepared but unstarted Pair as interrupted;
        # it incurred no provider calls and cannot look like an unfinished success.
        post(f"/api/pairs/{changed['pair_id']}/reset", {})
        report['checks'].append({'name': 'rerun_new_pair_and_run_ids_no_hidden_replacement', 'passed': True, 'new_pair_id': changed['pair_id']})
        report.update(pair=get('/api/pairs/'+pair['pair_id']), before=before, after=after, final=final, reset_response=response)
    except BaseException as exc:
        report['failure'] = type(exc).__name__ + ': ' + str(exc)
        if report['pair_id']:
            report['final_observed'] = snapshots({'pair_id': report['pair_id']})
        raise
    finally:
        (out / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(json.dumps({'evidence': str(out), 'pair_id': report['pair_id'], 'checks': report['checks'], 'failure': report.get('failure')}))
