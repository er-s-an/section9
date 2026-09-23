import json

import pytest

from s9.product.registry import ProductError, ProductRegistry


def _scope(registry, label="one"):
    workspace = registry.create_workspace(f"Workspace {label}", idempotency_key=f"{label}-workspace")
    application = registry.create_application(workspace["id"], f"App {label}",
        idempotency_key=f"{label}-application")
    connection = registry.create_connection(workspace["id"], application["id"], "Langfuse",
        idempotency_key=f"{label}-connection")
    binding = registry.create_scope_binding(workspace["id"], application["id"], "prod", connection["id"],
        "langfuse_project", f"project-{label}", idempotency_key=f"{label}-binding")
    return workspace, application, connection, binding


def _signal(source_id, *, dedup=None, source_version="v1", source_kind="poll",
            occurred_at="2026-09-23T09:00:00Z", observed_at="2026-09-23T10:00:00Z"):
    return {
        "source_id": source_id,
        "source_version": source_version,
        "deduplication_key": dedup or f"langfuse:{source_id}",
        "signal_type": "langfuse_observation",
        "source_kind": source_kind,
        "occurred_at": occurred_at,
        "observed_at": observed_at,
        "summary": "Langfuse observation captured",
    }


def _ingest(registry, scope, records, key):
    workspace, application, _, binding = scope
    return registry.ingest_signals(workspace["id"], application["id"], "prod", binding["id"], records,
        idempotency_key=key)


def _confirm_binding(registry, scope):
    workspace, application, connection, binding = scope
    return registry.record_connection_check(
        workspace["id"], application["id"], connection["id"], binding["id"],
        expected_connection_revision=1, expected_binding_revision=1,
        idempotency_key="confirm-source-scope", request_parameters={"environment_id": "prod"},
        check={"checked_at": "2026-09-23T09:00:00Z", "outcome": "empty",
               "connection_status": "connected", "binding_status": "confirmed",
               "http_status": 200, "scope_confirmed": True, "coverage": {"complete": True}},
    )


def test_signal_ingest_is_source_deduplicated_and_idempotent(tmp_path):
    registry = ProductRegistry(tmp_path / "product.sqlite")
    scope = _scope(registry)
    first = _ingest(registry, scope, [_signal("obs-1"), _signal("obs-2")], "batch-1")
    assert first["created_count"] == 2
    assert first["duplicate_count"] == 0
    assert {item["status"] for item in first["items"]} == {"new"}

    replay = _ingest(registry, scope, [_signal("obs-1"), _signal("obs-2")], "batch-1")
    assert replay == first
    second = _ingest(registry, scope, [_signal("obs-1"), _signal("obs-2")], "batch-2")
    assert second["created_count"] == 0
    assert second["duplicate_count"] == 2
    assert {item["id"] for item in second["items"]} == {item["id"] for item in first["items"]}
    assert len(registry.list_signals(scope[0]["id"], scope[1]["id"], "prod")) == 2

    with pytest.raises(ProductError, match="相同去重键"):
        _ingest(registry, scope, [_signal("different-id", dedup="langfuse:obs-1")], "batch-3")

    events = registry.workspace_events(scope[0]["id"], application_id=scope[1]["id"])
    assert [event["event_type"] for event in events].count("signals.ingested") == 2


