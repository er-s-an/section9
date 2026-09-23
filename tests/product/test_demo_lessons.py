from types import SimpleNamespace

import pytest

from s9.product.demo_lessons import relevant_lessons, review_lesson
from s9.product.registry import ProductError, ProductRegistry


def workflow(tmp_path):
    registry = ProductRegistry(tmp_path / 'registry.sqlite')
    service = SimpleNamespace(registry=registry, wid='w', aid='a', env='local-test')
    service.get = lambda key: registry.get('business_task', key, project_id='w', environment_id='local-test')
    service.save = lambda value: registry.put('business_task', value['id'], value, project_id='w', environment_id='local-test')
    candidate = {'id': 'candidate', 'plan_sha256': 'a' * 64}
    plan = {'status': 'verified', 'plan_id': 'p', 'plan_sha256': 'a' * 64, 'source_commit': 'b' * 40,
            'new_policy': 'guarded', 'lesson_candidate': candidate, 'validation_report_id': 'validation',
            'recovery_receipt_id': 'recovery'}
    scope = {'project_id': 'w', 'environment_id': 'local-test', 'incident_id': 'i'}
    registry.put('lesson_candidate', 'candidate', candidate, **scope)
    registry.put('demo_resolution_plan', 'resolution-plan:p', plan, **scope)
    service.save({'id': 'job', 'incident_id': 'i', 'resolution': plan})
    return service, scope


def test_review_is_bound_and_lesson_reuse_stops_after_rollback(tmp_path):
    service, scope = workflow(tmp_path)
    incoming = {'message': '查询订单1002物流', 'source_commit': 'b' * 40,
                'target_configuration': {'policy': 'guarded'}}
    assert relevant_lessons(service, incoming) == []
    review_lesson(service, 'job', 'a' * 64, 'approved', '独立验证通过')
    lesson = relevant_lessons(service, incoming)[0]
    assert lesson['source_incident_id'] == 'i'
    assert lesson['use'] == 'reviewed_historical_reference_not_current_evidence'
    assert relevant_lessons(service, {**incoming, 'source_commit': 'c' * 40}) == []
    plan = service.registry.get('demo_resolution_plan', 'resolution-plan:p', **scope)
    service.registry.put('demo_resolution_plan', 'resolution-plan:p', {**plan, 'status': 'rolled_back'}, **scope)
    assert relevant_lessons(service, incoming) == []
    with pytest.raises(ProductError, match='过期经验'):
        review_lesson(service, 'job', 'a' * 64, 'approved', '独立验证通过')


def test_wrong_plan_cannot_review_and_rejection_is_not_reused(tmp_path):
    service, _ = workflow(tmp_path)
    with pytest.raises(ProductError):
        review_lesson(service, 'job', 'f' * 64, 'approved', 'wrong')
    review_lesson(service, 'job', 'a' * 64, 'rejected', '尚不足以泛化')
    assert relevant_lessons(service, {'message': '订单', 'source_commit': 'b' * 40,
                                     'target_configuration': {'policy': 'guarded'}}) == []
