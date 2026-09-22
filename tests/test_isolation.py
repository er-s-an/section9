from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import Request
from starlette.responses import Response

from s9.memory import MemoryStore
from s9.sandbox import _profile
from s9.store import Rejected, Store


ROOT = Path(__file__).resolve().parents[1]


def _core_for(store: Store, tmp_path: Path):
    # Build only the projection object; this never starts workers or a model.
    import dotenv

    original_loader = dotenv.load_dotenv
    dotenv.load_dotenv = lambda *args, **kwargs: None
    try:
        from s9.core import Core
    finally:
        dotenv.load_dotenv = original_loader

    core = Core.__new__(Core)
    core.store = store
    core.memory = MemoryStore(tmp_path / "memory")
    core.evaluation_memory = MemoryStore(tmp_path / "evaluation-memory")
    core.memory_candidates = {}
    core.pending_detection = set()
    return core


def test_muted_context_has_no_peer_messages_results_plan_or_memory(tmp_path: Path) -> None:
    store = Store(tmp_path / "section9.sqlite")
    tokens = {aid: store.register(aid) for aid in ("fixer-a", "fixer-b", "verifier")}
    run = store.inject("prompt", "memory", "component-test", 42, 4000)
    observation = store.emit("observation.probe", {"signal": "quality_mismatch"}, run_id=run["id"])
    store.open_incident([observation["event_id"]])
    with store.tx() as db:
        task = next(json.loads(row[0]) for row in db.execute("SELECT data FROM tasks")
                    if json.loads(row[0])["kind"] == "repair")
    lease = store.claim("fixer-a", task["id"])
    with store.tx() as db:
        agent = store.get(db, "agents", "fixer-a")
        run_view = store.get(db, "runs", run["id"])
        transport_epoch = str(store.meta(db, "transport_epoch"))
    store.create_plan("fixer-a", {
        "run_id": run["id"], "task_id": task["id"], "task_epoch": lease["epoch"],
        "instance_id": agent["instance_id"], "generation": run_view["generation"],
        "transport_epoch": transport_epoch,
        "expected_revision": store.current_config()["revision"],
        "actions": [{"type": "set_prompt_revision", "values": {"prompt_version": "healthy"}}],
        "rationale": "projection fixture",
    })
    store.message("fixer-a", {
        "run_id": run["id"], "task_id": lease["task_id"], "task_epoch": lease["epoch"],
        "instance_id": agent["instance_id"], "generation": run_view["generation"],
        "transport_epoch": transport_epoch, "kind": "result", "content": "peer result",
        "evidence_ids": [], "confidence": 0.5,
    })
    store.set_muted(True)

    context = _core_for(store, tmp_path).context(store.authenticate(tokens["fixer-b"]))
    assert context["messages"] == []
    assert context["plan"] is None
    assert context["memory"] is None
    assert all("result_summary" not in task_view for task_view in context["tasks"])
    assert "peer result" not in json.dumps(context, ensure_ascii=False)


@pytest.mark.skipif(sys.platform != "darwin" or not Path("/usr/bin/sandbox-exec").exists(),
                    reason="requires macOS Seatbelt")
def test_worker_sandbox_denies_project_data_and_non_worker_python(tmp_path: Path) -> None:
    profile = _profile(ROOT, Path(sys.executable), 9021)
    targets = [ROOT / name / "_section9_isolation_probe.txt"
               for name in ("data", ".runtime", "artifacts", "docs")]
    targets.append(ROOT / "s9" / "_section9_isolation_probe.py")
    for target in targets:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("probe", encoding="utf-8")
    try:
        for target in targets:
            probe = (
                "from pathlib import Path; import sys; "
                "Path(sys.argv[1]).read_text(encoding='utf-8'); print('ALLOWED')"
            )
            result = subprocess.run(
                ["/usr/bin/sandbox-exec", "-p", profile, str(Path(sys.executable).resolve()), "-c", probe, str(target)],
                cwd=ROOT, env={"PATH": "/usr/bin:/bin", "PYTHONNOUSERSITE": "1"},
                text=True, capture_output=True, timeout=15,
            )
            assert result.returncode != 0, target
            assert "Operation not permitted" in result.stderr, result.stderr
            assert "ALLOWED" not in result.stdout
    finally:
        for target in targets:
            target.unlink(missing_ok=True)


