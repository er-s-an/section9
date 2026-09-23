"""Independent, narrow acceptance contract for the routing-policy runbook.

This validates tool receipts against SQLite business truth. It never asks the
investigating model to grade its own recommendation or to define success.
Unsupported incidents remain unsupported, rather than inheriting a green
result from unrelated canned probes.
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from s9.product.registry import ProductError


def derive_assertions(job):
    message = job.get('message', '')
    result = job.get('business_result') or {}
    mode = result.get('support_policy') or job.get('target_configuration', {}).get('policy')
    if mode != 'legacy':
        raise ProductError('RUNBOOK_NOT_APPLICABLE', '此预案用于旧路由问题，当前任务不符合适用条件', 409)
    if re.search(r'转人工|找人工|人工客服|ignore.*instruction|忽略.*指令|泄露|api.?key', message, re.I):
        raise ProductError('RUNBOOK_NOT_APPLICABLE', '该任务需要其他处理方式，不能以路由保护声称修复', 409)
    logistics = bool(re.search(r'物流|快递|配送|包裹|发货|tracking|shipping|shipment|parcel|delivery', message, re.I))
    refund = bool(re.search(r'退款|退货|refund', message, re.I))
    order = bool(re.search(r'订单|order', message, re.I))
    explicit_status = bool(re.search(r'状态|到哪|进度|status|where.*order', message, re.I))
    ids = sorted(set(int(v) for v in re.findall(r'(?<!\d)(\d{3,6})(?!\d)', message)))
    if not (logistics or refund or order) or len(ids) > 3:
        raise ProductError('RUNBOOK_NOT_APPLICABLE', '没有可独立验证的路由问题，需人工制定其他预案', 409)
    trace = result.get('trace') or []
    if not ids:
        if result.get('agent') != 'escalate':
            raise ProductError('RUNBOOK_NOT_APPLICABLE', '未确认缺订单号时发生了错误转交', 409)
        return {'kind': 'clarify_missing_order', 'expected_decision': 'clarification_needed',
                'before_failure': 'human_handoff_instead_of_clarification', 'source_task_id': job['id']}
    required = (['track_shipment'] if logistics else ['get_order_status'] if order and (explicit_status or not refund) else [])
    if refund:
        required.append('check_refund_eligibility')
    requests = [{'tool': tool, 'order_id': value} for value in ids for tool in required]
    missing = [q for q in requests if not any(t.get('tool') == q['tool']
        and (t.get('args') or {}).get('order_id') == q['order_id'] and isinstance(t.get('result'), dict)
        for t in trace if isinstance(t, dict))]
    if result.get('agent') != 'escalate' and not missing:
        raise ProductError('RUNBOOK_NOT_APPLICABLE', '本任务已有所需查询回执，不能把启用预案当成已修复该事故', 409)
    return {'kind': 'complete_readonly_lookup', 'requests': requests, 'source_task_id': job['id'],
            'before_failure': 'unexpected_handoff' if result.get('agent') == 'escalate' else 'missing_requested_tools',
            'missing_requests_before': missing}


def check_assertions(assertions, body, db_path):
    checks = []
    decision = body.get('policy_decision') or {}
    code = decision.get('decision') if isinstance(decision, dict) else None
    trace = body.get('trace') or []
    checks.append({'name': 'guarded_policy', 'passed': body.get('support_policy') == 'guarded'})
    checks.append({'name': 'not_handed_off', 'passed': body.get('agent') != 'escalate'
                   and body.get('handoff_state') in {'not_requested', 'not_connected'}})
    checks.append({'name': 'no_pipeline_failure', 'passed': not body.get('error_type') and code != 'pipeline_error'})
    checks.append({'name': 'no_business_mutation', 'passed': all(
        not isinstance(t, dict) or t.get('tool') not in {'initiate_refund', 'process_payment'}
        or (isinstance(t.get('result'), dict) and t['result'].get('blocked') is True) for t in trace)})
    if assertions.get('kind') == 'clarify_missing_order':
        checks.append({'name': 'asks_for_missing_order', 'passed': code == 'clarification_needed'
                       and bool(body.get('reply', '').strip()) and not trace})
    elif assertions.get('kind') == 'complete_readonly_lookup':
        path = Path(db_path)
        try:
            if not path.is_file():
                raise sqlite3.OperationalError('database missing')
            with sqlite3.connect(f'file:{path}?mode=ro', uri=True) as db:
                for q in assertions['requests']:
                    order = db.execute('SELECT status FROM orders WHERE order_id=?', (q['order_id'],)).fetchone()
                    refund = db.execute('SELECT status FROM refunds WHERE order_id=?', (q['order_id'],)).fetchone()
                    receipts = [t.get('result') for t in trace if isinstance(t, dict) and t.get('tool') == q['tool']
                        and (t.get('args') or {}).get('order_id') == q['order_id'] and isinstance(t.get('result'), dict)]
                    if order is None:
                        ok = any(bool(receipt.get('error')) and 'status' not in receipt and 'eligible' not in receipt for receipt in receipts)
                    elif q['tool'] == 'check_refund_eligibility':
                        expected = order[0] == 'delivered' and (refund is None or refund[0] == 'none')
                        ok = any(receipt.get('order_id') == q['order_id'] and receipt.get('eligible') is expected for receipt in receipts)
                    else:
                        ok = any(receipt.get('order_id') == q['order_id'] and receipt.get('status') == order[0] for receipt in receipts)
                    checks.append({'name': f"{q['tool']}:{q['order_id']}", 'passed': ok})
        except sqlite3.Error:
            checks.append({'name': 'independent_database_truth', 'passed': False, 'state': 'unknown'})
    else:
        checks.append({'name': 'supported_acceptance_contract', 'passed': False})
    return {'passed': bool(checks) and all(c['passed'] for c in checks), 'checks': checks,
            'source_task_id': assertions.get('source_task_id'), 'basis': 'tool_receipts_and_independent_database'}