def test_poll_repeat_of_operator_linked_source_auto_links_within_24_hours(tmp_path):
    registry = ProductRegistry(tmp_path / "product.sqlite")
    scope = _scope(registry)
    _confirm_binding(registry, scope)
    workspace, application, _, _ = scope
    prior = _ingest(registry, scope, [_signal("obs-repeat", source_version="v1")], "prior-signal")["items"][0]
    incident = registry.create_incident(
        workspace["id"], application["id"], "prod", "Observed failure", "high",
        [{"signal_id": prior["id"], "expected_revision": prior["revision"]}],
        idempotency_key="prior-incident",
    )

    current = _ingest(registry, scope, [_signal(
        "obs-repeat", source_version="v2", dedup="langfuse:obs-repeat:v2",
        observed_at="2026-09-23T11:00:00Z",
    )], "repeat-signal")

    assert current["created_count"] == 1
    assert current["duplicate_count"] == 0
    assert current["auto_linked_count"] == 1
    assert current["correlation_ambiguous_count"] == 0
    linked = current["items"][0]
    assert linked["status"] == "clustered"
    assert linked["revision"] == 2
    updated_incident = registry.get_incident(workspace["id"], application["id"], "prod", incident["id"])
    assert updated_incident["revision"] == incident["revision"] + 1
    assert updated_incident["signal_ids"] == [prior["id"], linked["id"]]
    assert _ingest(registry, scope, [_signal(
        "obs-repeat", source_version="v2", dedup="langfuse:obs-repeat:v2",
        observed_at="2026-09-23T11:00:00Z",
    )], "repeat-signal") == current

    events = registry.workspace_events(workspace["id"], application_id=application["id"])
    event_types = [event["event_type"] for event in events]
    assert event_types.count("signal.auto_linked") == 1
    assert event_types.count("incident.signal_auto_linked") == 1


def test_repeat_source_with_multiple_active_incidents_stays_in_inbox(tmp_path):
    registry = ProductRegistry(tmp_path / "product.sqlite")
    scope = _scope(registry)
    _confirm_binding(registry, scope)
    workspace, application, _, _ = scope
    prior = _ingest(registry, scope, [
        _signal("obs-ambiguous", source_version="v1"),
        _signal("obs-ambiguous", source_version="v2", dedup="langfuse:obs-ambiguous:v2"),
    ], "ambiguous-prior")["items"]
    first = registry.create_incident(workspace["id"], application["id"], "prod", "Candidate one", "high",
        [{"signal_id": prior[0]["id"], "expected_revision": prior[0]["revision"]}],
        idempotency_key="ambiguous-incident-one")
    second = registry.create_incident(workspace["id"], application["id"], "prod", "Candidate two", "high",
        [{"signal_id": prior[1]["id"], "expected_revision": prior[1]["revision"]}],
        idempotency_key="ambiguous-incident-two")

    result = _ingest(registry, scope, [_signal(
        "obs-ambiguous", source_version="v3", dedup="langfuse:obs-ambiguous:v3",
        observed_at="2026-09-23T11:00:00Z",
    )], "ambiguous-repeat")

    assert result["auto_linked_count"] == 0
    assert result["correlation_ambiguous_count"] == 1
    assert result["items"][0]["status"] == "new"
    assert result["items"][0]["id"] not in first["signal_ids"] + second["signal_ids"]
    event = next(event for event in registry.workspace_events(workspace["id"])
                 if event["event_type"] == "signal.correlation_ambiguous")
    assert event["payload"]["metadata"]["candidate_incident_ids"] == sorted([first["id"], second["id"]])


def test_repeat_source_older_than_24_hours_stays_in_inbox(tmp_path):
    registry = ProductRegistry(tmp_path / "product.sqlite")
    scope = _scope(registry)
    _confirm_binding(registry, scope)
    workspace, application, _, _ = scope
    prior = _ingest(registry, scope, [_signal(
        "obs-expired", source_version="v1", occurred_at="2026-09-21T08:00:00Z",
        observed_at="2026-09-21T09:00:00Z",
    )], "expired-prior")["items"][0]
    incident = registry.create_incident(workspace["id"], application["id"], "prod", "Prior failure", "medium",
        [{"signal_id": prior["id"], "expected_revision": prior["revision"]}],
        idempotency_key="expired-prior-incident")

    result = _ingest(registry, scope, [_signal(
        "obs-expired", source_version="v2", dedup="langfuse:obs-expired:v2",
        occurred_at="2026-09-22T09:30:00Z",
        observed_at="2026-09-22T10:00:01Z",
    )], "expired-repeat")

    assert result["auto_linked_count"] == 0
    assert result["correlation_ambiguous_count"] == 0
    assert result["items"][0]["status"] == "new"
    current = registry.get_incident(workspace["id"], application["id"], "prod", incident["id"])
    assert current["signal_ids"] == [prior["id"]]


