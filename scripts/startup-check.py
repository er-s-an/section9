#!/usr/bin/env python3
"""Cached-image startup measurement; touches only Section9's own services."""
import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/startup"


def command(args, log, timeout=180):
    subprocess.run(args, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=timeout)


def main():
    OUT.mkdir(exist_ok=True, parents=True)
    report = {"started_at": datetime.now(timezone.utc).isoformat(), "measurement": "cached_images_dependencies_stopped_containers_to_healthy",
              "fresh_image_download_measured": False, "volumes_preserved": True}
    with (OUT / "startup.log").open("w") as log:
        command([str(ROOT / ".venv/bin/python"), "scripts/environments.py", "stop"], log)
        command(["./scripts/stop.sh"], log)
        command(["docker", "compose", "--project-name", "section9-observe", "--env-file", "infra/.env", "-f", "infra/docker-compose.yml", "stop"], log)
        start = time.monotonic()
        command(["./scripts/start.sh"], log)
        report["operator_ready_s"] = round(time.monotonic() - start, 3)
        with httpx.Client(timeout=10, trust_env=False) as client:
            while time.monotonic() - start < 180:
                state = client.get("http://127.0.0.1:9019/api/state").json()
                if all(state["dependencies"].get(name, {}).get("status") == "available" for name in ["collector", "langfuse"]):
                    break
                time.sleep(1)
            else:
                raise RuntimeError("Dependencies were not healthy after 180 seconds")
            report["dependencies_healthy_s"] = round(time.monotonic() - start, 3)
            # A real business request, beyond health status or HTTP 200.
            result = client.post("http://127.0.0.1:9019/api/chat", json={"message": '耳机收到3天已激活，是否仍能无理由退货？输出JSON，eligible为布尔值。'}, timeout=80).json()
            report["business_request"] = result
            report["business_ready_s"] = round(time.monotonic() - start, 3)
            assert result.get("status") == "success" and result.get("structured", {}).get("eligible") is True
            client.post("http://127.0.0.1:9019/api/agents/join", json={}).raise_for_status()
            report["passed"] = True
            report["final_url"] = "http://127.0.0.1:9019"
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    (OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps({k: v for k, v in report.items() if k != "business_request"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
