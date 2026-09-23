from s9.pairs.journal import PairJournal
from s9.pairs.coordinator import PairCoordinator
from s9.pairs.projection import project_arm
from s9.store import DEFAULT_CONFIG, Store


def _pair():
    return {'pair_id': 'p-history', 'swarm_run_id': 'run-history',
            'baseline_run_id': 'other', 'spec_hash': 'spec-history',
            'immutable_spec': {'baseline_config': DEFAULT_CONFIG}}


def test_ingest_drains_fixed_high_water_and_pages_without_gaps(tmp_path):
    pair = _pair()
    store = Store(tmp_path / 'source.sqlite', scope={'pair_id': 'p-history', 'run_id': 'run-history',
                                                     'arm': 'swarm', 'spec_hash': 'spec-history'},
                  baseline_config=DEFAULT_CONFIG)
    for n in range(12050):
        store.emit('observation.probe', {'summary': f'event-{n}'}, run_id='run-history', producer='probe')
    store.emit('incident.closed', {'summary': 'terminal'}, run_id='run-history', producer='verifier')
    journal = PairJournal(tmp_path / 'journal.sqlite')
    journal.ingest(pair, 'swarm', store)
    rows = journal.read('p-history', 'swarm', 0, 20000)
    assert len(rows) == 12051
    assert rows[-1]['payload']['summary'] == 'terminal'
    earlier_page = journal.read_before('p-history', 'swarm', int(rows[-1]['sequence']), 50,
                                       journal.watermark('p-history', 'swarm'))
    assert [row['payload']['summary'] for row in earlier_page[:2]] == ['event-12000', 'event-12001']
    assert journal.has_before('p-history', 'swarm', int(earlier_page[0]['sequence']),
                              journal.watermark('p-history', 'swarm'))
    assert journal.count_after('p-history', 'swarm', int(rows[9999]['sequence']),
                               journal.watermark('p-history', 'swarm')) == 2051
    selected = journal.read_by_ids('p-history', 'swarm', 'run-history',
                                   [rows[0]['event_id'], rows[-1]['event_id'], 'missing'])
    assert [row['event_id'] for row in selected] == [rows[0]['event_id'], rows[-1]['event_id']]
    seen = []
    after = 0
    while True:
        page = journal.read_page('p-history', 'swarm', after, 500, journal.watermark('p-history', 'swarm'))
        if not page:
            break
        seen.extend(int(row['sequence']) for row in page)
        after = int(page[-1]['sequence'])
    assert seen == [int(row['sequence']) for row in rows]


def test_projection_uses_full_history_while_tail_is_bounded():
    pair = _pair()
    events = [{'event_id': f'e{n}', 'sequence': n, 'scope': 'run', 'pair_id': 'p-history',
               'arm': 'swarm', 'run_id': 'run-history', 'producer': 'probe',
               'event_type': 'observation.probe', 'payload': {'summary': str(n)}}
              for n in range(1, 10005)]
    events.append({'event_id': 'terminal', 'sequence': 10005, 'scope': 'run', 'pair_id': 'p-history',
                   'arm': 'swarm', 'run_id': 'run-history', 'producer': 'verifier',
                   'event_type': 'incident.closed', 'payload': {'summary': 'done'}})
    projection = project_arm(pair, 'swarm', {'run_id': 'run-history', 'status': 'resolved'}, events,
                             config=DEFAULT_CONFIG, agents=[], usage=[], dependencies={}, as_of_sequence=10005)
    assert projection['stages'][-1]['status'] == 'passed'
    assert len(events[-200:]) == 200


def test_export_redaction_preserves_usage_metrics():
    value = {'token_budget': 16000, 'total_tokens': 42, 'unknown_reserved_tokens': 7,
             'environment': 'showcase', 'token_hash': 'credential-hash',
             'provider_api_key': 'secret'}
    redacted = PairCoordinator._export_redact(value)
    assert redacted['token_budget'] == 16000
    assert redacted['total_tokens'] == 42
    assert redacted['unknown_reserved_tokens'] == 7
    assert redacted['environment'] == 'showcase'
    assert redacted['token_hash'] == '[REDACTED]'
    assert redacted['provider_api_key'] == '[REDACTED]'


async def test_coordinator_snapshot_and_export_cover_events_after_ten_thousand(tmp_path):
    import hashlib
    import io
    import json
    import zipfile
    from s9.pairs.contracts import CreatePair
    from s9.provenance import capture_identity

    coordinator = PairCoordinator(tmp_path / 'pairs', object(), object(), capture_identity())
    pair = await coordinator.create(CreatePair(scenario='prompt', seed=7))
    arm = coordinator.runtime(pair, 'swarm')
    rid = pair['swarm_run_id']
    arm.store.inject('prompt', 'swarm', 'component', 7, 16000, run_id=rid)
    with arm.store.tx() as db:
        for n in range(10005):
            arm.store.event(db, 'observation.probe', {'summary': str(n)}, run_id=rid)
        arm.store.event(db, 'verification.completed', {'passed': True, 'summary': 'after truncation boundary'}, run_id=rid)
        arm.store.event(db, 'incident.closed', {'summary': 'last event'}, run_id=rid)
        run = arm.store.get(db, 'runs', rid)
        run.update(status='resolved')
        arm.store.save(db, 'runs', run)
    snap = coordinator.snapshot(pair['pair_id'], 'swarm')
    assert len(snap['events']) == 200
    assert snap['events'][-1]['event_type'] == 'incident.closed'
    assert snap['stages'][-1]['status'] == 'passed'
    assert snap['as_of_sequence'] == snap['events'][-1]['sequence']
    assert snap['log_page']['has_more'] is True
    payload, _ = coordinator.export_zip(pair['pair_id'])
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        manifest = json.loads(archive.read('manifest.json'))
        for name, expected in manifest['hashes'].items():
            assert hashlib.sha256(archive.read(name)).hexdigest() == expected
        pages = [json.loads(archive.read(name)) for name in archive.namelist() if name.startswith('swarm/events-')]
        assert all(len(page) <= 500 for page in pages)
        events = [event for page in pages for event in page]
        assert len(events) == manifest['counts']['swarm']['events'] > 10000
        assert events[-1]['sequence'] == manifest['counts']['swarm']['watermark'] == snap['as_of_sequence']
        assert len({e['event_id'] for e in events}) == len(events)
        assert json.loads(archive.read('swarm/run.json'))['token_budget'] == 16000
        assert manifest['source_identity']['backend_source_hash']
