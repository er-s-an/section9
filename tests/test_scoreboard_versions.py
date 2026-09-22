"""Historical experiments must not masquerade as proof of current code."""
from types import SimpleNamespace

from s9.core import Core


def record(run_id, identity=None, unknown=False, tokens=0):
    manifest = {'environment': 'evaluation'}
    if identity:
        manifest['source_identity'] = identity
    return {'id': run_id, 'condition': 'swarm', 'scenario': 'prompt', 'status': 'failed' if unknown else 'resolved',
            'elapsed_s': 10, 'manifest': manifest, 'usage_unknown': unknown, 'usage_tokens': tokens, 'token_budget': 16000}


def test_current_version_excludes_legacy_and_retains_unknown_subtotals():
    identity = {key: 'current' for key in ['backend_source_hash', 'frontend_build_hash', 'dependency_lock_hash', 'fixture_hash', 'acceptance_contract_hash']}
    records = [record('legacy', tokens=800), record('known', identity, tokens=300), record('unknown', identity, unknown=True, tokens=120)]
    core = Core.__new__(Core)
    core.identity = identity
    core.evaluation_store = lambda: SimpleNamespace(runs=lambda limit: records)
    current = core.scoreboard()
    swarm = next(row for row in current['rows'] if row['id'] == 'swarm')
    assert set(swarm['run_ids']) == {'known', 'unknown'}
    assert swarm['usage_tokens'] == 420
    assert swarm['unknown_usage_runs'] == 1
    assert swarm['failures'] == 1
    assert swarm['cells'][1]['n'] == 0 and swarm['cells'][1]['usage_tokens'] is None
    historical = core.scoreboard('legacy')
    legacy_swarm = next(row for row in historical['rows'] if row['id'] == 'swarm')
    assert legacy_swarm['run_ids'] == ['legacy']
    core.identity = {**identity, 'frontend_build_hash': 'new-frontend'}
    assert all(row['n'] == 0 for row in core.scoreboard()['rows'])
