import pytest

from s9.product.registry import ProductError, ProductRegistry


def setup_plan(tmp_path, status='prepared'):
    r = ProductRegistry(tmp_path / 'registry.sqlite')
    w = r.create_workspace('demo', idempotency_key='w')['id']
    a = r.create_application(w, 'support', idempotency_key='a')['id']
    c = r.create_connection(w, a, 'Langfuse', idempotency_key='c')['id']
    b = r.create_scope_binding(w, a, 'local-test', c, 'application_trace', 'support', idempotency_key='b')['id']
    s = r.ingest_signals(w, a, 'local-test', b, [{'source_id': 'trace', 'source_version': '1',
        'deduplication_key': 'trace', 'signal_type': 'error', 'source_kind': 'manual',
        'occurred_at': '2026-09-24T00:00:00Z', 'observed_at': '2026-09-24T00:00:00Z',
        'summary': 'actual observation'}], idempotency_key='s')['items'][0]
    i = r.create_incident(w, a, 'local-test', 'routing error', 'low',
        [{'signal_id': s['id'], 'expected_revision': s['revision']}], idempotency_key='i')
    run = r.create_investigation_run(w, a, 'local-test', i['id'], expected_incident_revision=i['revision'],
        idempotency_key='run', execution_mode='single')['run']
    plan = {'plan_id': 'p', 'plan_sha256': 'a' * 64, 'status': status, 'run_id': run['id'],
            'source_commit': 'b' * 40, 'approval_id': 'ap', 'execution_attempt_id': 'ex',
            'validation_report_id': 'val', 'recovery_receipt_id': 'rec'}
    scope = {'project_id': w, 'environment_id': 'local-test', 'incident_id': i['id']}
    def put(kind, name, item):
        return r.put(kind, name, item, **scope)
    put('demo_resolution_plan', 'resolution-plan:p', plan)
    return r, (w, a, 'local-test', i['id'], 'p'), plan, put


def add_proofs(plan, put):
    h = {'plan_sha256': plan['plan_sha256']}
    put('approval', 'ap', {**h, 'decision': 'approved', 'execution_authorization': True})
    put('execution_attempt', 'ex', {**h, 'state': 'applied'})
    put('validation_report', 'val', {**h, 'id': 'val', 'plan_id': plan['plan_id'],
        'execution_attempt_id': plan['execution_attempt_id'], 'result': 'passed',
        'health_observations': [{'status': 'passed'} for _ in range(5)],
        'business_probes': [{'status': 'passed', 'id': 'same-problem'}],
        'database_guard': {'status': 'passed'}})
    put('recovery_receipt', 'rec', {**h, 'attempt_id': plan['execution_attempt_id'],
        'verification_report_id': plan['validation_report_id'], 'plan_id': plan['plan_id'],
        'status': 'recovered', 'business_probe_passed': True,
        'source_revision': plan['source_commit']})


def test_prepared_is_not_resolved(tmp_path):
    r, args, _, _ = setup_plan(tmp_path)
    assert r.advance_demo_resolution(*args)['state'] == 'awaiting_approval'


def test_missing_execution_or_recovery_cannot_resolve(tmp_path):
    r, args, _, _ = setup_plan(tmp_path, 'verified')
    with pytest.raises(ProductError, match='回执'):
        r.advance_demo_resolution(*args)
    assert r.get_incident(*args[:4])['state'] != 'resolved'


def test_proof_chain_closes_same_incident_and_rollback_reopens(tmp_path):
    r, args, plan, put = setup_plan(tmp_path, 'verified')
    add_proofs(plan, put)
    updated = r.advance_demo_resolution(*args)
    assert updated['state'] == 'resolved' and updated['outcome'] == 'resolved'
    assert r.advance_demo_resolution(*args)['revision'] == updated['revision']
    put('demo_resolution_plan', 'resolution-plan:p', {**plan, 'status': 'rolled_back'})
    assert r.advance_demo_resolution(*args)['state'] == 'investigating'


def test_stale_approval_cannot_authorize_new_hash(tmp_path):
    r, args, _, put = setup_plan(tmp_path, 'authorized')
    put('approval', 'ap', {'plan_sha256': 'c' * 64, 'decision': 'approved', 'execution_authorization': True})
    with pytest.raises(ProductError, match='绑定'):
        r.advance_demo_resolution(*args)


def test_failed_probe_cannot_be_hidden_by_passed_summary(tmp_path):
    r, args, plan, put = setup_plan(tmp_path, 'verified')
    add_proofs(plan, put)
    validation = r.get('validation_report', 'val', project_id=args[0], environment_id=args[2], incident_id=args[3])
    put('validation_report', 'val', {**validation, 'result': 'passed',
        'health_observations': [{'status': 'passed'} for _ in range(5)],
        'business_probes': [{'status': 'unknown'}], 'database_guard': {'status': 'passed'}})
    with pytest.raises(ProductError, match='独立业务验证'):
        r.advance_demo_resolution(*args)


@pytest.mark.parametrize('kind,field,value', [
    ('validation_report', 'execution_attempt_id', 'wrong-attempt'),
    ('recovery_receipt', 'attempt_id', 'wrong-attempt'),
    ('recovery_receipt', 'verification_report_id', 'wrong-report'),
])
def test_verified_proofs_must_reference_the_same_attempt_and_report(tmp_path, kind, field, value):
    r, args, plan, put = setup_plan(tmp_path, 'verified')
    add_proofs(plan, put)
    item_id = 'val' if kind == 'validation_report' else 'rec'
    item = r.get(kind, item_id, project_id=args[0], environment_id=args[2], incident_id=args[3])
    put(kind, item_id, {**item, field: value})
    with pytest.raises(ProductError, match='引用不一致'):
        r.advance_demo_resolution(*args)
