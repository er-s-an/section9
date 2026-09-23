"""Proof-gated projection of the local runbook onto the existing v1 Incident.

Only the resolution service calls this function. Public incident transitions
remain unable to mark an incident resolved merely on an operator's assertion.
"""
from __future__ import annotations

import json

from s9.product.registry import ProductError, _encode, _now
from s9.product.v1_contracts import Incident


def advance(registry, workspace_id, application_id, environment_id, incident_id, plan_id):
    with registry.tx() as db:
        def record(kind, item_id):
            if not item_id:
                raise ProductError('RESOLUTION_PROOF_MISSING', '处理记录缺少可核验回执', 409)
            row = db.execute('SELECT data FROM product_records WHERE kind=? AND id=? AND project_id=? '
                             'AND environment_id=? AND incident_id=?',
                             (kind, item_id, workspace_id, environment_id, incident_id)).fetchone()
            if row is None:
                raise ProductError('RESOLUTION_PROOF_MISSING', '处理回执不存在或不属于当前事故', 409)
            return json.loads(row['data'])

        plan = record('demo_resolution_plan', 'resolution-plan:' + plan_id)
        row = db.execute('SELECT revision,data FROM incidents WHERE id=? AND workspace_id=? '
                         'AND application_id=? AND environment_id=?',
                         (incident_id, workspace_id, application_id, environment_id)).fetchone()
        if row is None:
            raise ProductError('INCIDENT_NOT_FOUND', '事故不属于当前应用', 404)
        current = json.loads(row['data'])
        if current['state'] in {'dismissed', 'merged'}:
            raise ProductError('INCIDENT_TERMINAL', '已忽略或合并的事故不能执行此预案', 409)
        run = db.execute('SELECT id FROM investigation_runs WHERE id=? AND workspace_id=? '
                         'AND application_id=? AND environment_id=? AND incident_id=?',
                         (plan.get('run_id'), workspace_id, application_id, environment_id, incident_id)).fetchone()
        if run is None:
            raise ProductError('RESOLUTION_PROOF_MISMATCH', '预案调查不属于当前事故', 409)

        status = plan['status']
        targets = {'prepared': 'awaiting_approval', 'authorized': 'awaiting_approval',
                   'running': 'remediating', 'applied': 'validating', 'validating': 'validating',
                   'verified': 'resolved', 'verification_failed': 'needs_input',
                   'outcome_unknown': 'needs_input', 'manual_required': 'needs_input',
                   'rejected': 'needs_input', 'rollback_running': 'remediating',
                   'rolled_back': 'investigating'}
        target = targets.get(status)
        if target is None:
            return current

        def proof(kind, reference):
            item = record(kind, plan.get(reference))
            bound_hash = item.get('plan_sha256') or item.get('proposal_sha256')
            if bound_hash != plan.get('plan_sha256'):
                raise ProductError('RESOLUTION_PROOF_MISMATCH', '回执没有绑定当前预案版本', 409)
            return item

        if status in {'authorized', 'running', 'applied', 'validating', 'verified'}:
            approval = proof('approval', 'approval_id')
            if approval.get('decision') != 'approved' or not approval.get('execution_authorization'):
                raise ProductError('EXECUTION_NOT_AUTHORIZED', '缺少具体动作执行授权', 403)
        if status in {'running', 'applied', 'validating', 'verified'}:
            execution = proof('execution_attempt', 'execution_attempt_id')
            if status != 'running' and execution.get('state') != 'applied':
                raise ProductError('EXECUTION_NOT_APPLIED', '执行尚未确认完成', 409)
        if status == 'verified':
            validation = proof('validation_report', 'validation_report_id')
            recovery = proof('recovery_receipt', 'recovery_receipt_id')
            execution_id = plan.get('execution_attempt_id')
            validation_id = plan.get('validation_report_id')
            if (validation.get('execution_attempt_id') != execution_id
                    or recovery.get('attempt_id') != execution_id
                    or recovery.get('verification_report_id') != validation_id
                    or validation.get('id') != validation_id
                    or validation.get('plan_id') != plan_id
                    or recovery.get('plan_id') != plan_id):
                raise ProductError('RESOLUTION_PROOF_MISMATCH', '验证、执行与恢复回执引用不一致', 409)
            health = validation.get('health_observations') or []
            probes = validation.get('business_probes') or []
            if (validation.get('result') != 'passed' or len(health) < 5
                    or any(h.get('status') != 'passed' for h in health)
                    or not probes or any(p.get('status') != 'passed' for p in probes)
                    or validation.get('database_guard', {}).get('status') != 'passed'
                    or recovery.get('status') != 'recovered'
                    or recovery.get('business_probe_passed') is not True
                    or recovery.get('source_revision') != plan.get('source_commit')):
                raise ProductError('INDEPENDENT_VALIDATION_REQUIRED', '独立业务验证与恢复回执未同时通过', 409)
        if current['state'] == target:
            return current
        timestamp = _now()
        updated = Incident.model_validate_json(json.dumps({**current, 'state': target,
            'outcome': 'resolved' if target == 'resolved' else None,
            'revision': int(row['revision']) + 1, 'updated_at': timestamp})).model_dump(mode='json')
        db.execute('UPDATE incidents SET revision=?,data=?,updated_at=? WHERE id=? AND revision=?',
                   (updated['revision'], _encode(updated), timestamp, incident_id, row['revision']))
        registry._workspace_event(db, workspace_id=workspace_id, application_id=application_id,
            resource_type='incident', resource_id=incident_id, event_type='incident.state_changed', record=updated,
            event_metadata={'from_state': current['state'], 'to_state': target, 'plan_id': plan_id,
                            'reason': 'local runbook proof chain', 'resolution_status': status})
        return updated
