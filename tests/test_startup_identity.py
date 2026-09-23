from __future__ import annotations

import sys

import pytest

from s9.provenance import IDENTITY_KEYS, capture_identity, identity_mismatches


def identity(**overrides):
    value = {key: f"value-{key}" for key in IDENTITY_KEYS}
    value["dirty"] = False
    value.update(overrides)
    return value


def test_matching_identity_is_accepted():
    expected = identity()
    assert identity_mismatches(expected, dict(expected)) == {}


def test_identity_hash_is_stable_across_python_cache_writes():
    first = capture_identity()
    second = capture_identity()
    assert first["backend_source_hash"] == second["backend_source_hash"]
    assert first["fixture_hash"] == second["fixture_hash"]


def test_old_checkout_identity_is_rejected():
    expected = identity()
    observed = dict(expected, backend_source_hash="old-backend")
    assert "backend_source_hash" in identity_mismatches(expected, observed)


def test_other_checkout_identity_is_rejected():
    expected = identity()
    observed = dict(expected, checkout="/other/checkout")
    assert "checkout" in identity_mismatches(expected, observed)


def test_dirty_diagnostic_changes_do_not_reject_runtime_identity():
    expected = identity()
    observed = dict(expected, dirty=True, dirty_diff_sha256="new-artifact-only-hash")
    assert identity_mismatches(expected, observed) == {}


def test_service_does_not_accept_mismatched_existing_process(monkeypatch):
    import scripts.service as service

    expected = identity()
    monkeypatch.setattr(service, "capture_identity", lambda: expected)
    monkeypatch.setattr(service, "listener_health", lambda: {"status": "running", "identity": dict(expected, git_commit="old")})
    touched = []
    monkeypatch.setattr(service.subprocess, "Popen", lambda *args, **kwargs: touched.append(args))
    monkeypatch.setattr(sys, "argv", ["service.py", "start"])
    with pytest.raises(SystemExit, match="mismatched identity"):
        service.main()
    assert touched == []


def test_service_accepts_matching_existing_process(monkeypatch, capsys):
    import scripts.service as service

    expected = identity()
    monkeypatch.setattr(service, "capture_identity", lambda: expected)
    monkeypatch.setattr(service, "listener_health", lambda: {"status": "running", "identity": dict(expected)})
    monkeypatch.setattr(service, "_read_owned_record", lambda *args, **kwargs: (
        {"pid": 12345, "startup_identity": expected},
        {"pid": 12345, "command": "owned section9 process"},
        None,
    ))
    monkeypatch.setattr(service, "_server_command_matches", lambda record: True)
    monkeypatch.setattr(sys, "argv", ["service.py", "start"])
    assert service.main() == 0
    assert "Already running" in capsys.readouterr().out
