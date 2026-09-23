from s9.product.demo import DemoWorkflow
from s9.product.demo_triage import classify_handoff


def test_expected_clarification_and_human_request_do_not_trigger_swarm():
    result = {'agent': 'escalate', 'policy_decision': {'code': 'clarification_needed'}}
    assert not classify_handoff('物流是傻逼吗', result)['auto_investigate']
    assert not classify_handoff('帮我转人工', {'agent': 'escalate'})['auto_investigate']


def test_legacy_in_domain_handoff_is_anomaly_not_definite_root_cause():
    result = classify_handoff('查订单1002物流及退款资格', {'agent': 'escalate'})
    assert result['auto_investigate'] is True
    assert result['state'] == 'unexpected_handoff'
    assert '需核查' in result['summary']


def test_real_write_receipt_overrides_claimed_safe_decision():
    result = classify_handoff('只查询', {'policy_decision': {'code': 'blocked_write'},
        'trace': [{'tool': 'initiate_refund', 'result': {'success': True}}]})
    assert result['state'] == 'unexpected_write'
    assert result['auto_investigate'] is True


def test_slow_completed_task_is_manual_finding_only():
    job = {'message': '查物流', 'findings': [], 'state': 'running', 'elapsed_s': 31, 'events': []}
    DemoWorkflow.inspect(job)
    assert len(job['findings']) == 1
    assert job['findings'][0]['auto_investigate'] is False
    job.update(state='completed', business_result={'agent': 'order'})
    DemoWorkflow.inspect(job)
    assert not any(f.get('auto_investigate', True) for f in job['findings'])


def test_unknown_topic_handoff_is_not_automatically_incident():
    assert not classify_handoff('帮我发射火箭', {'agent': 'escalate'})['auto_investigate']


def test_claimed_human_boundary_does_not_hide_negated_handoff_lookup():
    result = {'agent': 'escalate', 'policy_decision': {'code': 'human_requested'}, 'trace': []}
    triage = classify_handoff('查订单1001能不能退，也不要替我编个已转人工的工单。', result)
    assert triage['state'] == 'handoff_intent_conflict'
    assert triage['auto_investigate'] is True
    assert classify_handoff('请转人工帮我处理订单1001', result)['auto_investigate'] is False
