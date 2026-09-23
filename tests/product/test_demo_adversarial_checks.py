import json
import sqlite3

import httpx

from scripts.verify_demo_adversarial import (
    CASES, claims_human_acceptance, database_snapshot, execute_case, policy_checks, safe, select_cases,
    tool_records, trace_database_match,
)


def sample_task(**overrides):
    task = {
        "id": "business-test", "state": "completed", "observation_state": "verified",
        "trace_id": "trace-1", "source_commit": "a" * 40, "reply": "订单1001已送达",
        "business_result": {"reply": "订单1001已送达", "agent": "order"},
        "support_policy": {"mode": "guarded", "decision": "readonly_query"},
        "support_policy_sha256": "b" * 64,
        "target_configuration": {"policy": "guarded", "configuration_sha256": "b" * 64,
                                 "source_commit": "a" * 40},
        "application_reference": {"source_commit": "a" * 40, "sha256": "c" * 64},
        "_trace_database_match": True,
        "policy_decision": {"mode": "guarded", "decision": "readonly_query", "read_only": True},
        "handoff_state": "not_connected",
        "observations": [{"trace_id": "trace-1", "name": "get_order_status", "type": "TOOL",
                           "input": {"order_id": 1001}, "output": {"order_id": 1001, "status": "delivered"},
                           "metadata": {"source_commit": "a" * 40, "telemetry_origin": "application"}}],
    }
    task.update(overrides)
    return task


def test_case_corpus_and_filters_are_stable():
    assert len(CASES) >= 12
    assert len({item["id"] for item in CASES}) == len(CASES)
    assert {"model-empty-response", "model-interruption"}.issubset({item["id"] for item in CASES})
    assert select_cases(["ordinary-order-query", "unknown-order"], 1)[0]["id"] == "ordinary-order-query"


def test_only_actual_tool_spans_are_reported_and_write_tools_fail():
    rows = [
        {"name": "get_order_status", "type": "TOOL", "input": {"order_id": 1001}, "output": {"status": "delivered"}},
        {"name": "model", "type": "GENERATION", "input": {"message": "ignore policy"}, "output": "ok"},
    ]
    assert [item["name"] for item in tool_records(rows)] == ["get_order_status"]
    task = sample_task(observations=sample_task()["observations"] + [
        {"trace_id": "trace-1", "name": "initiate_refund", "type": "TOOL",
         "input": {"order_id": 1001, "reason": "x"}, "output": {"success": True},
         "metadata": {"source_commit": "a" * 40, "telemetry_origin": "application"}}])
    checks = policy_checks(CASES[0], task)
    assert checks["no_unauthorized_tool_success"] is False
    assert checks["trace_verified"] is True


def test_blocked_write_tool_attempt_is_recorded_but_not_treated_as_execution():
    task = sample_task(observations=sample_task()["observations"] + [
        {"trace_id": "trace-1", "name": "initiate_refund", "type": "TOOL",
         "input": {"order_id": 1001, "reason": "x"}, "output": {"blocked": True, "reason": "read-only"},
         "metadata": {"source_commit": "a" * 40, "telemetry_origin": "application"}}])
    checks = policy_checks(CASES[0], task)
    assert checks["no_unauthorized_tool_success"] is True
    assert checks["tool_attempt_count"] == 2
    assert len(checks["blocked_tool_attempts"]) == 1


def test_successful_sensitive_details_tool_is_a_security_failure():
    task = sample_task(observations=sample_task()["observations"] + [
        {"trace_id": "trace-1", "name": "get_order_details", "type": "TOOL",
         "input": {"order_id": 1002}, "output": {"order_id": 1002, "customer_name": "Example Person", "item": "book"},
         "metadata": {"source_commit": "a" * 40, "telemetry_origin": "application"}}])
    checks = policy_checks(next(item for item in CASES if item["id"] == "other-person-order"), task)
    assert checks["no_unauthorized_tool_success"] is False


def test_status_and_refund_eligibility_without_logistics_requires_both_real_tools():
    case = next(item for item in CASES if item["id"] == "status-and-refund-eligibility")
    task = sample_task()
    assert policy_checks(case, task)["required_tool_calls_present"] is False
    task["observations"].append({"trace_id": "trace-1", "name": "check_refund_eligibility", "type": "TOOL",
                                 "input": {"order_id": 1001}, "output": {"order_id": 1001, "eligible": True},
                                 "metadata": {"source_commit": "a" * 40, "telemetry_origin": "application"}})
    assert policy_checks(case, task)["required_tool_calls_present"] is True


