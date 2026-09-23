import json

from scripts.process_ownership import (
    IDENTITY_VERSION,
    command_has_tokens,
    load_json_record,
    owned_process_matches,
    parse_pid_file,
    process_identity_matches,
    target_process_matches,
    write_json_record,
)
from scripts import service


def _snapshot(**updates):
    value = {
        "pid": 1234,
        "ppid": 1,
        "pgid": 1234,
        "uid": 501,
        "start_time": "Tue Sep 22 10:11:12 2026",
        "boot_id": "boot-a",
        "command": "/repo/.venv/bin/python -m uvicorn s9.api:app --host 127.0.0.1 --port 9019",
    }
    value.update(updates)
    return value


def _record(**updates):
    return {
        "identity_version": IDENTITY_VERSION,
        **_snapshot(),
        "role": "section9-server",
        "root": "/repo",
        "port": 9019,
        **updates,
    }


def test_owned_process_requires_matching_os_identity_and_owner_tuple():
    record = _record()
    assert owned_process_matches(record, _snapshot(), role="section9-server", root="/repo", port=9019)
    assert not owned_process_matches(record, _snapshot(start_time="Tue Sep 22 10:11:13 2026"), role="section9-server", root="/repo", port=9019)
    assert not owned_process_matches(record, _snapshot(), role="evaluation", root="/repo", port=9019)
    assert not owned_process_matches(record, _snapshot(), role="section9-server", root="/other", port=9019)
    assert not owned_process_matches(record, _snapshot(), role="section9-server", root="/repo", port=9024)


def test_process_group_and_identity_version_are_fail_closed():
    assert not owned_process_matches(
        _record(), _snapshot(pgid=9999), role="section9-server", root="/repo", port=9019,
    )
    old = _record(identity_version=IDENTITY_VERSION - 1)
    assert not process_identity_matches(old, _snapshot())


def test_target_process_link_is_verified_by_start_identity():
    target = {"identity_version": IDENTITY_VERSION, **{
        key: _snapshot()[key] for key in ("pid", "pgid", "uid", "start_time", "boot_id", "command")
    }}
    guard = _record(target_process=target)
    assert target_process_matches(guard, _snapshot())
    assert not target_process_matches(guard, _snapshot(start_time="reused"))


def test_caffeinate_command_must_contain_exact_wait_target_tokens():
    command = "/usr/bin/caffeinate -i -w 1234"
    assert command_has_tokens(command, "/usr/bin/caffeinate", "-i", "-w", "1234")
    assert not command_has_tokens("/tmp/not-caffeinate -i -w 1234", "/usr/bin/caffeinate", "-i", "-w", "1234")
    assert not command_has_tokens("/usr/bin/caffeinate -i -w 12340", "/usr/bin/caffeinate", "-i", "-w", "1234")


def test_pid_and_json_records_reject_damage_and_write_private_files(tmp_path):
    pid_path = tmp_path / "service.pid"
    pid_path.write_text("1234\n", encoding="ascii")
    assert parse_pid_file(pid_path) == 1234
    pid_path.write_text("1234 extra", encoding="ascii")
    assert parse_pid_file(pid_path) is None

    record_path = tmp_path / "service.identity.json"
    write_json_record(record_path, _record())
    assert load_json_record(record_path) == _record()
    assert record_path.stat().st_mode & 0o777 == 0o600
    record_path.write_text("{damaged", encoding="utf-8")
    assert load_json_record(record_path) is None
    record_path.write_text(json.dumps([]), encoding="utf-8")
    assert load_json_record(record_path) is None


def test_service_stop_sends_no_signal_for_reused_pid(monkeypatch):
    record = {
        **_record(role="section9-server", root=str(service.ROOT), port=service.config.PORT),
        "pid": 9876,
        "pgid": 9876,
        "start_time": "original start",
    }
    signals = []
    monkeypatch.setattr(service, "process_snapshot", lambda pid: _snapshot(pid=pid, pgid=pid, start_time="reused start"))
    monkeypatch.setattr(service.os, "kill", lambda *args: signals.append(args))

    result = service._stop_record(
        record, role="section9-server", port=service.config.PORT,
        command_check=lambda _record: True, timeout=0,
    )

    assert result == "process identity changed; left it untouched"
    assert signals == []


def test_service_stop_signals_only_matching_managed_process(monkeypatch):
    expected = _snapshot(pid=9876, pgid=9876)
    record = {
        "identity_version": IDENTITY_VERSION,
        **expected,
        "role": "section9-server",
        "root": str(service.ROOT),
        "port": service.config.PORT,
    }
    snapshots = iter([expected, expected, None])
    signals = []
    monkeypatch.setattr(service, "process_snapshot", lambda _pid: next(snapshots))
    monkeypatch.setattr(service.os, "kill", lambda *args: signals.append(args))

    result = service._stop_record(
        record, role="section9-server", port=service.config.PORT,
        command_check=lambda _record: True, timeout=1,
    )

    assert result == "stopped"
    assert signals == [(9876, service.signal.SIGTERM)]
