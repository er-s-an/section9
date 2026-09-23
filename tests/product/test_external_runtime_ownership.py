from pathlib import Path

import pytest

from s9.product import external_runtime as external_runtime_module
from s9.product.external_runtime import ExternalRuntime, _ProcessSnapshot


def _runtime(tmp_path: Path) -> tuple[ExternalRuntime, _ProcessSnapshot, dict[str, object]]:
    root = tmp_path / "support-agent"
    bin_dir = root / ".venv" / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "python").write_text("fake python executable", encoding="utf-8")
    (bin_dir / "uvicorn").write_text("fake uvicorn entrypoint", encoding="utf-8")
    runtime = ExternalRuntime(
        root=root,
        expected_commit="pinned-commit",
        port=8765,
        runtime_dir=tmp_path / "runtime",
        log_path=tmp_path / "logs" / "runtime.log",
        data_path=tmp_path / "data" / "runtime.sqlite",
    )
    command = tuple(runtime._command())
    snapshot = _ProcessSnapshot(
        pid=43210,
        start_identity="test-boot:123456",
        executable=str(Path(command[0]).resolve()),
        argv=command,
        cwd=str(root.resolve()),
    )
    record = runtime._record_for_snapshot(snapshot)
    return runtime, snapshot, record


def _disable_pidfds_for_fake_processes(monkeypatch: pytest.MonkeyPatch) -> list[tuple[int, int]]:
    signals: list[tuple[int, int]] = []
    monkeypatch.setattr(external_runtime_module.os, "pidfd_open", None, raising=False)
    monkeypatch.setattr(external_runtime_module.signal, "pidfd_send_signal", None, raising=False)
    monkeypatch.setattr(external_runtime_module.os, "kill", lambda pid, signum: signals.append((pid, signum)))
    return signals


@pytest.mark.parametrize("contents", ["{broken", "43210", '{"version":1,"version":1}'])
def test_stop_does_not_signal_for_legacy_malformed_or_ambiguous_pid_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, contents: str
) -> None:
    runtime, _, _ = _runtime(tmp_path)
    runtime.runtime_dir.mkdir(parents=True)
    runtime.pid_path.write_text(contents, encoding="utf-8")
    signals = _disable_pidfds_for_fake_processes(monkeypatch)

    result = runtime.stop()

    assert result["owned"] is False
    assert signals == []


def test_stop_does_not_signal_when_pid_record_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, _, _ = _runtime(tmp_path)
    signals = _disable_pidfds_for_fake_processes(monkeypatch)

    result = runtime.stop()

    assert result == {"status": "stopped", "owned": False}
    assert signals == []


def test_stop_does_not_signal_a_reused_pid(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runtime, snapshot, record = _runtime(tmp_path)
    runtime._write_pid_record(record)
    reused = _ProcessSnapshot(
        pid=snapshot.pid,
        start_identity="test-boot:999999",
        executable=snapshot.executable,
        argv=snapshot.argv,
        cwd=snapshot.cwd,
    )
    monkeypatch.setattr(runtime, "_process_snapshot", lambda pid: reused if pid == snapshot.pid else None)
    signals = _disable_pidfds_for_fake_processes(monkeypatch)

    result = runtime.stop()

    assert result["owned"] is False
    assert signals == []


def test_stop_does_not_signal_an_unrelated_process_with_the_same_pid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, snapshot, record = _runtime(tmp_path)
    runtime._write_pid_record(record)
    unrelated = _ProcessSnapshot(
        pid=snapshot.pid,
        start_identity=snapshot.start_identity,
        executable="/usr/bin/python3",
        argv=("python3", "-m", "uvicorn", "api:app", "--port", "8765"),
        cwd="/tmp/unrelated",
    )
    monkeypatch.setattr(runtime, "_process_snapshot", lambda pid: unrelated if pid == snapshot.pid else None)
    signals = _disable_pidfds_for_fake_processes(monkeypatch)

    result = runtime.stop()

    assert result["owned"] is False
    assert signals == []


def test_status_recovers_only_a_fully_matching_owned_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, snapshot, record = _runtime(tmp_path)
    runtime._write_pid_record(record)
    monkeypatch.setattr(runtime, "_process_snapshot", lambda pid: snapshot if pid == snapshot.pid else None)

    assert runtime.status() == {
        "status": "running", "pid": snapshot.pid, "port": runtime.port,
        "owned": True, "recovered": True,
    }


def test_stop_signals_a_valid_owned_process_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runtime, snapshot, record = _runtime(tmp_path)
    runtime._write_pid_record(record)
    signals = _disable_pidfds_for_fake_processes(monkeypatch)
    snapshots = iter([snapshot, snapshot, snapshot, None])
    monkeypatch.setattr(runtime, "_process_snapshot", lambda pid: next(snapshots, None))

    result = runtime.stop(timeout=1)

    assert result == {"status": "stopped", "owned": True}
    assert signals == [(snapshot.pid, external_runtime_module.signal.SIGTERM)]


def test_stop_revalidates_before_sigkill_after_stale_pid_polling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, snapshot, record = _runtime(tmp_path)
    runtime._write_pid_record(record)
    signals = _disable_pidfds_for_fake_processes(monkeypatch)
    reused = _ProcessSnapshot(
        pid=snapshot.pid,
        start_identity="test-boot:reused-after-term",
        executable=snapshot.executable,
        argv=snapshot.argv,
        cwd=snapshot.cwd,
    )
    calls = 0

    def snapshots(_pid: int) -> _ProcessSnapshot:
        nonlocal calls
        calls += 1
        # Keep the recorded process through TERM and the first wait check, then
        # simulate PID reuse before the forced-kill identity recheck.
        return reused if calls >= 6 else snapshot

    monkeypatch.setattr(runtime, "_process_snapshot", snapshots)
    monotonic_values = iter([0.0, 0.0, 2.0, 3.0, 4.0])
    monkeypatch.setattr(external_runtime_module.time, "monotonic", lambda: next(monotonic_values, 10.0))
    monkeypatch.setattr(external_runtime_module.time, "sleep", lambda _seconds: None)

    result = runtime.stop(timeout=1)

    assert result["owned"] is True
    assert signals == [(snapshot.pid, external_runtime_module.signal.SIGTERM)]
