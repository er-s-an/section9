from __future__ import annotations

import runpy
from pathlib import Path


EVALUATE = runpy.run_path(Path(__file__).parents[1] / "scripts" / "evaluate.py")


def manifest(identity: dict | None) -> dict:
    return {"manifest": {"environment": "evaluation", "model": "m", "total_token_budget": 10,
                          "request_concurrency_limit": 2, "fixture_version": "f", "memory_condition": "off",
                          "source_identity": identity}}


IDENTITY = {"backend_source_hash": "b", "frontend_build_hash": "f", "dependency_lock_hash": "d",
            "fixture_hash": "x", "acceptance_contract_hash": "a"}


def test_manifest_hashes_align_and_mismatch_is_visible():
    expected = {"model": "m", "total_token_budget": 10, "concurrency": 2, "fixture_version": "f",
                "memory_snapshot": "off", "identity": IDENTITY}
    good = EVALUATE["_manifest_check"]({"id": "run-good", **manifest(IDENTITY)}, expected)
    assert good["identity_status"] == "aligned"
    assert good["aligned"] is True
    bad_identity = dict(IDENTITY, backend_source_hash="changed")
    bad = EVALUATE["_manifest_check"]({"id": "run-bad", **manifest(bad_identity)}, expected)
    assert bad["identity_status"] == "unaligned_hash_mismatch"
    assert bad["aligned"] is False


def test_historical_manifest_without_identity_is_unaligned():
    expected = {"model": "m", "total_token_budget": 10, "concurrency": 2, "fixture_version": "f",
                "memory_snapshot": "off", "identity": None}
    check = EVALUATE["_manifest_check"]({"id": "old-run", **manifest(None)}, expected)
    assert check["identity_status"] == "unaligned_missing_identity"
    assert check["aligned"] is False


def test_failed_unknown_run_still_counts_as_failure():
    cell = EVALUATE["_empty_cell"]("swarm", "prompt")
    check = EVALUATE["_add_run"](cell, {"id": "failed-unknown", "status": "failed", "usage_unknown": True,
                                      "manifest": {}}, None)
    assert cell["n"] == 1 and cell["failure"] == 1 and cell["tokens_unknown"] == 1
    assert check["aligned"] is False
