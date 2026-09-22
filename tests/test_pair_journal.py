from __future__ import annotations

import pytest

from s9.pairs.journal import PairJournal
from s9.store import DEFAULT_CONFIG, Store


def _pair(*, pair_id="pair-1", arm="swarm", run_id="run-1", spec_hash="spec-1"):
    return {"pair_id": pair_id, "swarm_run_id": run_id if arm == "swarm" else "other-swarm",
            "baseline_run_id": run_id if arm == "baseline" else "other-baseline", "spec_hash": spec_hash}


def _scoped_store(tmp_path, name, *, pair_id="pair-1", arm="swarm", run_id="run-1", spec_hash="spec-1"):
    scope = {"pair_id": pair_id, "run_id": run_id, "arm": arm, "spec_hash": spec_hash}
    return Store(tmp_path / f"{name}.sqlite", scope=scope, baseline_config=DEFAULT_CONFIG)


@pytest.mark.parametrize("field,foreign", [
    ("pair_id", "pair-foreign"),
    ("arm", "baseline"),
    ("spec_hash", "spec-foreign"),
])
def test_same_run_event_with_mismatched_scope_is_not_relabelled(tmp_path, field, foreign):
    # The run id deliberately collides; the remaining scope field must still
    # prevent this source journal from being relabelled as the expected arm.
    values = {"pair_id": "pair-1", "arm": "swarm", "run_id": "run-1", "spec_hash": "spec-1"}
    values[field] = foreign
    source = _scoped_store(tmp_path, "foreign", **values)
    source.inject("prompt", "swarm", "foreign", 1, 1000, run_id="run-1")
    pair = _pair()
    journal = PairJournal(tmp_path / f"journal-{field}.sqlite")

    journal.ingest(pair, "swarm", source)

    assert journal.read("pair-1", "swarm") == []


def test_repeated_ingest_is_idempotent(tmp_path):
    source = _scoped_store(tmp_path, "source")
    source.inject("prompt", "swarm", "source", 1, 1000, run_id="run-1")
    source.emit("observation.probe", {"signal": "quality_mismatch"}, run_id="run-1", producer="probe")
    pair = _pair()
    journal = PairJournal(tmp_path / "journal.sqlite")

    journal.ingest(pair, "swarm", source)
    first = journal.read("pair-1", "swarm")
    journal.ingest(pair, "swarm", source)
    second = journal.read("pair-1", "swarm")

    assert [(e["event_id"], e["run_sequence"]) for e in second] == [
        (e["event_id"], e["run_sequence"]) for e in first
    ]
    assert len(second) == len({e["event_id"] for e in second})


def test_fresh_journal_rebuild_preserves_event_identity_and_scope(tmp_path):
    source = _scoped_store(tmp_path, "source")
    source.inject("prompt", "swarm", "source", 1, 1000, run_id="run-1")
    source.emit("observation.probe", {"signal": "quality_mismatch"}, run_id="run-1", producer="probe")
    expected = source.events(run_id="run-1")
    pair = _pair()

    first = PairJournal(tmp_path / "journal-first.sqlite")
    first.ingest(pair, "swarm", source)
    rebuilt = PairJournal(tmp_path / "journal-rebuilt.sqlite")
    rebuilt.ingest(pair, "swarm", source)

    expected_keys = {(e["event_id"], int(e["sequence"])) for e in expected}
    actual = rebuilt.read("pair-1", "swarm")
    actual_keys = {(e["event_id"], int(e["run_sequence"])) for e in actual}
    assert actual_keys == expected_keys
    assert all(e["scope"] == "run" and e["pair_id"] == "pair-1" and e["arm"] == "swarm"
               and e["run_id"] == "run-1" for e in actual)
