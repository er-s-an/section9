#!/bin/sh
set -eu

PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$PROJECT_DIR"
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
LOG_PATH=${1:-"$PROJECT_DIR/artifacts/external-support-agent/g5/linux-container-cold-install-$STAMP.log"}
LOG_PARENT=$(dirname -- "$LOG_PATH")
mkdir -p "$LOG_PARENT"
IMAGE=${SECTION9_LINUX_IMAGE:-node:22-bookworm}
PLATFORM=${SECTION9_LINUX_PLATFORM:-linux/arm64}

if ! command -v docker >/dev/null 2>&1; then
  echo "docker CLI is required for the Linux cold-install check" >&2
  exit 2
fi

if docker run --rm --user 0:0 --platform "$PLATFORM" --pull=missing --entrypoint /bin/sh \
  --mount "type=bind,source=$PROJECT_DIR,target=/source,readonly" \
  "$IMAGE" -ec '
    if command -v apt-get >/dev/null 2>&1; then
      apt-get update -qq
      DEBIAN_FRONTEND=noninteractive apt-get install -y -qq ca-certificates curl git python3
    elif command -v apk >/dev/null 2>&1; then
      apk add --no-cache ca-certificates curl git python3
    else
      echo "unsupported Linux package manager" >&2
      exit 2
    fi
    if ! command -v npm >/dev/null 2>&1; then
      if command -v apk >/dev/null 2>&1; then
        apk add --no-cache npm
      else
        DEBIAN_FRONTEND=noninteractive apt-get install -y -qq npm
      fi
    fi
    curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh
    PYTHON_VERSION=$(python3 -c "import sys; print(f\"{sys.version_info.major}.{sys.version_info.minor}\")")
    case "$PYTHON_VERSION" in 3.12|3.13) ;; *) uv python install 3.12 ;; esac
    mkdir -p /workspace
    cd /source
    tar --exclude="./.git" --exclude="./.venv" --exclude="./frontend/node_modules" \
      --exclude="./integrations/gep/node_modules" --exclude="./frontend/dist" \
      --exclude="./data" --exclude="./artifacts" --exclude="./.runtime" \
      --exclude="./.env" --exclude="./infra/.env" --exclude="./.pytest_cache" \
      --exclude="*/__pycache__" --exclude="*/.pytest_cache" --exclude=".DS_Store" \
      -cf - . | tar -xf - -C /workspace
    cd /workspace
    echo "clean_source_copy=created; developer_env_database_runtime_artifacts_excluded=true"
    ./scripts/install.sh
    .venv/bin/python -c "import sys; assert (3,12) <= sys.version_info[:2] < (3,14); print(\"python_version=\" + sys.version.split()[0])"
    node --version
    .venv/bin/python -m pytest tests/product tests/connectors -q
    if ! S9_PORT=19019 S9_DATA_DIR=/tmp/section9-cold-data ./scripts/start.sh; then
      python3 -c '\''import pathlib,re; p=pathlib.Path(".runtime/server.log"); text=p.read_text(errors="replace") if p.exists() else "server log missing"; lines=["[redacted credential-like diagnostic line]" if re.search(r"(?i)(authorization|api[_-]?key|secret|password|token|credential)", line) else line for line in text.splitlines()]; print("sanitized_startup_log_tail_begin"); print("\\n".join(lines[-80:])); print("sanitized_startup_log_tail_end")'\''
      exit 1
    fi
    python3 -c '\''import json,urllib.request; x=json.load(urllib.request.urlopen("http://127.0.0.1:19019/api/health",timeout=5)); assert x.get("status")=="running"; print("cold_health=running")'\''
    python3 -c '\''import json,urllib.error,urllib.request; u="http://127.0.0.1:19019/api/product/snapshot"; q=urllib.request.Request(u,headers={"Authorization":"Bearer invalid-container-test"});
try: urllib.request.urlopen(q,timeout=5)
except urllib.error.HTTPError as e: d=json.loads(e.read()); assert e.code==403 and d["error"]["code"]=="ROLE_FORBIDDEN"; print("worker_console_denial=403")
else: raise SystemExit("worker console request was not denied")'\''
    python3 -c '\''import json,urllib.error,urllib.request; b=json.dumps({"project_id":"another-project","environment_id":"local-test","run_tests":False}).encode(); q=urllib.request.Request("http://127.0.0.1:19019/api/product/connect",data=b,headers={"Content-Type":"application/json"});
try: urllib.request.urlopen(q,timeout=5)
except urllib.error.HTTPError as e: d=json.loads(e.read()); assert e.code==403 and d["error"]["code"]=="SCOPE_FORBIDDEN"; print("cross_project_denial=403")
else: raise SystemExit("cross-project connect was not denied")'\''
    python3 -c '\''import json,urllib.error,urllib.request; b=json.dumps({"project_id":"support-agent","environment_id":"local-test","sample":"order_truth"}).encode(); q=urllib.request.Request("http://127.0.0.1:19019/api/product/business-probe",data=b,headers={"Content-Type":"application/json"});
try: urllib.request.urlopen(q,timeout=5)
except urllib.error.HTTPError as e: d=json.loads(e.read()); assert e.code==409 and d["error"]["code"]=="EXTERNAL_RUNTIME_STOPPED"; print("stopped_runtime_guard=409")
else: raise SystemExit("stopped external runtime was not rejected")'\''
    python3 -c '\''import json,urllib.request; q=urllib.request.Request("http://127.0.0.1:19019/api/reset",data=b"{}",headers={"Content-Type":"application/json"}); d=json.load(urllib.request.urlopen(q,timeout=10)); assert isinstance(d.get("generation"),str); print("scoped_lab_reset=passed")'\''
    ./scripts/stop.sh --app-only
    python3 -c '\''import socket; s=socket.socket(); s.settimeout(1); code=s.connect_ex(("127.0.0.1",19019)); s.close(); assert code!=0; print("owned_service_stop=passed")'\''
    echo "linux_cold_install=PASS; model_calls=0; generated_local_credentials_not_printed=true"
  ' >"$LOG_PATH" 2>&1; then
  tail -n 35 "$LOG_PATH"
  echo "linux_container_log=$LOG_PATH"
else
  status=$?
  tail -n 100 "$LOG_PATH" >&2
  echo "linux_container_failure_log=$LOG_PATH" >&2
  exit "$status"
fi
