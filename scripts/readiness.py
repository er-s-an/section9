#!/usr/bin/env python3
"""Report service readiness; business/model probing requires explicit --business."""
from __future__ import annotations
import argparse, json, urllib.request
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__); ap.add_argument("--base-url", default=None); ap.add_argument("--business", action="store_true"); args = ap.parse_args()
    env = {}
    p = ROOT / ".env"
    if p.is_file():
        for line in p.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k,v=line.split("=",1); env[k.strip()]=v.strip().strip('"').strip("'")
    base = args.base_url or f"http://127.0.0.1:{env.get('S9_PORT','9019')}"
    out = {"ready": "business_unchecked", "business_probe": "not_requested"}
    try:
        with OPENER.open(base.rstrip("/") + "/api/health", timeout=3) as response: health=json.loads(response.read())
        out["service"] = "alive"; out["identity_present"] = isinstance(health.get("identity"), dict)
    except Exception as exc:
        out.update(ready="degraded", service="unreachable", error=type(exc).__name__); print(json.dumps(out, ensure_ascii=False)); return 1
    if args.business:
        request=urllib.request.Request(base.rstrip("/") + "/api/chat", data=json.dumps({"message":"耳机收到3天已激活，是否仍能无理由退货？输出JSON，eligible为布尔值。"}, ensure_ascii=False).encode(), headers={"Content-Type":"application/json"})
        try:
            with OPENER.open(request, timeout=90) as response: body=json.loads(response.read())
            structured = body.get("structured") if isinstance(body, dict) else None
            truth_ok = isinstance(structured, dict) and structured.get("eligible") is True
            out["business_probe"]="passed" if body.get("status")=="success" and truth_ok else "failed"
            out["business_truth_checked"] = truth_ok
            # Keep the public readiness vocabulary small: verified business
            # truth is evidence attached to service_alive, never business_ready.
            out["ready"]="service_alive" if out["business_probe"]=="passed" else "degraded"
        except Exception as exc: out.update(business_probe="failed", ready="degraded", error=type(exc).__name__)
    print(json.dumps(out, ensure_ascii=False)); return 0 if out["ready"] != "degraded" else 1
if __name__ == "__main__": raise SystemExit(main())
