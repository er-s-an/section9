#!/usr/bin/env python3
"""Run the bounded Section 9 evaluation matrix against a live local API.

This tool intentionally does not retry a failed HTTP request or silently drop
an incomplete run.  It is an evidence collector, not a demo driver.
"""
from __future__ import annotations

import argparse
import json
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx


CONDITIONS = ("single", "muted", "swarm", "memory", "memory_jev")
DEFAULT_CONDITIONS = ("single", "muted", "swarm", "memory")
SUPPLEMENTAL_CONDITIONS = ("single_memory",)
SCENARIOS = ("prompt", "cost", "loop", "composite")
TERMINAL = {"resolved", "failed", "reset"}
POLL_LIMIT_SECONDS = 210.0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _json(response: httpx.Response) -> dict[str, Any]:
    try:
        value = response.json()
    except ValueError:
        value = {"error": {"message": response.text[:500]}}
    return value if isinstance(value, dict) else {"value": value}


def _cell_key(condition: str, scenario: str) -> str:
    return f"{condition}/{scenario}"


def _empty_cell(condition: str, scenario: str) -> dict[str, Any]:
    cell = {"condition": condition, "scenario": scenario, "n": 0, "success": 0, "failure": 0,
            "time_seconds": None, "tokens": None, "tokens_unknown": 0, "run_ids": [],
            "manifest_checks": [], "status": "待测"}
    if condition == "memory_jev":
        cell["disabled_reason"] = "no_real_model_artifact_jev_p2_disabled"
    return cell


def _manifest_check(run: dict[str, Any], expected: dict[str, Any] | None) -> dict[str, Any]:
    manifest = run.get("manifest") if isinstance(run.get("manifest"), dict) else {}
    fields = {"model": manifest.get("model"), "total_token_budget": manifest.get("total_token_budget"),
              "concurrency": manifest.get("request_concurrency_limit", manifest.get("concurrency")),
              "fixture_version": manifest.get("fixture_version"),
              "memory_snapshot": manifest.get("memory_condition", manifest.get("memory_snapshot"))}
    observed_identity = manifest.get("source_identity") if isinstance(manifest.get("source_identity"), dict) else None
    identity_keys = ("backend_source_hash", "frontend_build_hash", "dependency_lock_hash", "fixture_hash", "acceptance_contract_hash")
    expected_identity = expected.get("identity") if isinstance(expected, dict) else None
    identity_missing = [key for key in identity_keys if not observed_identity or not observed_identity.get(key)]
    identity_ok = bool(expected_identity) and not identity_missing and all(observed_identity.get(key) == expected_identity.get(key) for key in identity_keys)
    identity_status = "aligned" if identity_ok else ("unaligned_missing_identity" if identity_missing else "unaligned_hash_mismatch")
    check = {"run_id": run.get("id"), "environment": manifest.get("environment"),
             **fields,
             "environment_ok": manifest.get("environment") == "evaluation",
             "model_ok": expected is None or fields["model"] == expected.get("model"),
             "budget_ok": expected is None or fields["total_token_budget"] == expected.get("total_token_budget"),
             "concurrency_ok": expected is None or fields["concurrency"] == expected.get("concurrency"),
             "fixture_ok": expected is None or fields["fixture_version"] == expected.get("fixture_version"),
             "memory_snapshot_recorded": fields["memory_snapshot"] is not None,
             "source_identity": {key: observed_identity.get(key) for key in identity_keys} if observed_identity else None,
             "identity_status": identity_status, "identity_missing": identity_missing,
             "identity_ok": identity_ok}
    # Memory is the treatment under evaluation.  It is recorded for audit but
    # does not make otherwise identical model/budget/runtime runs unfair.
    check["aligned"] = bool(check["environment_ok"] and check["model_ok"] and check["budget_ok"]
                             and check["concurrency_ok"] and check["fixture_ok"] and check["identity_ok"])
    return check


