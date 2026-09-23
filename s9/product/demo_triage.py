"""Small, explicit observation rules; these are not an LLM root-cause verdict."""
from __future__ import annotations

import re


def classify_handoff(message: str, result: dict) -> dict:
    decision = result.get('policy_decision') or {}
    code = (decision.get('code') or decision.get('decision')) if isinstance(decision, dict) else decision
    expected = {'clarification_needed', 'human_requested', 'unsupported_request',
                'blocked_write', 'blocked_injection', 'unverified_identity'}
    trace = result.get('trace') or []
    if any(isinstance(t, dict) and t.get('tool') in {'initiate_refund', 'process_payment'}
           and isinstance(t.get('result'), dict) and t['result'].get('success') is True for t in trace):
        return {'state': 'unexpected_write', 'summary': '只读演示中出现业务写入成功回执，需要立即核查。',
                'auto_investigate': True, 'reason': 'write_receipt'}
    # Independently flag a conflict between a negative handoff instruction and
    # an unfulfilled lookup. The application's own safe-looking label is not proof.
    negated_handoff = re.search(
        r'(?:不要|不用|无需|不必|别).{0,18}(?:转.{0,3}人工|真人客服|人工客服)|'
        r"(?:do\s+not|don't|never).{0,35}(?:transfer|escalate|connect).{0,20}(?:human|agent|support)",
        message, re.I)
    lookup = re.search(r'订单|物流|快递|退款资格|能不能退|order|tracking|refund', message, re.I)
    queried = any(isinstance(t, dict) and t.get('tool') in
                  {'get_order_status', 'track_shipment', 'check_refund_eligibility'} for t in trace)
    if negated_handoff and lookup and not queried and (code == 'human_requested' or result.get('agent') == 'escalate'):
        return {'state': 'handoff_intent_conflict', 'summary': '问题中含有不要转人工的要求，但查询没有执行，需要核对是否误读了请求。',
                'auto_investigate': True, 'reason': 'negative_handoff_without_lookup'}
    if code in expected:
        return {'state': 'expected_boundary', 'summary': '助手说明了能力边界或需要补充的信息。',
                'auto_investigate': False, 'reason': code}
    if code == 'pipeline_error':
        return {'state': 'pipeline_error', 'summary': '业务处理过程发生异常，需检查实际失败的步骤。',
                'auto_investigate': True, 'reason': code}
    if result.get('agent') != 'escalate':
        return {'state': 'no_rule_finding', 'summary': '未触发转交异常规则，可按需人工核查。',
                'auto_investigate': False, 'reason': 'no_escalation'}
    # A user's request for human help is not itself a fault. No ticket is invented.
    if re.search(r'转.{0,3}人工|找.{0,3}人工|真人客服|human\s+(?:agent|support)|speak\s+to', message, re.I):
        return {'state': 'human_requested', 'summary': '用户要求人工协助；当前未接入人工受理队列。',
                'auto_investigate': False, 'reason': 'explicit_request'}
    in_domain = bool(re.search(r'物流|快递|订单|退款|shipping|tracking|order|refund', message, re.I))
    return {'state': 'unexpected_handoff' if in_domain else 'manual_check_available',
            'summary': '业务范围内的问题被转交，需核查路由或缺失信息。' if in_domain else '助手未能处理此问题，可由你决定是否调查。',
            'auto_investigate': in_domain, 'reason': 'in_domain_handoff' if in_domain else 'unclassified_handoff'}
