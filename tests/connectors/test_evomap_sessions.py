import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from s9.connectors.evomap import EvoMapSessions
from s9.product.registry import ProductError, ProductRegistry


def adapter(tmp_path, *, drop=False):
    messages = []
    calls = []
    def handler(request):
        body = json.loads(request.content) if request.content else {}
        calls.append((request.url.path, body))
        if request.url.path.endswith('/create'):
            return httpx.Response(200, json={'session_id': 'native-test'})
        if request.url.path.endswith('/context'):
            return httpx.Response(200, json={'recent_messages': messages[:-1] if drop else messages})
        if request.url.path.endswith('/message'):
            assert len(json.dumps(body['payload']).encode()) < 6000
            messages.append({'id':str(len(messages)), 'fromNodeId':body['sender_id'], 'payload':body['payload']})
        return httpx.Response(200, json={'ok':True})
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    nodes = {r:{'node_id':'node_'+r, 'node_secret':'test-'+r} for r in ['coordinator','investigator','reviewer']}
    registry = ProductRegistry(tmp_path / 'registry.sqlite')
    return EvoMapSessions(registry, client, nodes), calls


def test_large_native_handoff_is_reconstructed_from_verified_chunks(tmp_path):
    async def run():
        native,calls=adapter(tmp_path)
        task=SimpleNamespace(id='task-1',role='investigator')
        context={'evidence':'订单上下文'*1800}
        returned=await native.prepare('w','r',task,context)
        assert returned == context
        await native.finish('w','r',task,{'summary':'根据证据复核完成'})
        assert [e['kind'] for e in native.binding('w','r')['events']] == ['handoff','result']
        assert len([c for c in calls if c[0].endswith('/create')])==1
        assert len([c for c in calls if c[0].endswith('/message')])>2
        await native.client.aclose()
    asyncio.run(run())


def test_missing_remote_chunk_stops_before_model_handoff(tmp_path):
    async def run():
        native,_=adapter(tmp_path,drop=True)
        with pytest.raises(ProductError,match='原生交接'):
            await native.prepare('w','r',SimpleNamespace(id='t',role='investigator'),{'x':'test'})
        assert native.binding('w','r')['events']==[]
        await native.client.aclose()
    asyncio.run(run())


def test_paused_member_cannot_publish_and_another_node_takes_over(tmp_path):
    async def run():
        native,_=adapter(tmp_path)
        task=SimpleNamespace(id='task-1',role='investigator')
        await native.prepare('w','r',task,{'x':'evidence'})
        await native.pause('w','r','investigator')
        with pytest.raises(ProductError,match='成员已暂停'):
            await native.finish('w','r',task,{'summary':'late result'})
        assert native.worker_id('w','r',task)=='node_coordinator'
        await native.prepare('w','r',task,{'x':'evidence','attempt':2})
        await native.finish('w','r',task,{'summary':'takeover result'})
        record=native.binding('w','r')
        assert any(e['kind']=='takeover' and e['node_id']=='node_coordinator' for e in record['events'])
        assert record['events'][-1]['summary']=='takeover result'
        await native.client.aclose()
    asyncio.run(run())
