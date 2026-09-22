"""Pure, journal-backed projection for a paired Section9 run.

This module intentionally has no Store, model, clock, or network dependency.  It
accepts a journal slice and returns a stable read model for one arm.
"""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from copy import deepcopy
from typing import Any

STAGES = (
    ("monitor", "注入"), ("detect", "检测"), ("diagnose", "诊断"),
    ("plan", "计划"), ("execute", "执行"), ("verify", "复核"), ("close", "结案"),
)
POSITIONS = {"monitor": "monitor", "detect": "monitor", "diagnose": "analysis",
             "plan": "meeting", "execute": "execution", "verify": "verification", "close": "archive"}


def _payload(e: dict[str, Any]) -> dict[str, Any]:
    p = e.get("payload")
    return p if isinstance(p, dict) else {}


def _hash(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        value = {k: v for k, v in value.items() if k not in {"revision", "generation"}}
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def _spec(pair: dict[str, Any], key: str, fallback: Any = None) -> Any:
    spec = pair.get("immutable_spec") or pair.get("spec") or {}
    for name in (key, f"{key}_snapshot", f"{key}_config"):
        if name in spec:
            return spec[name]
        if name in pair:
            return pair[name]
    return fallback


def _stage_for(event_type: str, payload: dict[str, Any]) -> str | None:
    s = str(payload.get("stage") or payload.get("purpose") or "").lower()
    t = event_type.lower()
    if t in {"chaos.injected", "arm.started"}:
        return "monitor"
    for candidate in ("monitor", "detect", "diagnose", "plan", "execute", "verify", "close"):
        if candidate in s or candidate in t:
            return candidate
    if t == "incident.opened":
        return "detect"
    if t.startswith(("observation.", "probe.")):
        return "detect"
    if t.startswith('model.') and s in {'single', 'repair'}:
        return 'plan'
    if t.startswith("plan.") or t in {"model.decision", "model.completed"}:
        return "plan"
    if t.startswith(("action.", "execution.", "grant.")):
        return "execute"
    if t.startswith("verification."):
        return "verify"
    if t.startswith(("incident.closed", "incident.failed")):
        return "close"
    if t.startswith("agent.") or t.startswith("dialog."):
        return None
    return None


def _status_for(e: dict[str, Any], stage: str) -> str | None:
    p, t = _payload(e), str(e.get("event_type", "")).lower()
    if t == 'grant.rejected' and p.get('code') == 'APPROVAL_REQUIRED':
        return 'waiting'
    if t.endswith('.cancelled'):
        return 'cancelled'
    if stage == "verify" and ("passed" in p or t == "verification.completed"):
        return "passed" if p.get("passed") is True else "failed"
    if stage == "close":
        return "passed" if t == "incident.closed" else "failed" if t == "incident.failed" else None
    if t in {"chaos.injected", "arm.started", "incident.opened"}:
        return "passed"
    if stage == "diagnose" and t == "model.completed" and str(p.get("purpose", "")).lower() == "diagnose":
        return "passed"
    if stage == "plan" and t == "plan.proposed":
        return "passed"
    if stage == "execute" and t == "execution.granted":
        return "running"
    if stage == "execute" and t == "action.applied":
        return "passed"
    if p.get("status") in {"pending", "running", "passed", "failed", "skipped"}:
        return p["status"]
    if t.endswith((".failed", ".rejected")) or "failure" in t:
        return "failed"
    if t.endswith((".completed", ".applied", ".closed", ".received", ".created", ".proposed", ".approved", ".granted")):
        return "passed" if stage in {"detect", "execute", "close"} else "running"
    return "running"


def _activity_event(e: dict[str, Any]) -> bool:
    t = str(e.get("event_type", "")).lower()
    return not t.startswith(("agent.joined", "agent.status", "agent.heartbeat", "agent.paused", "agent.resumed"))


def project_arm(pair: dict, arm: str, run: dict | None, events: list[dict], *, config: dict,
                agents: list, usage: list, dependencies: dict, as_of_sequence: int) -> dict:
    pair_id = pair.get("pair_id", pair.get("id"))
    run_id = ((run or {}).get("run_id") or (run or {}).get("id") or pair.get(f"{arm}_run_id"))
    # Pair and run identity are mandatory.  In particular, system/null-run
    # records cannot become an arm's activity merely because they are recent.
    selected = []
    for e in events:
        if e.get("sequence", 0) > as_of_sequence:
            continue
        if e.get("pair_id") != pair_id or e.get("arm") != arm:
            continue
        if e.get("scope") not in {"pair", "run"}:
            continue
        if run_id is None or e.get("run_id") != run_id:
            continue
        selected.append(e)
    selected.sort(key=lambda e: (int(e.get("sequence", 0)), str(e.get("event_id", ""))))

    stage_events: dict[str, list[dict]] = defaultdict(list)
    for e in selected:
        s = _stage_for(str(e.get("event_type", "")), _payload(e))
        if s:
            stage_events[s].append(e)
    baseline = arm == "baseline"
    combined = next((e for e in selected if e.get('event_type') == 'plan.proposed'), None)
    stages = []
    for sid, label in STAGES:
        es = stage_events[sid]
        status = "pending"
        if es:
            statuses = [_status_for(e, sid) for e in es]
            status = "failed" if "failed" in statuses else "passed" if "passed" in statuses else statuses[-1] or "running"
        if not es and (run or {}).get('status') in {'reset', 'failed'}:
            status = 'cancelled'
        if baseline and sid in {"diagnose", "plan"} and combined:
            status = "combined"
            es = [combined]
        stages.append({"id": sid, "label": label, "status": status,
                       "started_at": es[0].get("occurred_at") if es else None,
                       "ended_at": es[-1].get("occurred_at") if es and status in {"passed", "failed"} else None,
                       "source_event_ids": [e.get("event_id") for e in es if e.get("event_id")],
                       "detail": _payload(es[-1]).get("summary") if es else None})

    projected_agents = []
    for a in agents or []:
        aid = a.get("id") if isinstance(a, dict) else str(a)
        if baseline and aid != 'single':
            continue
        own_all = [e for e in selected if (e.get("producer") == aid or e.get("producer_id") == aid)]
        own = [e for e in own_all if _activity_event(e)]
        last = own[-1] if own else None
        p = _payload(last) if last else {}
        purpose = str(p.get("purpose") or p.get("stage") or "").lower()
        stage = _stage_for(str(last.get("event_type", "")), p) if last else None
        activity_position = POSITIONS.get(stage or purpose, 'idle')
        if last and last.get('event_type') in {'dialog.sent', 'dialog.received'}:
            activity_position = 'meeting'
        elif last and str(last.get('event_type', '')).startswith('memory.'):
            activity_position = 'archive'
        status_events = [e for e in own_all if str(e.get("event_type", "")).lower() in
                         {"agent.status", "agent.paused", "agent.resumed", "agent.offline"}]
        status_payload = _payload(status_events[-1]) if status_events else {}
        status = (a.get("status") if isinstance(a, dict) and a.get("status") else
                  status_payload.get("status") or ("working" if last else "idle"))
        if status_events and str(status_events[-1].get("event_type", "")).lower() == "agent.paused":
            status = "paused"
        elif status_events and str(status_events[-1].get("event_type", "")).lower() == "agent.offline":
            status = "offline"
        projected_agents.append({"id": aid, "name": a.get("name", aid) if isinstance(a, dict) else aid,
                                 "role": a.get("role") if isinstance(a, dict) else None, "status": status,
                                 "detail": p.get("summary") or p.get("detail"),
                                 "source_event_id": (status_events[-1] if status_events else last or {}).get("event_id"),
                                 "position_event_id": last.get("event_id") if last else None,
                                 "position": "idle" if status in {"idle", "offline", "stopped", "paused"} else activity_position,
                                 "heartbeat_at": a.get("heartbeat_at") if isinstance(a, dict) else None})

    baseline_cfg = _spec(pair, "baseline_config", _spec(pair, "baseline", {})) or {}
    fault_spec = pair.get("fault_spec") or (pair.get("immutable_spec") or {}).get("fault_spec") or {}
    injected_cfg = _spec(pair, "injected_config", None)
    if injected_cfg is None:
        injected_cfg = {**baseline_cfg, **fault_spec}
    injected_cfg = injected_cfg or {}
    current_cfg = config or {}
    cfg = {"baseline": deepcopy(baseline_cfg), "injected": deepcopy(injected_cfg), "current": deepcopy(current_cfg),
           "baseline_hash": _spec(pair, "baseline_config_hash") or _hash(baseline_cfg),
           "injected_hash": _spec(pair, "injected_config_hash") or _hash(injected_cfg),
           "current_hash": _hash(current_cfg)}

    records = [deepcopy(x) for x in (usage or [])]
    queue_events = [e for e in selected if str(e.get("event_type", "")).lower() in
                    {"model.queued", "model.admitted", "model.provider_started", "model.completed", "model.cancelled", "model.failed"}]
    def _total(r):
        actual = r.get("usage") if isinstance(r.get("usage"), dict) else r
        return actual.get("total_tokens") if isinstance(actual, dict) else None
    known = sum(int(_total(r) or 0) for r in records if _total(r) is not None and r.get("unknown") is not True)
    unknown_count = sum(1 for r in records if r.get("status") != "reserved" and (r.get("unknown") is True or _total(r) is None))
    pending = [e for e in queue_events if str(e.get("event_type")).lower() in {"model.queued", "model.admitted", "model.provider_started"}]
    terminal_ids = {(_payload(e).get("request_id") or _payload(e).get("usage_id")) for e in queue_events
                    if str(e.get("event_type")).lower() in {"model.completed", "model.cancelled", "model.failed"}}
    pending = [e for e in pending if (_payload(e).get("request_id") or _payload(e).get("usage_id")) not in terminal_ids]
    queue_records = {}
    for e in queue_events:
        p, typ = _payload(e), str(e.get("event_type", "")).lower()
        request_id = p.get("request_id") or p.get("usage_id")
        if not request_id:
            continue
        item = queue_records.setdefault(request_id, {"request_id": request_id, "purpose": p.get("purpose")})
        field = {"model.queued": "queued_at", "model.admitted": "admitted_at",
                 "model.provider_started": "provider_started_at", "model.completed": "completed_at",
                 "model.cancelled": "cancelled_at", "model.failed": "failed_at"}.get(typ)
        if field:
            item[field] = e.get("occurred_at")
        for timing in ('queue_ms', 'provider_ms', 'elapsed_ms'):
            if timing in p:
                item[timing] = p[timing]
    pending_tokens = (run or {}).get("reserved_tokens")
    if pending_tokens is None:
        pending_tokens = sum(int(_payload(e).get("reserved_tokens", _payload(e).get("tokens", 0)) or 0) for e in pending)
    unknown_tokens = (run or {}).get("unknown_reserved_tokens", 0)
    usage_out = {"known_tokens": known, "pending_tokens": pending_tokens,
                 "unknown_tokens": unknown_tokens,
                 "unknown_count": unknown_count, "records": records,
                 "scheduling": {"queued": len([e for e in queue_events if e.get("event_type") == "model.queued"]),
                                "admitted": len([e for e in queue_events if e.get("event_type") == "model.admitted"]),
                                "provider_started": len([e for e in queue_events if e.get("event_type") == "model.provider_started"]),
                                "pending": len({(_payload(e).get("request_id") or _payload(e).get("usage_id")) for e in pending}), "requests": list(queue_records.values())}}

    verification = next((_payload(e) for e in reversed(selected) if e.get("event_type") == "verification.completed"), None)
    rca = [_payload(e) for e in selected if str(e.get("event_type", "")).lower().startswith(("plan.", "dialog.", "failure.", "observation."))
           or e.get('event_type') in {'verification.completed', 'action.applied', 'incident.closed', 'incident.failed'}
           or "check" in str(e.get("event_type", "")).lower()]
    status = pair.get("status") or pair.get("pair_status") or (run or {}).get("status") or ("failed" if any(s["status"] == "failed" for s in stages) else "running")
    return {"pair_id": pair_id, "run_id": run_id, "arm": arm,
            "spec_hash": pair.get("spec_hash") or _hash(pair.get("immutable_spec", pair.get("spec", {}))),
            "pair_status": status, "run": deepcopy(run), "config": cfg, "stages": stages,
            "agents": projected_agents, "events": deepcopy(selected), "as_of_sequence": as_of_sequence, "usage": usage_out,
            "verification": deepcopy(verification), "rca": rca, "dependencies": deepcopy(dependencies),
            "comparison_integrity": deepcopy(pair.get("comparison_integrity", {})),
            "started_at": pair.get("started_at") or (run or {}).get("opened_at")}
