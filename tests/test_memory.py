from __future__ import annotations

import json

import pytest

from s9.memory import MemoryStore


def run(*, symptoms, passed=True, model="model-under-test", run_id="run-1"):
    return {"id": run_id, "scenario": "composite", "symptoms": symptoms,
            "verification": {"passed": passed}, "model": model,
            "closed_at": "2026-09-22T00:00:00+00:00"}


def actions():
    return [{"type": "apply_config_bundle", "values": {"prompt_version": "healthy", "retry_limit": 3}}]


def test_seeded_match_prefers_complete_composite_without_scenario(tmp_path):
    store = MemoryStore(tmp_path)
    matched = store.match(["cost_high", "quality_mismatch", "tool_stalled"], {"revision": "8"})
    assert matched is not None
    assert matched["id"] == "seed_composite_repair"
    assert matched["source"] == "seeded"
    assert matched["reuse_count"] == 0


def test_match_requires_real_supported_signals_and_prefers_two_over_single(tmp_path):
    store = MemoryStore(tmp_path)
    assert store.match(["scenario:composite"], {}) is None
    matched = store.match(["quality_mismatch", "tool_stalled"], {})
    assert matched is not None and matched["id"] == "seed_composite_repair"


def test_failed_run_is_rejected_and_does_not_write(tmp_path):
    store = MemoryStore(tmp_path)
    with pytest.raises(ValueError, match="passed"):
        store.record_success(run(symptoms=["quality_mismatch"], passed=False), actions())
    assert not (tmp_path / "events.jsonl").exists()


def test_assets_are_officially_hashed_schema_validated_and_persisted(tmp_path):
    store = MemoryStore(tmp_path)
    result = store.record_success(run(symptoms=["quality_mismatch"]), actions())
    for key in ("gene", "capsule", "evolution_event"):
        assert result[key]["asset_id"].startswith("sha256:")
    lines = (tmp_path / "events.jsonl").read_text().splitlines()
    assert [json.loads(line)["type"] for line in lines] == ["Gene", "Capsule", "EvolutionEvent"]
    restored = MemoryStore(tmp_path)
    assert restored.status()["events"] == 3
    assert restored.list_playbooks()[-1]["source"] == "learned"
    assert restored.status()["hub_status"] == "not_implemented"
    assert restored.status()["remote_publish_implemented"] is False
    assert restored.status()["needs_implementation"] is True
    assert restored.status()["pending_auth"] is False
    assert result["publish_state"] == "local_only"
    assert result["remote_publish_implemented"] is False


def test_verified_reuse_increments_count_and_references_prior_asset(tmp_path):
    store = MemoryStore(tmp_path)
    first = store.record_success(run(symptoms=["tool_stalled"], run_id="run-1"), actions())
    second_run = run(symptoms=["tool_stalled"], run_id="run-2")
    second_run.update(memory_used_id=first["playbook"]["id"], reuseapproved=True)
    second = store.record_success(second_run, actions())
    assert second["playbook"]["reuse_count"] == 1
    assert second["playbook"]["success_count"] == 2
    assert second["capsule"]["source_type"] == "reused"
    assert second["capsule"]["reused_asset_id"] == first["capsule"]["asset_id"]
    assert len((tmp_path / "events.jsonl").read_text().splitlines()) == 6


def test_repeated_success_without_root_reuse_approval_is_not_reuse(tmp_path):
    store = MemoryStore(tmp_path)
    first = store.record_success(run(symptoms=["tool_stalled"], run_id="run-1"), actions())
    second = store.record_success(run(symptoms=["tool_stalled"], run_id="run-2"), actions())
    assert second["playbook"]["id"] == first["playbook"]["id"]
    assert second["playbook"]["reuse_count"] == 0
    assert second["playbook"]["success_count"] == 2
    assert second["capsule"]["source_type"] == "generated"


def test_schema_failure_does_not_pollute_playbooks(tmp_path, monkeypatch):
    store = MemoryStore(tmp_path)
    before = store.list_playbooks()
    monkeypatch.setattr(store, "_schema_check", lambda assets: (_ for _ in ()).throw(RuntimeError("schema")))
    with pytest.raises(RuntimeError, match="schema"):
        store.record_success(run(symptoms=["quality_mismatch"]), actions())
    assert store.list_playbooks() == before
    assert not (tmp_path / "events.jsonl").exists()