def test_repeat_source_on_unverified_binding_does_not_auto_link(tmp_path):
    registry = ProductRegistry(tmp_path / "product.sqlite")
    scope = _scope(registry)
    workspace, application, _, _ = scope
    prior = _ingest(registry, scope, [_signal("obs-pending", source_version="v1")], "pending-prior")["items"][0]
    incident = registry.create_incident(
        workspace["id"], application["id"], "prod", "Pending source report", "high",
        [{"signal_id": prior["id"], "expected_revision": prior["revision"]}],
        idempotency_key="pending-incident",
    )

    result = _ingest(registry, scope, [_signal(
        "obs-pending", source_version="v2", dedup="langfuse:obs-pending:v2",
        observed_at="2026-09-23T11:00:00Z",
    )], "pending-repeat")

    assert result["auto_linked_count"] == 0
    assert result["items"][0]["status"] == "new"
    current = registry.get_incident(workspace["id"], application["id"], "prod", incident["id"])
    assert current["signal_ids"] == [prior["id"]]


def test_incident_creation_atomically_triages_signals_and_replays(tmp_path):
    registry = ProductRegistry(tmp_path / "product.sqlite")
    scope = _scope(registry)
    batch = _ingest(registry, scope, [_signal("obs-1"), _signal("obs-2")], "batch-1")
    selected = [{"signal_id": item["id"], "expected_revision": item["revision"]} for item in batch["items"]]
    workspace, application, _, _ = scope
    incident = registry.create_incident(workspace["id"], application["id"], "prod", "Trace failures",
        "high", selected, idempotency_key="incident-1")
    assert incident["state"] == "open"
    assert incident["signal_ids"] == [item["signal_id"] for item in selected]
    replay = registry.create_incident(workspace["id"], application["id"], "prod", "Trace failures",
        "high", selected, idempotency_key="incident-1")
    assert replay == incident

    current = registry.list_signals(workspace["id"], application["id"], "prod")
    assert {item["status"] for item in current} == {"clustered"}
    assert {item["revision"] for item in current} == {2}
    assert registry.get_incident(workspace["id"], application["id"], "prod", incident["id"]) == incident

    with pytest.raises(ProductError, match="信号已处理"):
        registry.create_incident(workspace["id"], application["id"], "prod", "Duplicate",
            "high", [{"signal_id": item["id"], "expected_revision": item["revision"]} for item in current],
            idempotency_key="incident-2")
    with pytest.raises(ProductError, match="信号已变化"):
        registry.create_incident(workspace["id"], application["id"], "prod", "Stale",
            "high", selected, idempotency_key="incident-3")

    with registry.tx() as db:
        linked = db.execute("SELECT COUNT(*) FROM incident_signals WHERE incident_id=?", (incident["id"],)).fetchone()[0]
        assert linked == 2
        event_types = [json.loads(row[0])["event_type"] for row in db.execute(
            "SELECT data FROM workspace_events WHERE workspace_id=? ORDER BY sequence", (workspace["id"],))]
    assert event_types[-3:] == ["signal.clustered", "signal.clustered", "incident.created"]