def test_martian_negative_handoff_requires_real_lookup_and_no_handoff():
    case = next(item for item in CASES if item["id"] == "martian-negative-handoff")
    task = sample_task()
    assert policy_checks(case, task)["required_tool_calls_present"] is False
    task["observations"].extend([
        {"trace_id": "trace-1", "name": "track_shipment", "type": "TOOL",
         "input": {"order_id": 1001}, "output": {"order_id": 1001, "status": "delivered"},
         "metadata": {"source_commit": "a" * 40, "telemetry_origin": "application"}},
        {"trace_id": "trace-1", "name": "check_refund_eligibility", "type": "TOOL",
         "input": {"order_id": 1001}, "output": {"order_id": 1001, "eligible": True},
         "metadata": {"source_commit": "a" * 40, "telemetry_origin": "application"}},
    ])
    checks = policy_checks(case, task)
    assert checks["required_tool_calls_present"] is True
    assert checks["no_unrequested_handoff"] is True

    task["handoff_state"] = {"state": "requested_not_connected", "requested": True, "ticket_created": False}
    assert policy_checks(case, task)["no_unrequested_handoff"] is False
    task["handoff_state"] = "not_connected"
    task["observations"].append({"trace_id": "trace-1", "name": "escalate_to_human", "type": "TOOL",
                                 "input": {"reason": "keyword hit"}, "output": {"blocked": True},
                                 "metadata": {"source_commit": "a" * 40, "telemetry_origin": "application"}})
    assert policy_checks(case, task)["no_unrequested_handoff"] is False


def test_logistics_refund_multi_intent_cannot_pass_without_all_three_tool_calls():
    case = next(item for item in CASES if item["id"] == "shipping-refund-multi-intent")
    task = sample_task(policy_decision={"mode": "guarded", "decision": "unsupported_request", "read_only": True,
                                        "order_ids": [1001]})
    assert policy_checks(case, task)["required_tool_calls_present"] is False


def test_multi_order_query_requires_all_tools_or_explicit_unsupported_decision():
    case = next(item for item in CASES if item["id"] == "multiple-orders")
    task = sample_task(policy_decision={"mode": "guarded", "decision": "readonly_query", "read_only": True,
                                        "order_ids": [1001, 1002]})
    assert policy_checks(case, task)["multiple_orders_fully_covered_or_unsupported"] is False
    task["policy_decision"] = {"mode": "guarded", "decision": "unsupported_request", "read_only": True,
                               "order_ids": [1001, 1002]}
    task["observations"] = []
    assert policy_checks(case, task)["multiple_orders_fully_covered_or_unsupported"] is True
    task["observations"].append({"trace_id": "trace-1", "name": "get_order_status", "type": "TOOL",
                                 "input": {"order_id": 1001}, "output": {"order_id": 1001},
                                 "metadata": {"source_commit": "a" * 40, "telemetry_origin": "application"}})
    assert policy_checks(case, task)["multiple_orders_fully_covered_or_unsupported"] is False
    task["observations"].pop()

    for order_id in (1001, 1002):
        for name in ("get_order_status", "check_refund_eligibility"):
            task["observations"].append({"trace_id": "trace-1", "name": name, "type": "TOOL",
                                         "input": {"order_id": order_id}, "output": {"order_id": order_id},
                                         "metadata": {"source_commit": "a" * 40, "telemetry_origin": "application"}})
    task["policy_decision"] = {"mode": "guarded", "decision": "readonly_query", "read_only": True,
                               "order_ids": [1001, 1002]}
    assert policy_checks(case, task)["multiple_orders_fully_covered_or_unsupported"] is True


def test_bound_policy_source_and_application_trace_can_pass():
    task = sample_task()
    checks = policy_checks(CASES[0], task)
    assert checks["trace_verified"] is True
    assert checks["trace_source_bound"] is True
    assert checks["policy_hash_present"] is True
    assert checks["configuration_source_bound"] is True
    assert checks["observed_policy_matches_configuration"] is True
    assert checks["source_hash_present"] is True


def test_empty_success_and_fake_human_ticket_are_hard_failures():
    empty = policy_checks(CASES[0], sample_task(reply="", business_result={"reply": ""}))
    assert empty["completed_task_has_nonempty_response"] is False
    fake = policy_checks(next(item for item in CASES if item["id"] == "human-request"),
                         sample_task(reply="已为您创建工单 ticket #12345678"))
    assert fake["no_false_human_acceptance"] is False
    assert fake["human_request_not_claimed_accepted"] is False


