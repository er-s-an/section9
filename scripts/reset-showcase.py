#!/usr/bin/env python3
"""Explicit operator reset of a selected Pair; retains both arms' evidence."""
import argparse
import json

import httpx

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--pair-id', help='defaults to the active/last selected Pair')
args = parser.parse_args()
with httpx.Client(base_url='http://127.0.0.1:9019', timeout=45, trust_env=False) as client:
    pair_id = args.pair_id
    if not pair_id:
        response = client.get('/api/pairs/active')
        response.raise_for_status()
        pair = response.json()['active_pair']
        if pair is None:
            print('No Pair exists; nothing to reset.')
            raise SystemExit(0)
        pair_id = pair['pair_id']
    response = client.post(f'/api/pairs/{pair_id}/reset', json={})
    response.raise_for_status()
    result = response.json()
    print(json.dumps({k: result[k] for k in ('pair_id', 'status', 'rerun_requires_new_pair')}))
