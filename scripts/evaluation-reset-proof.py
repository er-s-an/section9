#!/usr/bin/env python3
"""Deliberately interrupt one real evaluation request; retain the failed trial.

This safety experiment is visibly OPERATOR_RESET, not an autonomous performance
trial. It is retained in statistics so unknown provider cost cannot disappear.
"""
import json
from pathlib import Path
import time

import httpx

ROOT = Path(__file__).resolve().parents[1]


def main():
    out = ROOT / 'artifacts' / 'evaluation-reset' / str(time.time_ns())
    out.mkdir(parents=True)
    evidence = {'origin': 'live_remote_model_intentional_interruption', 'performance_trial': False}
    with httpx.Client(base_url='http://127.0.0.1:9024', timeout=15, trust_env=False) as client:
        client.post('/api/reset', json={}).raise_for_status()
        response = client.post('/api/evaluate', json={'scenario': 'prompt', 'condition': 'swarm', 'seed': 42})
        response.raise_for_status()
        rid = response.json()['run_id']
        evidence['run_id'] = rid
        try:
            for _ in range(80):
                run = client.get('/api/runs/' + rid).json()
                if any(row.get('status') == 'reserved' for row in run.get('usage', [])):
                    break
                time.sleep(.1)
            else:
                raise AssertionError('No in-flight provider reservation observed')
            started = time.monotonic()
            reset = client.post('/api/reset', json={})
            reset.raise_for_status()
            evidence['reset_seconds'] = time.monotonic() - started
            evidence['reset'] = reset.json()
            run = client.get('/api/runs/' + rid).json()
            evidence['run'] = run
            assert run['status'] == 'reset' and run['usage_unknown']
            assert run.get('unknown_reserved_tokens', 0) > 0
            assert reset.json()['model_cancellation']['remaining'] == 0
            assert evidence['reset_seconds'] < 2
            evidence['passed'] = True
        except Exception as exc:
            evidence.update(passed=False, error=str(exc))
            raise
        finally:
            evidence['run'] = client.get('/api/runs/' + rid).json()
            (out / 'report.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2))
            print(json.dumps({'passed': evidence.get('passed'), 'run_id': rid, 'reset_s': evidence.get('reset_seconds'), 'evidence': str(out / 'report.json')}))


if __name__ == '__main__':
    main()