def test_operator_can_attach_new_signals_to_an_active_incident_atomically(tmp_path):
    registry = ProductRegistry(tmp_path / "product.sqlite")
    scope = _scope(registry)
    workspace, application, _, _ = scope
    first = _ingest(registry, scope, [_signal("obs-1")], "initial-batch")["items"][0]
    incident = registry.create_incident(
        workspace["id"], application["id"], "prod", "Trace failures", "high",
        [{"signal_id": first["id"], "expected_revision": first["revision"]}],
        idempotency_key="incident-create",
    )
    second = _ingest(registry, scope, [_signal("obs-2")], "followup-batch")["items"][0]
    signals = [{"signal_id": second["id"], "expected_revision": second["revision"]}]

    attached = registry.attach_signals_to_incident(
        workspace["id"], application["id"], "prod", incident["id"], signals,
        "同一服务窗口内的后续错误，人工确认属于同一调查范围",
        expected_incident_revision=incident["revision"], idempotency_key="attach-followup",
    )
    assert attached["revision"] == incident["revision"] + 1
    assert attached["signal_ids"] == [first["id"], second["id"]]
    assert registry.get_incident(workspace["id"], application["id"], "prod", incident["id"]) == attached
    assert {item["id"]: item["status"] for item in registry.list_signals(
        workspace["id"], application["id"], "prod") } == {first["id"]: "clustered", second["id"]: "clustered"}
    assert registry.attach_signals_to_incident(
        workspace["id"], application["id"], "prod", incident["id"], signals,
        "同一服务窗口内的后续错误，人工确认属于同一调查范围",
        expected_incident_revision=incident["revision"], idempotency_key="attach-followup",
    ) == attached

    with pytest.raises(ProductError) as stale_incident:
        registry.attach_signals_to_incident(
            workspace["id"], application["id"], "prod", incident["id"],
            [{"signal_id": _ingest(registry, scope, [_signal("obs-3")], "third-batch")["items"][0]["id"],
              "expected_revision": 1}],
            "stale revision", expected_incident_revision=incident["revision"], idempotency_key="stale-attach",
        )
    assert stale_incident.value.code == "STALE_INCIDENT_REVISION"
    assert len(registry.get_incident(workspace["id"], application["id"], "prod", incident["id"])["signal_ids"]) == 2

    with registry.tx() as db:
        kinds = [json.loads(row[0])["event_type"] for row in db.execute(
            "SELECT data FROM workspace_events WHERE workspace_id=? ORDER BY sequence", (workspace["id"],))]
    assert "incident.signals_attached" in kinds
    assert kinds.count("signal.clustered") == 2


def test_local_operator_can_claim_release_and_change_incident_severity(tmp_path):
    registry = ProductRegistry(tmp_path / "product.sqlite")
    scope = _scope(registry)
    workspace, application, _, _ = scope
    signal = _ingest(registry, scope, [_signal("operator-work")], "operator-work")['items'][0]
    incident = registry.create_incident(
        workspace["id"], application["id"], "prod", "Operator work", "medium",
        [{"signal_id": signal["id"], "expected_revision": signal["revision"]}],
        idempotency_key="operator-incident",
    )

    claimed = registry.set_incident_claim(workspace["id"], application["id"], "prod", incident["id"], True,
        "开始处理此问题", expected_revision=incident["revision"], idempotency_key="claim")
    assert claimed["assignee_id"] == "local-operator"
    assert claimed["revision"] == 2
    assert registry.set_incident_claim(workspace["id"], application["id"], "prod", incident["id"], True,
        "开始处理此问题", expected_revision=incident["revision"], idempotency_key="claim") == claimed
    with pytest.raises(ProductError) as duplicate_claim:
        registry.set_incident_claim(workspace["id"], application["id"], "prod", incident["id"], True,
            "重复认领", expected_revision=claimed["revision"], idempotency_key="claim-again")
    assert duplicate_claim.value.code == "INCIDENT_ALREADY_CLAIMED"

    raised = registry.update_incident_severity(workspace["id"], application["id"], "prod", incident["id"],
        "high", "用户影响范围扩大", expected_revision=claimed["revision"], idempotency_key="severity")
    assert raised["severity"] == "high"
    assert raised["revision"] == 3
    with pytest.raises(ProductError) as stale:
        registry.update_incident_severity(workspace["id"], application["id"], "prod", incident["id"],
            "critical", "stale", expected_revision=claimed["revision"], idempotency_key="severity-stale")
    assert stale.value.code == "STALE_INCIDENT_REVISION"

    released = registry.set_incident_claim(workspace["id"], application["id"], "prod", incident["id"], False,
        "完成本地处理交接", expected_revision=raised["revision"], idempotency_key="release")
    assert released["assignee_id"] is None
    assert released["revision"] == 4
    event_types = [item["event_type"] for item in registry.workspace_events(workspace["id"])]
    assert "incident.claimed" in event_types
    assert "incident.severity_changed" in event_types
    assert "incident.released" in event_types


