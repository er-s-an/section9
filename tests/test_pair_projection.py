from s9.pairs.projection import project_arm


def ev(n, typ, *, arm="baseline", scope="run", run="r1", pair="p1", producer="single", **payload):
    return {"event_id": f"e{n}", "sequence": n, "scope": scope, "pair_id": pair, "run_id": run,
            "arm": arm, "producer": producer, "event_type": typ, "occurred_at": f"2026-01-01T00:00:0{n}Z",
            "payload": payload}


def project(events, *, arm="baseline", usage=None, agents=None, run=None):
    return project_arm({"pair_id": "p1", "immutable_spec": {"baseline_config": {"x": 1}}}, arm,
                       run or {"id": "r1", "run_id": "r1", "status": "running"}, events,
                       config={"x": 2, "revision": "9", "generation": "4"}, agents=agents or [], usage=usage or [],
                       dependencies={}, as_of_sequence=99)


def test_wrong_arm_and_system_null_run_do_not_become_activity():
    out = project([ev(1, "observation.probe", arm="injected", signal="quality_mismatch"),
                   ev(2, "observation.probe", scope="system", run=None, signal="quality_mismatch")])
    assert out["events"] == []
    assert out["stages"][1]["status"] == "pending"


def test_failed_verification_is_visible_as_failed():
    out = project([ev(1, "verification.completed", producer="verifier", passed=False, checks=[{"name": "x", "passed": False}])])
    assert out["stages"][5]["status"] == "failed"
    assert out["verification"]["passed"] is False


def test_baseline_uses_one_actual_decision_for_combined_diagnose_and_plan():
    out = project([ev(1, "plan.proposed", producer="single", purpose="single", summary="one call")])
    assert out["stages"][2]["status"] == out["stages"][3]["status"] == "combined"
    assert out["stages"][2]["source_event_ids"] == out["stages"][3]["source_event_ids"] == ["e1"]


def test_queued_request_is_pending_and_unknown_usage_is_preserved():
    out = project([ev(1, "model.queued", producer="single", purpose="single", request_id="q1", reserved_tokens=40)],
                  usage=[{"request_id": "q0", "usage_unknown": True}])
    assert out["usage"]["pending_tokens"] == 40
    assert out["usage"]["unknown_count"] == 1
    assert out["usage"]["unknown_tokens"] == 0


def test_agent_activity_references_last_own_event_and_idle_has_no_source():
    out = project([ev(1, "dialog.received", arm="swarm", producer="diagnoser", purpose="diagnose", summary="evidence")], arm="swarm",
                  agents=[{"id": "diagnoser", "name": "诊断员", "role": "diagnoser"},
                          {"id": "verifier", "name": "复核员", "role": "verifier"}])
    by_id = {a["id"]: a for a in out["agents"]}
    assert by_id["diagnoser"]["source_event_id"] == "e1"
    assert by_id["diagnoser"]["position"] == "meeting"
    assert by_id["verifier"]["status"] == "idle"
    assert by_id["verifier"]["source_event_id"] is None


def test_baseline_excludes_deterministic_sentry_from_reasoning_actors():
    result = project([], agents=[{'id': 'sentry'}, {'id': 'single'}])
    assert [a['id'] for a in result['agents']] == ['single']


def test_pending_reservation_is_not_unknown_usage():
    result = project([], usage=[{'id': 'u', 'status': 'reserved', 'reservation': 100}],
                     run={'id': 'r1', 'run_id': 'r1', 'reserved_tokens': 100})
    assert result['usage']['unknown_count'] == 0
    assert result['usage']['pending_tokens'] == 100


def test_raw_model_response_without_accepted_plan_is_not_combined_success():
    result = project([ev(1, 'model.completed', purpose='single', returned_model='test')])
    assert result['stages'][2]['status'] == 'pending'
    assert result['stages'][3]['status'] == 'running'


def test_waiting_approval_and_verification_evidence_are_preserved():
    result = project([ev(1, 'grant.rejected', code='APPROVAL_REQUIRED'),
                      ev(2, 'verification.completed', passed=False, checks=[{'passed': False}])])
    assert result['stages'][4]['status'] == 'waiting'
    assert result['rca'][-1]['checks'] == [{'passed': False}]


def test_preparation_does_not_display_fault_injection_as_started():
    prepared = project([ev(1, 'arm.prepared')])
    assert prepared['stages'][0]['status'] == 'pending'
    assert prepared['stages'][0]['source_event_ids'] == []
    injected = project([ev(1, 'chaos.injected')])
    assert injected['stages'][0]['status'] == 'passed'