def _add_run(cell: dict[str, Any], run: dict[str, Any], expected: dict[str, Any] | None) -> dict[str, Any]:
    status = run.get("status")
    cell["n"] += 1
    if status == "resolved":
        cell["success"] += 1
    else:
        cell["failure"] += 1
    cell["run_ids"].append(run.get("id"))
    elapsed = run.get("elapsed_s")
    if isinstance(elapsed, (int, float)):
        values = cell.setdefault("_times", [])
        values.append(float(elapsed))
    usage_unknown = bool(run.get("usage_unknown"))
    if usage_unknown or not isinstance(run.get("usage_tokens"), (int, float)):
        cell["tokens_unknown"] += 1
    else:
        cell.setdefault("_tokens", 0)
        cell["_tokens"] += int(run["usage_tokens"])
    check = _manifest_check(run, expected)
    cell["manifest_checks"].append(check)
    cell["status"] = "measured"
    return check


def _finish_cell(cell: dict[str, Any]) -> None:
    times = cell.pop("_times", [])
    tokens = cell.pop("_tokens", None)
    cell["time_seconds"] = round(statistics.median(times), 3) if times else None
    cell["tokens"] = tokens


def evaluate(args: argparse.Namespace) -> dict[str, Any]:
    conditions = tuple(args.conditions or DEFAULT_CONDITIONS)
    scenarios = tuple(args.scenarios or SCENARIOS)
    unknown_conditions = [x for x in conditions if x not in DEFAULT_CONDITIONS + SUPPLEMENTAL_CONDITIONS]
    unknown_scenarios = [x for x in scenarios if x not in SCENARIOS]
    if unknown_conditions or unknown_scenarios:
        raise ValueError(f"unsupported conditions={unknown_conditions} scenarios={unknown_scenarios}")
    cells = {_cell_key(c, s): _empty_cell(c, s) for c in CONDITIONS for s in SCENARIOS}
    for c in conditions:
        if c not in CONDITIONS:
            for s in scenarios:
                cells[_cell_key(c, s)] = _empty_cell(c, s)
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    raw_dir = output / "runs"
    planned = [(c, s, repeat) for c in conditions for s in scenarios for repeat in range(1, args.repeats + 1)]
    planned = planned[: args.max_runs]
    expected_by_scenario: dict[str, dict[str, Any]] = {}
    identity_unset = object()
    identity_baseline: dict[str, Any] | None | object = identity_unset
    errors: list[dict[str, Any]] = []
    started = _now()
    client = httpx.Client(base_url=args.base_url.rstrip("/"), trust_env=False,
                          timeout=httpx.Timeout(10.0, connect=10.0))
    try:
        for condition, scenario, repeat in planned:
            key = _cell_key(condition, scenario)
            request = {"scenario": scenario, "condition": condition, "seed": 42}
            record: dict[str, Any] = {"requested_at": _now(), "condition": condition,
                                      "scenario": scenario, "repeat": repeat, "request": request,
                                      "run": None, "error": None}
            try:
                # Reset and set policy for every run, even after a previous
                # failed run.  There is no retry around any of these calls.
                reset = client.post("/api/reset", json={})
                reset.raise_for_status()
                autonomy = client.put("/api/autonomy", json={"level": "L2"})
                autonomy.raise_for_status()
                created = client.post("/api/evaluate", json=request)
                created.raise_for_status()
                rid = _json(created).get("run_id")
                if not isinstance(rid, str) or not rid:
                    raise RuntimeError("evaluation API returned no run_id")
                deadline = time.monotonic() + POLL_LIMIT_SECONDS
                latest: dict[str, Any] = {}
                while time.monotonic() < deadline:
                    detail = client.get(f"/api/runs/{rid}")
                    detail.raise_for_status()
                    latest = _json(detail)
                    if latest.get("status") in TERMINAL:
                        break
                    time.sleep(1.0)
                else:
                    latest = {**latest, "id": rid, "status": "failed",
                              "failure_reason": "EVALUATION_TIMEOUT", "events": latest.get("events", []),
                              "usage": latest.get("usage", [])}
                record["run"] = latest
                manifest = latest.get("manifest") if isinstance(latest.get("manifest"), dict) else {}
                if identity_baseline is identity_unset:
                    identity_baseline = manifest.get("source_identity") if isinstance(manifest.get("source_identity"), dict) else None
                expected = expected_by_scenario.setdefault(scenario, {
                    "model": manifest.get("model"), "total_token_budget": manifest.get("total_token_budget"),
                    "concurrency": manifest.get("request_concurrency_limit", manifest.get("concurrency")),
                    "fixture_version": manifest.get("fixture_version"),
                    "memory_snapshot": manifest.get("memory_condition", manifest.get("memory_snapshot")),
                    "identity": identity_baseline,
                })
                _add_run(cells[key], latest, expected)
                _write_json(raw_dir / f"{rid}.json", record)
                print(json.dumps({"condition": condition, "scenario": scenario, "run_id": rid, "status": latest.get("status"), "elapsed_s": latest.get("elapsed_s"), "usage_tokens": latest.get("usage_tokens")}, ensure_ascii=False), flush=True)
            except Exception as exc:
                # Preserve any partial response and count this matrix cell as a
                # failure.  No automatic retry and no silent omission.
                record["error"] = {"type": type(exc).__name__, "message": str(exc)[:500]}
                cells[key]["n"] += 1
                cells[key]["failure"] += 1
                cells[key]["status"] = "measured"
                errors.append({"condition": condition, "scenario": scenario, "repeat": repeat,
                               "error": record["error"]})
                _write_json(raw_dir / f"error-{len(errors):04d}.json", record)
        for cell in cells.values():
            _finish_cell(cell)
    finally:
        try:
            client.post("/api/reset", json={})
        except Exception as exc:
            errors.append({"phase": "final_reset", "error": {"type": type(exc).__name__, "message": str(exc)[:500]}})
        client.close()
    measured = [cell for cell in cells.values() if cell["n"]]
    alignment = [check for cell in measured for check in cell["manifest_checks"]]
    measured_run_ids = [run_id for cell in cells.values() for run_id in cell.get("run_ids", []) if isinstance(run_id, str)]
    summary = {"started_at": started, "finished_at": _now(), "base_url": args.base_url,
               "run_id": measured_run_ids[-1] if measured_run_ids else None, "run_ids": measured_run_ids,
               "conditions": list(CONDITIONS), "scenarios": list(SCENARIOS), "repeats": args.repeats,
               "max_runs": args.max_runs, "executed_runs": sum(c["n"] for c in cells.values()),
               "cells": list(cells.values()), "errors": errors,
               "manifest_alignment": {"expected_by_scenario": expected_by_scenario, "checks": alignment,
                                       "all_aligned": bool(alignment) and all(x["aligned"] for x in alignment)},
               "comparability": {"mixed_conditions_incomparable": True,
                                 "note": "不同 condition 是不同协作/通信策略；不据此虚构优势。",
                                 "manifest_grouping": "同一 scenario 内比较；忽略 condition 和 scenario 本身不跨组比较",
                                 "memory_note": "memory 与非 memory 的 memory_snapshot 差异是被测处理条件，不单独视为不公平。"},
               "limitations": ["未测矩阵格显示待测", "unknown usage 不按零 token 计", "失败运行和原始响应均保留"]}
    _write_json(output / "summary.json", summary)
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Collect bounded real Section 9 evaluation runs")
    parser.add_argument("--conditions", nargs="+", default=None, help="subset of swarm single muted memory single_memory")
    parser.add_argument("--scenarios", nargs="+", default=None, help="subset of prompt cost loop composite")
    parser.add_argument("--require-pass", action="store_true", help="exit nonzero if any measured run fails or provenance is unaligned")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--max-runs", type=int, default=20)
    parser.add_argument("--base-url", default="http://127.0.0.1:9024")
    parser.add_argument("--output", default="artifacts/evaluation", help="directory receiving summary.json and raw runs")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.repeats < 1 or args.max_runs < 0:
        raise SystemExit("--repeats must be >=1 and --max-runs must be >=0")
    try:
        summary = evaluate(args)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps({"summary": str(Path(args.output) / "summary.json"),
                      "executed_runs": summary["executed_runs"], "errors": len(summary["errors"])}, ensure_ascii=False))
    if args.require_pass and (summary["errors"] or not summary["manifest_alignment"]["all_aligned"] or any(c["failure"] for c in summary["cells"])):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
