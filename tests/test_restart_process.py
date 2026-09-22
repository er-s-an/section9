"""Process level recovery proof using synthetic, durable reservations only.

The child is killed after its marker is written, which is emitted only after
every Store transaction has committed. No provider, HTTP service, model, or
Docker process is involved.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

from s9.store import Store


CHILD = textwrap.dedent(
    """
    import sys
    import time
    from pathlib import Path
    sys.path.insert(0, sys.argv[1])
    from s9.store import Store
    root = Path(sys.argv[2])
    scope = lambda arm, run: {"pair_id": "restart-proof", "run_id": run, "arm": arm, "spec_hash": "synthetic"}
    primary = Store(root / "primary.sqlite")
    run = primary.inject("prompt", "single", "synthetic-model-never-called", 1, 1000)
    primary.reserve_usage(run["id"], 11, "probe")
    sent = primary.reserve_usage(run["id"], 22, "chat")
    primary.mark_usage_sent(sent)
    legacy = primary.reserve_usage(run["id"], 33, "verify")
    with primary.tx() as db:
        row = primary.get(db, "usage", legacy)
        row.pop("provider_state", None)
        primary.save(db, "usage", row)
    swarm = Store(root / "swarm.sqlite", scope=scope("swarm", "swarm-run"))
    swarm_run = swarm.inject("prompt", "swarm", "synthetic-model-never-called", 2, 1000)
    swarm.reserve_usage(swarm_run["id"], 44, "verify")
    baseline = Store(root / "baseline.sqlite", scope=scope("baseline", "baseline-run"))
    baseline_run = baseline.inject("prompt", "single", "synthetic-model-never-called", 3, 1000)
    baseline_id = baseline.reserve_usage(baseline_run["id"], 55, "chat")
    baseline.mark_usage_sent(baseline_id)
    # Marker is written only after all SQLite transactions above committed.
    (root / "committed.marker").write_text("committed", encoding="utf-8")
    while True:
        time.sleep(1)
    """
)


def _records(store: Store):
    run_ids = [row["id"] for row in store.runs()]
    return [record for run_id in run_ids for record in store.usage_records(run_id)]


def test_sigkill_after_commit_recovers_primary_and_each_pair_arm(tmp_path):
    process = subprocess.Popen(
        [sys.executable, "-c", CHILD, str(Path(__file__).resolve().parents[1]), str(tmp_path)],
        cwd=Path(__file__).resolve().parents[1],
    )
    try:
        marker = tmp_path / "committed.marker"
        deadline = time.monotonic() + 15
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert marker.exists(), "child did not publish the post-commit marker"
        os.kill(process.pid, signal.SIGKILL)
        assert process.wait(timeout=5) == -signal.SIGKILL

        primary = Store(tmp_path / "primary.sqlite")
        swarm = Store(tmp_path / "swarm.sqlite", scope={"pair_id": "restart-proof", "run_id": "swarm-run", "arm": "swarm", "spec_hash": "synthetic"})
        baseline = Store(tmp_path / "baseline.sqlite", scope={"pair_id": "restart-proof", "run_id": "baseline-run", "arm": "baseline", "spec_hash": "synthetic"})

        assert primary.recover_usage() == {"recovered": 3, "known_zero": 1, "unknown": 2}
        assert swarm.recover_usage() == {"recovered": 1, "known_zero": 1, "unknown": 0}
        assert baseline.recover_usage() == {"recovered": 1, "known_zero": 0, "unknown": 1}

        # Verification-purpose reservations survive recovery and stay scoped to
        # their own SQLite arm; no arm can consume the other arm's records.
        assert any(row["purpose"] == "verify" for row in _records(primary))
        assert any(row["purpose"] == "verify" for row in _records(swarm))
        assert not any(row["purpose"] == "verify" for row in _records(baseline))
        assert primary.recover_usage() == {"recovered": 0, "known_zero": 0, "unknown": 0}
        assert swarm.recover_usage() == {"recovered": 0, "known_zero": 0, "unknown": 0}
        assert baseline.recover_usage() == {"recovered": 0, "known_zero": 0, "unknown": 0}
        assert primary.run(next(iter(primary.runs()))["id"])["unknown_reserved_tokens"] == 55
        assert baseline.run(next(iter(baseline.runs()))["id"])["unknown_reserved_tokens"] == 55
        assert swarm.run(next(iter(swarm.runs()))["id"]).get("unknown_reserved_tokens", 0) == 0
    finally:
        if process.poll() is None:
            os.kill(process.pid, signal.SIGKILL)
            process.wait(timeout=5)
