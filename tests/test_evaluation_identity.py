from __future__ import annotations

import pytest

from scripts.environments import identity_error, wait_for_ready


def test_evaluation_rejects_old_listener_identity():
    expected = {"checkout": "/checkout", "git_commit": "new", "backend_source_hash": "new-backend"}
    observed = {"status": "running", "identity": {**expected, "backend_source_hash": "old-backend"}}
    assert "backend_source_hash" in identity_error(observed, expected)


def test_evaluation_accepts_matching_listener_identity():
    expected = {"checkout": "/checkout", "git_commit": "new", "backend_source_hash": "new-backend"}
    assert identity_error({"status": "running", "identity": expected}, expected) is None


def test_evaluation_timeout_is_failure():
    class Response:
        is_success = False

    class Client:
        def get(self, url):
            return Response()

    class Child:
        def poll(self):
            return None

    with pytest.raises(SystemExit, match="timed out"):
        wait_for_ready(Client(), 9024, Child(), {"checkout": "/checkout"}, attempts=1)
