import asyncio
import json

from fastapi import APIRouter, Header, Request
from fastapi.responses import StreamingResponse

from s9.pairs.contracts import Arm, CreatePair, EmptyRequest, StartPair

router = APIRouter(prefix='/api/pairs', tags=['paired experiments'])


def coordinator(request):
    return request.app.state.pairs


@router.get('')
async def list_pairs(request: Request):
    c = coordinator(request)
    return {'items': [c.refresh(p['pair_id']) for p in c.catalog.list()]}


@router.get('/active')
async def active(request: Request):
    c = coordinator(request)
    p = c.catalog.active()
    return {'active_pair': c.refresh(p['pair_id']) if p else None}


@router.post('')
async def create(body: CreatePair, request: Request, idempotency_key: str | None = Header(default=None)):
    return await coordinator(request).create(body, idempotency_key)


@router.get('/{pair_id}')
async def detail(pair_id: str, request: Request):
    return coordinator(request).refresh(pair_id)


@router.post('/{pair_id}/start')
async def start(pair_id: str, body: StartPair, request: Request):
    return await coordinator(request).start(pair_id, body.expected_spec_hash)


@router.post('/{pair_id}/reset')
async def reset(pair_id: str, body: EmptyRequest, request: Request):
    return await coordinator(request).reset(pair_id)


@router.post('/{pair_id}/arms/{arm}/reset')
async def reset_arm(pair_id: str, arm: Arm, body: EmptyRequest, request: Request):
    return await coordinator(request).reset(pair_id, arm)


@router.get('/{pair_id}/arms/{arm}')
async def snapshot(pair_id: str, arm: Arm, request: Request):
    return coordinator(request).snapshot(pair_id, arm)


@router.get('/{pair_id}/logbook')
async def logbook(pair_id: str, request: Request, arm: Arm | None = None, after: int = 0):
    c = coordinator(request)
    c.refresh(pair_id)
    return {'items': c.journal.read(pair_id, arm, max(0, after)), 'as_of_sequence': c.journal.cursor(pair_id)}


@router.get('/{pair_id}/events')
async def events(pair_id: str, request: Request, arm: Arm, after: int = 0):
    c = coordinator(request)
    c.catalog.get(pair_id)  # Unknown Pair must return JSON 404 before streaming.
    try:
        after = max(after, int(request.headers.get('last-event-id', '0')))
    except ValueError:
        pass

    async def stream():
        cursor = max(0, after)
        while not await request.is_disconnected():
            c.refresh(pair_id)
            rows = c.journal.read(pair_id, arm, cursor, 200)
            for event in rows:
                cursor = event['sequence']
                yield f'id: {cursor}\nevent: update\ndata: {json.dumps(event, ensure_ascii=False)}\n\n'
            if not rows:
                yield ': heartbeat\n\n'
            await asyncio.sleep(.5)
    return StreamingResponse(stream(), media_type='text/event-stream', headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})
