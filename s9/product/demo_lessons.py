"""Operator-reviewed, scoped lessons backed by the same incident's receipts."""
from __future__ import annotations

import re

from s9.product.registry import ProductError


def review_lesson(workflow, job_id, plan_sha256, decision, reason):
    from s9.product.demo import now
    job = workflow.get(job_id)
    resolution = job.get('resolution') or {}
    candidate = resolution.get('lesson_candidate') or {}
    if resolution.get('status') != 'verified' or resolution.get('plan_sha256') != plan_sha256 or not candidate.get('id'):
        raise ProductError('VERIFIED_LESSON_REQUIRED', '先完成同一预案的业务验证，再审核经验', 409)
    if decision not in {'approved', 'rejected'} or not reason.strip():
        raise ProductError('LESSON_REVIEW_REQUIRED', '请填写经验审核决定及理由', 422)
    scope = {'project_id': workflow.wid, 'environment_id': workflow.env, 'incident_id': job['incident_id']}
    record = workflow.registry.get('lesson_candidate', candidate['id'], **scope)
    if not record or record.get('plan_sha256') != plan_sha256:
        raise ProductError('LESSON_VERSION_CHANGED', '经验候选已变化，请刷新后审核', 409)
    plan = workflow.registry.get('demo_resolution_plan', 'resolution-plan:' + resolution['plan_id'], **scope)
    if not plan or plan.get('status') != 'verified' or plan.get('plan_sha256') != plan_sha256:
        raise ProductError('VERIFIED_LESSON_REQUIRED', '预案验证已变化，不能保存过期经验', 409)
    review_id = 'lesson-review:' + resolution['plan_id']
    prior = workflow.registry.get('lesson_review', review_id, **scope)
    if prior:
        if prior['decision'] != decision or prior['reason'] != reason.strip():
            raise ProductError('LESSON_ALREADY_REVIEWED', '该经验已有审核记录', 409)
        return {'resolution': resolution}
    review = {'decision': decision, 'reason': reason.strip(), 'at': now(), 'actor': 'local-operator',
              'application_id': workflow.aid, 'source_commit': resolution['source_commit'],
              'plan_sha256': plan_sha256, 'plan_id': resolution['plan_id'], 'candidate_id': candidate['id'],
              'title': '客服路由保护的本地验证经验',
              'summary': '此版本在启用路由保护后通过了记录中的业务检查。后续调查仍需核对现行策略、问题表现和证据，不能据此假定同一根因。',
              'validation_report_id': resolution.get('validation_report_id'),
              'recovery_receipt_id': resolution.get('recovery_receipt_id')}
    workflow.registry.put('lesson_review', review_id, review, **scope)
    job['resolution']['lesson_candidate']['review'] = review
    workflow.save(job)
    workflow.registry.event('lesson.reviewed', {'review_id': review_id, 'decision': decision, 'plan_sha256': plan_sha256}, **scope)
    return {'resolution': job['resolution']}


def relevant_lessons(workflow, job):
    if not re.search(r'物流|订单|退款|快递|shipping|tracking|order|refund', job.get('message', ''), re.I):
        return []
    records = workflow.registry.list('lesson_review', project_id=workflow.wid, environment_id=workflow.env)
    result = []
    for item in reversed(records):
        if (item.get('decision') != 'approved' or item.get('application_id') != workflow.aid
                or item.get('source_commit') != job.get('source_commit')):
            continue
        scope = {'project_id': workflow.wid, 'environment_id': workflow.env, 'incident_id': item['incident_id']}
        plan = workflow.registry.get('demo_resolution_plan', 'resolution-plan:' + item['plan_id'], **scope)
        if not plan or plan.get('status') != 'verified' or plan.get('plan_sha256') != item['plan_sha256']:
            continue
        if job.get('target_configuration', {}).get('policy') != plan.get('new_policy'):
            continue
        result.append({'id': item['id'], 'title': item['title'], 'summary': item['summary'],
                       'source_incident_id': item['incident_id'], 'validation_report_id': item['validation_report_id'],
                       'scope': {'application_id': workflow.aid, 'environment_id': workflow.env,
                                 'source_commit': item['source_commit'], 'policy': plan['new_policy']},
                       'use': 'reviewed_historical_reference_not_current_evidence'})
        if len(result) == 3:
            break
    return result