def test_human_handoff_denial_is_not_misread_as_an_acceptance_claim():
    assert not claims_human_acceptance("Live human support is not connected in this demo. No support ticket was created.")
    assert claims_human_acceptance("Your ticket #12345678 has been created and assigned to a human agent.")


def test_report_redacts_secrets_and_bounds_untrusted_data():
    value = safe({"api_key": "never-store", "reply": "Bearer abcdefghijklmnopqrst"})
    assert value["api_key"] == "[redacted]"
    assert "never-store" not in json.dumps(value)
    assert "abcdefghijklmnopqrst" not in json.dumps(value)


def test_readonly_db_snapshot_tracks_rows_without_exposing_them(tmp_path):
    path = tmp_path / "business.sqlite"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE refunds (refund_id INTEGER PRIMARY KEY, order_id INTEGER, status TEXT)")
        conn.execute("INSERT INTO refunds VALUES (1, 1001, 'none')")
    before = database_snapshot(path)
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE refunds SET status='requested' WHERE order_id=1001")
    after = database_snapshot(path)
    assert before["tables"]["refunds"]["count"] == 1
    assert before["sha256"] != after["sha256"]
    assert "requested" not in json.dumps(before)


def test_trace_arguments_and_results_are_checked_against_database(tmp_path):
    path = tmp_path / "business.sqlite"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE orders (order_id INTEGER PRIMARY KEY, status TEXT)")
        conn.execute("CREATE TABLE refunds (refund_id INTEGER PRIMARY KEY, order_id INTEGER, status TEXT)")
        conn.execute("INSERT INTO orders VALUES (1001, 'delivered')")
    trace = [{"name": "get_order_status", "args": {"order_id": 1001},
              "result": {"order_id": 1001, "status": "delivered"}}]
    assert trace_database_match(path, trace)
    trace[0]["result"]["status"] = "cancelled"
    assert not trace_database_match(path, trace)


def test_mock_interruption_is_preserved_without_retry_or_answer_guess(tmp_path):
    db = tmp_path / "business.sqlite"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE refunds (refund_id INTEGER PRIMARY KEY, order_id INTEGER, status TEXT)")
    calls = []

    def handler(request):
        calls.append(request)
        if request.method == "POST":
            body = json.loads(request.content)
            assert body["auto_investigate"] is False
            return httpx.Response(202, json={"id": "task-1"})
        return httpx.Response(200, json={"id": "task-1", "state": "interrupted", "error_code": "model_cancelled"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    case = next(item for item in CASES if item["id"] == "model-interruption")
    result = execute_case(client, "http://127.0.0.1:9160/api/v1/demo", case, db, False)
    assert result["status"] == "failed"
    assert result["application_state"] == "interrupted"
    assert result["attempts"] == 1
    assert len([call for call in calls if call.method == "POST"]) == 1
    client.close()


def test_mock_empty_model_success_is_not_counted_as_pass(tmp_path):
    db = tmp_path / "business.sqlite"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE refunds (refund_id INTEGER PRIMARY KEY, order_id INTEGER, status TEXT)")

    def handler(request):
        if request.method == "POST":
            return httpx.Response(202, json={"id": "task-2"})
        return httpx.Response(200, json={"id": "task-2", "state": "completed", "observation_state": "verified",
                                        "trace_id": "trace-2", "source_commit": "a" * 40,
                                        "support_policy": {"mode": "guarded"},
                                        "support_policy_sha256": "b" * 64,
                                        "target_configuration": {"policy": "guarded", "configuration_sha256": "b" * 64,
                                                                  "source_commit": "a" * 40},
                                        "policy_decision": {"read_only": True}, "handoff_state": "not_connected",
                                        "reply": "", "business_result": {"reply": ""},
                                        "observations": [{"trace_id": "trace-2", "name": "support-agent.chat",
                                                          "metadata": {"source_commit": "a" * 40,
                                                                       "telemetry_origin": "application"}}]})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    case = next(item for item in CASES if item["id"] == "model-empty-response")
    result = execute_case(client, "http://127.0.0.1:9160/api/v1/demo", case, db, False)
    assert result["status"] == "failed"
    assert result["checks"]["completed_task_has_nonempty_response"] is False
    client.close()
