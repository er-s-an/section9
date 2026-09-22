from __future__ import annotations

import json
import runpy
import sqlite3
from pathlib import Path


CHECKER = runpy.run_path(Path(__file__).parents[1] / "scripts" / "check-telemetry.py")


def test_trace_id_is_kept_complete_and_display_redaction_is_separate():
    trace = "00112233445566778899aabbccddeeff"
    assert CHECKER["safe_trace_id"](trace) == trace
    assert CHECKER["display_trace_id"](trace) == "00112233…eeff"


def test_database_events_require_the_requested_run(tmp_path):
    db_path = tmp_path / "section9.sqlite"
    with sqlite3.connect(db_path) as db:
        db.execute("CREATE TABLE events (data TEXT)")
        for run_id in ("old-run", "target-run"):
            db.execute("INSERT INTO events VALUES (?)", (json.dumps({
                "event_type": "telemetry.span_received",
                "run_id": run_id,
                "occurred_at": "2026-09-22T00:00:00Z",
                "payload": {"trace_id": f"trace-{run_id}", "name": "model"},
            }),))
    events, error = CHECKER["database_events"](db_path, "target-run")
    assert error is None
    assert [item["run_id"] for item in events] == ["target-run"]
    assert events[0]["trace_id_full"] == "trace-target-run"


def test_evidence_requires_explicit_top_level_run_id(tmp_path):
    good = tmp_path / "good.json"
    good.write_text(json.dumps({"run_id": "run-42"}))
    nested = tmp_path / "nested.json"
    nested.write_text(json.dumps({"runs": [{"run_id": "run-42"}]}))
    assert CHECKER["evidence_run_id"](good) == "run-42"
    assert CHECKER["evidence_run_id"](nested) is None


def test_main_rejects_historical_observation_even_if_old_trace_matches_db(tmp_path, capsys):
    env = tmp_path / "infra.env"
    env.write_text("LANGFUSE_INIT_PROJECT_PUBLIC_KEY=public\nLANGFUSE_INIT_PROJECT_SECRET_KEY=secret\nLANGFUSE_INIT_PROJECT_ID=project\n")
    namespace = CHECKER["main"].__globals__
    old_env, old_out = namespace["ENV"], namespace["OUT"]
    old_request, old_db = namespace["request_json"], namespace["database_events"]
    namespace["ENV"], namespace["OUT"] = env, tmp_path / "reports"
    namespace["request_json"] = lambda url, auth: (200, {"data": [{
        "projectId": "project", "name": "model", "traceId": "old-trace",
        "metadata": {"run_id": "old-run"}, "startTime": "2026-09-22T00:00:00Z",
    }]})

    def database_events(path, requested_run_id):
        if requested_run_id == "old-run":
            return ([{"trace_id_full": "old-trace", "trace_id": "old-trace", "run_id": "old-run",
                       "name": "model", "timestamp": "2026-09-22T00:00:00Z", "database": "main"}], None)
        return ([], None)

    namespace["database_events"] = database_events
    try:
        assert CHECKER["main"](["--run-id", "requested-run"]) == 1
        output = capsys.readouterr().out
        assert "status=degraded" in output
        assert "both_path_matches=0" in output
    finally:
        namespace["ENV"], namespace["OUT"] = old_env, old_out
        namespace["request_json"], namespace["database_events"] = old_request, old_db