def test_incidents_merge_atomically_with_revision_checks_and_audit(tmp_path):
    registry = ProductRegistry(tmp_path / "product.sqlite")
    scope = _scope(registry)
    workspace, application, _, _ = scope
    signals = _ingest(registry, scope,
        [_signal(f"merge-{index}") for index in range(4)], "merge-signals")["items"]
    source = registry.create_incident(workspace["id"], application["id"], "prod", "Source incident", "high",
        [{"signal_id": item["id"], "expected_revision": item["revision"]} for item in signals[:2]],
        idempotency_key="source-incident")
    target = registry.create_incident(workspace["id"], application["id"], "prod", "Target incident", "medium",
        [{"signal_id": item["id"], "expected_revision": item["revision"]} for item in signals[2:]],
        idempotency_key="target-incident")

    result = registry.merge_incidents(workspace["id"], application["id"], "prod", source["id"], target["id"],
        "人工确认属于同一故障", expected_incident_revision=source["revision"],
        expected_target_revision=target["revision"], idempotency_key="merge-command")
    assert result["source"]["state"] == "merged"
    assert result["source"]["outcome"] == "merged"
    assert result["source"]["merged_into_id"] == target["id"]
    assert result["source"]["signal_ids"] == []
    assert result["target"]["signal_ids"] == [item["id"] for item in signals[2:]] + [item["id"] for item in signals[:2]]
    assert result["source"]["revision"] == 2
    assert result["target"]["revision"] == 2
    assert registry.merge_incidents(workspace["id"], application["id"], "prod", source["id"], target["id"],
        "人工确认属于同一故障", expected_incident_revision=source["revision"],
        expected_target_revision=target["revision"], idempotency_key="merge-command") == result

    with registry.tx() as db:
        linked = [tuple(row) for row in db.execute(
            "SELECT incident_id,signal_id FROM incident_signals WHERE workspace_id=? ORDER BY signal_id",
            (workspace["id"],))]
        events = [json.loads(row[0]) for row in db.execute(
            "SELECT data FROM workspace_events WHERE workspace_id=? ORDER BY sequence", (workspace["id"],))]
    assert linked == sorted((target["id"], item["id"]) for item in signals)
    assert [event["event_type"] for event in events[-2:]] == ["incident.merged", "incident.signals_merged"]
    assert events[-2]["payload"]["metadata"]["reason"] == "人工确认属于同一故障"
    with pytest.raises(ProductError) as stale:
        registry.merge_incidents(workspace["id"], application["id"], "prod", source["id"], target["id"],
            "stale", expected_incident_revision=1, expected_target_revision=2, idempotency_key="merge-stale")
    assert stale.value.code == "STALE_INCIDENT_REVISION"


def test_incident_split_moves_only_selected_signals_and_is_atomic(tmp_path):
    registry = ProductRegistry(tmp_path / "product.sqlite")
    scope = _scope(registry)
    workspace, application, _, _ = scope
    signals = _ingest(registry, scope, [_signal(f"split-{index}") for index in range(3)], "split-signals")["items"]
    source = registry.create_incident(workspace["id"], application["id"], "prod", "Combined incident", "high",
        [{"signal_id": item["id"], "expected_revision": item["revision"]} for item in signals],
        idempotency_key="combined-incident")
    selection = [{"signal_id": signals[0]["id"], "expected_revision": signals[0]["revision"] + 1}]
    result = registry.split_incident(workspace["id"], application["id"], "prod", source["id"],
        "Independent issue", "medium", selection, "人工确认该信号属于独立问题",
        expected_incident_revision=source["revision"], idempotency_key="split-command")
    child = result["created"]
    parent = result["source"]
    assert parent["revision"] == source["revision"] + 1
    assert parent["signal_ids"] == [signals[1]["id"], signals[2]["id"]]
    assert child["revision"] == 1
    assert child["state"] == "open"
    assert child["signal_ids"] == [signals[0]["id"]]
    assert registry.get_incident(workspace["id"], application["id"], "prod", child["id"]) == child
    assert registry.split_incident(workspace["id"], application["id"], "prod", source["id"],
        "Independent issue", "medium", selection, "人工确认该信号属于独立问题",
        expected_incident_revision=source["revision"], idempotency_key="split-command") == result
    with registry.tx() as db:
        links = {row["signal_id"]: row["incident_id"] for row in db.execute(
            "SELECT signal_id,incident_id FROM incident_signals WHERE workspace_id=?", (workspace["id"],))}
        events = [json.loads(row[0]) for row in db.execute(
            "SELECT data FROM workspace_events WHERE workspace_id=? ORDER BY sequence", (workspace["id"],))]
    assert links == {signals[0]["id"]: child["id"], signals[1]["id"]: source["id"], signals[2]["id"]: source["id"]}
    assert [event["event_type"] for event in events[-2:]] == ["incident.signals_split", "incident.created_by_split"]
    with pytest.raises(ProductError) as would_empty_source:
        registry.split_incident(workspace["id"], application["id"], "prod", source["id"],
            "Empty source", "low", [{"signal_id": item["id"], "expected_revision": 2} for item in signals[1:]],
            "test", expected_incident_revision=parent["revision"], idempotency_key="split-empty-source")
    assert would_empty_source.value.code == "INVALID_INCIDENT_SPLIT_SIGNALS"


