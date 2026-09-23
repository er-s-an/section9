#!/usr/bin/env python3
"""Explicitly register private native collaboration nodes; never print secrets."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import uuid

import httpx


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--register', action='store_true', help='Create missing EvoMap node identities (external state)')
    args = parser.parse_args()
    path = Path.home() / '.config/section9/evomap-nodes.json'
    nodes = json.loads(path.read_text()) if path.exists() else {}
    if not args.register:
        print(json.dumps({'configured': all(r in nodes for r in ('coordinator','investigator','reviewer'))}))
        return
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    with httpx.Client(timeout=30) as client:
        for role in ('coordinator','investigator','reviewer'):
            if role in nodes:
                continue
            response = client.post('https://evomap.ai/a2a/hello', json={
                'protocol':'gep-a2a','protocol_version':'1.0.0','message_type':'hello',
                'message_id':'msg_'+uuid.uuid4().hex, 'timestamp':datetime.now(timezone.utc).isoformat(),
                'payload':{'capabilities':{'section9_role':role,'scope':'readonly application investigation'},
                    'env_fingerprint':{'platform':platform.system().lower(),'arch':platform.machine()}}})
            response.raise_for_status()
            body = response.json().get('payload', {})
            if body.get('status') != 'acknowledged' or not body.get('node_secret') or not body.get('your_node_id'):
                raise SystemExit('Registration was not acknowledged. Inspect the official account; no automatic retry.')
            nodes[role] = {'node_id':body['your_node_id'],'node_secret':body['node_secret']}
            # Persist each successful identity so partial failures never recreate it.
            temporary = path.with_name('.nodes-'+uuid.uuid4().hex)
            fd = os.open(temporary, os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w') as handle:
                json.dump(nodes, handle)
            os.replace(temporary, path)
    print(json.dumps({'configured':True, 'members':{k:v['node_id'] for k,v in nodes.items()}}))


if __name__ == '__main__':
    main()
