#!/usr/bin/env python3
"""Real model/business probes against deliberately partial and racing repairs.

Plans here are explicit adversarial test inputs, not claimed agent decisions.
The workload, provider, state mutations, grants and verifier are production code.
"""
import asyncio
import json
import time
from pathlib import Path

from s9 import config
from s9.model import ModelClient
from s9.provenance import capture_identity
from s9.store import Store
from s9.store import digest
from s9.victim import VictimApp

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "artifacts" / "live-safety" / str(time.time_ns())


async def case(name, partial):
    directory = config.DATA / "live-safety" / (name + "-" + str(time.time_ns()))
    store = Store(directory / "section9.sqlite")
    model = ModelClient(store)
    async def emit(kind, payload, **kwargs):
        return store.emit(kind, payload, **kwargs)
    victim = VictimApp(model, store.current_config, emit)
    store.register("fixer-a")
    store.register("verifier")
    scenario = "composite" if partial else "prompt"
    run = store.inject(scenario, "swarm", config.MODEL, 42, config.RUN_TOKEN_BUDGET)
    identity = capture_identity()
    with store.tx() as db:
        stored_run = store.get(db, "runs", run["id"])
        stored_run["manifest"]["source_identity"] = identity
        stored_run["manifest"]["source_identity_hash"] = digest(identity)
        store.save(db, "runs", stored_run)
    background = asyncio.create_task(victim.chat("查询不存在订单 S9-MISSING 的物流", run_id=run["id"], purpose="safety-loop")) if partial else None
    output = OUTPUT
    output.mkdir(parents=True, exist_ok=True)
    filename = output / (name + ".json")
    record = {"case": name, "test_passed": False, "origin": "live_model_and_production_state_machine",
              "plan_origin": "deliberate_test_input_not_agent_output", "identity": identity,
              "identity_hash": digest(identity), "run_id": run["id"], "error": None}
    try:
        detected = await victim.probe(suite="detect", run_id=run["id"])
        assert not detected["passed"], "Injected fault must be observed first"
        observation = store.emit("observation.probe", {"signal": "quality_mismatch", "result": detected}, run_id=run["id"])
        store.open_incident([observation["event_id"]])
        with store.tx() as db:
            task = next(json.loads(row[0]) for row in db.execute("SELECT data FROM tasks") if json.loads(row[0])["kind"] == "repair")
        claim = store.claim("fixer-a", task["id"])
        with store.tx() as db:
            agent = store.get(db, "agents", "fixer-a")
            generation = str(store.meta(db, "generation"))
            transport_epoch = str(store.meta(db, "transport_epoch"))
        plan = store.create_plan("fixer-a", {"run_id": run["id"], "task_id": task["id"], "task_epoch": claim["epoch"],
            "expected_revision": store.current_config()["revision"], "actions": [{"type": "set_prompt_revision", "values": {"prompt_version": "healthy"}}],
            "rationale": "Deliberate adversarial acceptance test: prompt-only repair", "evidence_ids": [observation["event_id"]],
            "instance_id": agent["instance_id"], "generation": generation, "transport_epoch": transport_epoch})
        grant = store.grant("fixer-a", plan["id"])
        store.execute("fixer-a", plan["id"], grant["id"], "deliberate-test-action")
        result = await victim.probe(suite="verify", run_id=run["id"])
        if partial:
            semantic = next(c for c in result["checks"] if c["name"] == "heldout_semantic_policy")
            loop = next(c for c in result["checks"] if c["name"] == "terminal_tool_stops")
            assert semantic["passed"], "Semantic half really recovers"
            assert not loop["passed"], "Tool loop still fails a real business request"
            assert not result["passed"]
        else:
            assert result["passed"], "Independent real business probe must pass before introducing revision race"
            with store.tx() as db:
                changed = store.set_config(db, {"context_multiplier": 2})
                store.event(db, "test.concurrent_config_change", {"revision": changed["revision"], "summary": "测试在验收完成与结案之间真实修改配置"}, run_id=run["id"])
        closed = store.verification("verifier", run["id"], result)
        assert not closed["passed"]
        assert store.run(run["id"])["status"] == "failed"
        victim.cancel_all("test-finished")
        if background:
            await background
        record["test_passed"] = True
        record.update({"detection": detected, "verification": closed})
    except Exception as exc:
        record["error"] = {"type": type(exc).__name__, "message": str(exc)[:500]}
        raise
    finally:
        victim.cancel_all("cleanup")
        if background and not background.done():
            background.cancel()
            await asyncio.gather(background, return_exceptions=True)
        await model.close()
        record.update({"run": store.run(run["id"]), "events": store.events(run_id=run["id"], limit=5000),
                       "usage": store.usage_records(run["id"])})
        if record["test_passed"]:
            record["error"] = None
        filename.write_text(json.dumps(record, ensure_ascii=False, indent=2))
        print(json.dumps({"case": name, "test_passed": record["test_passed"], "run_id": run["id"], "file": str(filename)}, ensure_ascii=False), flush=True)


async def main():
    await case("composite-partial-repair", True)
    await case("verification-revision-race", False)


if __name__ == "__main__":
    asyncio.run(main())