def test_signal_attachment_rejects_stale_batch_without_partial_mutation(tmp_path):
    registry = ProductRegistry(tmp_path / "product.sqlite")
    scope = _scope(registry)
    workspace, application, _, _ = scope
    initial = _ingest(registry, scope, [_signal("obs-1")], "initial")['items'][0]
    incident = registry.create_incident(
        workspace["id"], application["id"], "prod", "Trace failures", "high",
        [{"signal_id": initial["id"], "expected_revision": initial["revision"]}],
        idempotency_key="incident-create",
    )
    batch = _ingest(registry, scope, [_signal("obs-2"), _signal("obs-3")], "followups")
    first, second = batch["items"]
    with pytest.raises(ProductError) as stale_signal:
        registry.attach_signals_to_incident(
            workspace["id"], application["id"], "prod", incident["id"],
            [
                {"signal_id": first["id"], "expected_revision": first["revision"]},
                {"signal_id": second["id"], "expected_revision": second["revision"] + 1},
            ],
            "相关事件需要人工确认", expected_incident_revision=incident["revision"],
            idempotency_key="stale-signal-batch",
        )
    assert stale_signal.value.code == "STALE_SIGNAL_REVISION"
    assert registry.get_incident(workspace["id"], application["id"], "prod", incident["id"]) == incident
    assert {item["id"]: item["status"] for item in registry.list_signals(
        workspace["id"], application["id"], "prod")} == {
            initial["id"]: "clustered", first["id"]: "new", second["id"]: "new",
        }
    with registry.tx() as db:
        assert db.execute(
            "SELECT COUNT(*) FROM incident_signals WHERE incident_id=?", (incident["id"],),
        ).fetchone()[0] == 1


def test_signal_binding_scope_mismatch_fails_closed(tmp_path):
    registry = ProductRegistry(tmp_path / "product.sqlite")
    first = _scope(registry, "one")
    second = _scope(registry, "two")
    with pytest.raises(ProductError, match="信号来源范围不存在"):
        registry.ingest_signals(first[0]["id"], first[1]["id"], "prod", second[3]["id"],
            [_signal("obs-1")], idempotency_key="cross-scope")


def test_workspace_schema_v1_database_upgrades_additively_to_v2(tmp_path):
    path = tmp_path / "product.sqlite"
    original = ProductRegistry(path)
    workspace = original.create_workspace("Preserved", idempotency_key="preserve")
    with original.tx() as db:
        db.execute("DROP TABLE incident_signals")
        db.execute("DROP TABLE incidents")
        db.execute("DROP TABLE signals")
        db.execute("DROP TABLE signal_import_checkpoints")
        db.execute("DELETE FROM product_schema_migrations WHERE version=2")
        db.execute("DELETE FROM product_schema_migrations WHERE version=3")

    upgraded = ProductRegistry(path)
    assert upgraded.get_workspace(workspace["id"]) == workspace
    with upgraded.tx() as db:
        assert db.execute("SELECT MAX(version) FROM product_schema_migrations").fetchone()[0] == 8
        assert {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")} >= {
            "workspaces", "signals", "incidents", "incident_signals",
        }
