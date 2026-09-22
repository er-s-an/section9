#!/usr/bin/env python3
"""Read-only local setup checks with actionable, credential-free diagnostics."""
from __future__ import annotations
import argparse, json, shutil, socket, subprocess, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
NUMERIC = {"S9_PORT": (1, 65533, int), "S9_MODEL_CONCURRENCY": (1, 1024, int),
           "S9_MODEL_TIMEOUT": (0.1, 86400, float), "S9_RUN_TIMEOUT": (0.1, 86400, float),
           "S9_RUN_TOKEN_BUDGET": (1, 10_000_000, int)}
TEXT_DEFAULTS = {"S9_MODEL": "evomap-gpt-5.6-luna", "S9_MODEL_URL": "https://api.evomap.ai/v1/chat/completions",
                 "S9_ENVIRONMENT": "demo", "S9_DATA_DIR": str(ROOT / "data")}

def dotenv(path: Path) -> dict[str, str]:
    if not path.is_file(): return {}
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1); out[key.strip()] = value.strip().strip('"').strip("'")
    return out

def check_port(port: int) -> str:
    if not 1 <= port <= 65535:
        return "invalid"
    sock = socket.socket(); sock.settimeout(.2)
    try: sock.bind(("127.0.0.1", port)); return "available"
    except OSError: return "occupied"
    finally: sock.close()

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base-url", default=None, help="existing local service URL (default uses S9_PORT)")
    ap.add_argument("--json", action="store_true", dest="as_json")
    args = ap.parse_args()
    values = dotenv(ROOT / ".env")
    result: dict = {"checks": [], "ready": "degraded"}
    def add(name, status, detail, action=""):
        item = {"name": name, "status": status, "detail": detail}
        if action: item["action"] = action
        result["checks"].append(item)
    root_env = ROOT / ".env"
    infra_env = ROOT / "infra" / ".env"
    add("config", "pass" if root_env.is_file() and infra_env.is_file() else "fail",
        "local dotenv files present" if root_env.is_file() and infra_env.is_file() else "missing .env or infra/.env",
        "run ./scripts/install.sh")
    key = values.get("EVOMAP_MODEL_API_KEY", "")
    add("model_key", "pass" if key else "fail", "configured" if key else "empty; business readiness is unavailable",
        "set EVOMAP_MODEL_API_KEY in .env (value is never printed)")
    numeric_ok = True
    for name, (lo, hi, parser) in NUMERIC.items():
        raw = values.get(name, "")
        if not raw: continue
        try: number = parser(raw); valid = lo <= number <= hi
        except (ValueError, TypeError): valid = False
        numeric_ok &= valid
        if not valid: add(name, "fail", f"must be {parser.__name__} in [{lo}, {hi}]", "fix .env and rerun doctor")
    if numeric_ok: add("numeric_config", "pass", "numeric settings are valid")
    port_raw = values.get("S9_PORT", "9019")
    try: port = int(port_raw)
    except (ValueError, TypeError): port = 0
    if not 1 <= port <= 65533:
        add("port", "fail", "S9_PORT must be an integer in [1, 65533]; agent ingress uses S9_PORT+2", "fix .env and rerun doctor")
    else:
        main_state, agent_state = check_port(port), check_port(port + 2)
        add("port", "pass" if main_state == "available" and agent_state == "available" else "warn",
            f"operator={main_state}; agent={agent_state}", "stop this checkout or choose free S9_PORT")
    for name, default in TEXT_DEFAULTS.items():
        add("config:" + name, "pass", "set" if values.get(name, default) else "default")
    add("python", "pass" if (ROOT / ".venv/bin/python").is_file() else "fail", "virtualenv available" if (ROOT / ".venv/bin/python").is_file() else "missing .venv", "run ./scripts/install.sh")
    # The built server serves frontend/dist; node_modules are install-time only.
    runtime_ok = (ROOT / "frontend/dist").is_dir()
    add("javascript_runtime", "pass" if runtime_ok else "fail", "frontend build available" if runtime_ok else "frontend/dist missing", "run ./scripts/install.sh")
    if not (ROOT / "frontend/node_modules").is_dir() or not (ROOT / "integrations/gep/node_modules").is_dir():
        add("install_cache", "warn", "node_modules missing; rebuilding and GEP SDK writeback may be unavailable", "run ./scripts/install.sh to restore build and GEP dependencies")
    docker = shutil.which("docker")
    if not docker: add("docker", "warn", "docker executable not found; observability is unavailable", "install/start Docker Desktop if observability is needed")
    else:
        try:
            info = subprocess.run([docker, "info", "--format", "{{json .}}"], capture_output=True, text=True, timeout=3, check=True)
            data = json.loads(info.stdout)
            mem = int(data.get("MemTotal", 0)); cpus = int(data.get("NCPU", 0))
            state = "pass" if mem >= 8 * 1024**3 and cpus >= 4 else "warn"
            add("docker", state, f"daemon reachable; memory={mem // (1024**3)}GiB; cpu={cpus}", "allocate at least 8GiB/4CPU for observability" if state == "warn" else "")
        except Exception: add("docker", "warn", "daemon unavailable; app can still run without observability", "start Docker Desktop")
    base = args.base_url or f"http://127.0.0.1:{port}"
    try:
        with OPENER.open(base.rstrip("/") + "/api/health", timeout=2) as response: health = json.loads(response.read())
        add("service", "pass", "HTTP /api/health is running")
        result["ready"] = "service_alive" if key else "degraded"
        result["health"] = {"status": health.get("status"), "identity_present": isinstance(health.get("identity"), dict)}
    except Exception:
        add("service", "warn", "service is not running; no business request was made", "run ./scripts/start.sh")
    if args.as_json: print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"ready={result['ready']}")
        for item in result["checks"]: print(f"{item['status']} {item['name']}: {item['detail']}" + (f"; {item['action']}" if item.get("action") else ""))
    return 1 if any(item["status"] == "fail" for item in result["checks"]) else 0
if __name__ == "__main__": raise SystemExit(main())