def _request(path: str, port: int) -> Request:
    return Request({
        "type": "http", "method": "GET", "path": path, "raw_path": path.encode(),
        "query_string": b"", "headers": [], "scheme": "http",
        "server": ("127.0.0.1", port), "client": ("127.0.0.1", 50000),
        "root_path": "", "http_version": "1.1",
    })


@pytest.mark.asyncio
async def test_agent_and_console_ingresses_are_separate(monkeypatch: pytest.MonkeyPatch) -> None:
    # Prevent module import from consulting either dotenv file.
    import dotenv
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *args, **kwargs: None)
    from s9.api import boundaries

    async def ok(_request: Request) -> Response:
        return Response(status_code=200)

    assert (await boundaries(_request("/api/state", 9021), ok)).status_code == 403
    assert (await boundaries(_request("/agent/context", 9019), ok)).status_code == 403
    assert (await boundaries(_request("/agent/context", 9021), ok)).status_code == 200
    assert (await boundaries(_request("/api/state", 9019), ok)).status_code == 200


@pytest.mark.asyncio
async def test_model_requires_live_unpaused_lease_without_provider_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import dotenv

    monkeypatch.setattr(dotenv, "load_dotenv", lambda *args, **kwargs: None)
    from s9.api import model
    from s9.contracts import ModelRequest

    store = Store(tmp_path / "section9.sqlite")
    token = store.register("fixer-a")
    run = store.inject("prompt", "swarm", "component-test", 42, 4000)
    event = store.emit("observation.probe", {"signal": "quality_mismatch"}, run_id=run["id"])
    store.open_incident([event["event_id"]])
    with store.tx() as db:
        task = next(json.loads(row[0]) for row in db.execute("SELECT data FROM tasks")
                    if json.loads(row[0])["kind"] == "repair")
    store.claim("fixer-a", task["id"])
    identity = store.authenticate(token)
    calls = []

    class NoProvider:
        async def complete(self, *args, **kwargs):
            calls.append((args, kwargs))
            return {"content": "unexpected"}

    core = SimpleNamespace(store=store, model=NoProvider())
    item = ModelRequest(run_id=run["id"], purpose="repair", messages=[])

    store.pause("fixer-a", True)
    with pytest.raises(Rejected) as paused:
        await model(item, identity, core)
    assert paused.value.code == "AGENT_PAUSED"
    assert calls == []

    store.pause("fixer-a", False)
    with store.tx() as db:
        task = store.get(db, "tasks", task["id"])
        task["lease_deadline"] = 0.0
        store.save(db, "tasks", task)
    with pytest.raises(Rejected) as expired:
        await model(item, identity, core)
    assert expired.value.code == "LEASE_EXPIRED"
    assert calls == []


def test_verifier_has_no_write_execution_authority(tmp_path: Path) -> None:
    store = Store(tmp_path / "section9.sqlite")
    store.register("fixer-a")
    store.register("verifier")
    run = store.inject("prompt", "swarm", "component-test", 42, 4000)
    event = store.emit("observation.probe", {"signal": "quality_mismatch"}, run_id=run["id"])
    store.open_incident([event["event_id"]])
    with store.tx() as db:
        task = next(json.loads(row[0]) for row in db.execute("SELECT data FROM tasks")
                    if json.loads(row[0])["kind"] == "repair")
    lease = store.claim("fixer-a", task["id"])
    with store.tx() as db:
        agent = store.get(db, "agents", "fixer-a")
        run_view = store.get(db, "runs", run["id"])
        transport_epoch = str(store.meta(db, "transport_epoch"))
    plan = store.create_plan("fixer-a", {
        "run_id": run["id"], "task_id": task["id"], "task_epoch": lease["epoch"],
        "instance_id": agent["instance_id"], "generation": run_view["generation"],
        "transport_epoch": transport_epoch,
        "expected_revision": store.current_config()["revision"],
        "actions": [{"type": "set_prompt_revision", "values": {"prompt_version": "healthy"}}],
        "rationale": "authority fixture",
    })
    grant = store.grant("fixer-a", plan["id"])
    with pytest.raises(Rejected) as error:
        store.execute("verifier", plan["id"], grant["grant_id"], "verifier-cannot-write")
    assert error.value.code == "ROLE_FORBIDDEN"
