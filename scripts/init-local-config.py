#!/usr/bin/env python3
"""Create/check the two local dotenv files needed by the Section9 stack.

The generator never copies or prints values from an existing dotenv file.
Values are only written to newly-created files, making a second invocation
safe while allowing the checker to inspect variable names and permissions.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import secrets
import stat
import sys
from pathlib import Path


COMPOSE_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
ROOT_VARS = (
    "S9_MODEL",
    "EVOMAP_MODEL_API_KEY",
    "S9_PORT",
    "S9_MODEL_CONCURRENCY",
    "S9_MODEL_TIMEOUT",
    "S9_RUN_TIMEOUT",
    "S9_RUN_TOKEN_BUDGET",
)
ROOT_REQUIRED_VARS = ("EVOMAP_MODEL_API_KEY",)
NUMERIC_RULES = {
    "S9_PORT": (1, 65533, int),
    "S9_MODEL_CONCURRENCY": (1, 1024, int),
    "S9_MODEL_TIMEOUT": (0.1, 86400, float),
    "S9_RUN_TIMEOUT": (0.1, 86400, float),
    "S9_RUN_TOKEN_BUDGET": (1, 10_000_000, int),
}


def compose_vars(root: Path) -> list[str]:
    compose = root / "infra" / "docker-compose.yml"
    if not compose.is_file():
        raise SystemExit(f"missing compose file: {compose}")
    return sorted(set(COMPOSE_VAR_RE.findall(compose.read_text(encoding="utf-8"))))


def token(prefix: str = "") -> str:
    return prefix + secrets.token_hex(32)


def dotenv_value(value: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_./:@+-]*", value):
        return value
    return json.dumps(value)


def write_new(path: Path, values: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = "".join(f"{key}={dotenv_value(value)}\n" for key, value in values.items())
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(path, flags, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(payload)
    except Exception:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        raise
    os.chmod(path, 0o600)


def root_values() -> dict[str, str]:
    return {
        "S9_MODEL": "evomap-gpt-5.6-luna",
        "EVOMAP_MODEL_API_KEY": "",
        "S9_PORT": "9019",
        "S9_MODEL_CONCURRENCY": "2",
    }


def infra_values() -> dict[str, str]:
    postgres_password = token()
    public_key = token("pk-lf-")
    secret_key = token("sk-lf-")
    auth = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()
    return {
        "NEXTAUTH_URL": "http://127.0.0.1:9030",
        "POSTGRES_USER": "section9",
        "POSTGRES_PASSWORD": postgres_password,
        "POSTGRES_DB": "langfuse",
        "DATABASE_URL": f"postgresql://section9:{postgres_password}@postgres:5432/langfuse",
        "SALT": token(),
        "ENCRYPTION_KEY": token(),
        "NEXTAUTH_SECRET": token(),
        "CLICKHOUSE_USER": "clickhouse",
        "CLICKHOUSE_PASSWORD": token(),
        "REDIS_AUTH": token(),
        "MINIO_ROOT_USER": "minio-section9",
        "MINIO_ROOT_PASSWORD": token(),
        "LANGFUSE_INIT_ORG_ID": "section9-local",
        "LANGFUSE_INIT_ORG_NAME": "Section9 Local",
        "LANGFUSE_INIT_PROJECT_ID": "section9-observability",
        "LANGFUSE_INIT_PROJECT_NAME": "Section9 Observability",
        "LANGFUSE_INIT_PROJECT_PUBLIC_KEY": public_key,
        "LANGFUSE_INIT_PROJECT_SECRET_KEY": secret_key,
        "LANGFUSE_INIT_USER_EMAIL": "section9@localhost.invalid",
        "LANGFUSE_INIT_USER_NAME": "Section9 Operator",
        "LANGFUSE_INIT_USER_PASSWORD": token(),
        "LANGFUSE_AUTH": auth,
    }


def names_only(path: Path) -> set[str]:
    """Return dotenv names without retaining or displaying right-hand sides."""
    names: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name = stripped.split("=", 1)[0].strip()
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            names.add(name)
    return names


def values_only(path: Path) -> dict[str, str]:
    """Read local values for validation without ever printing them."""
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, value = stripped.split("=", 1)
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name.strip()):
            values[name.strip()] = value.strip().strip('"').strip("'")
    return values


def mode(path: Path) -> str:
    return oct(stat.S_IMODE(path.stat().st_mode))


def check(root: Path, required: list[str], *, require_model_key: bool = True) -> int:
    root_env = root / ".env"
    infra_env = root / "infra" / ".env"
    problems: list[str] = []
    if not root_env.is_file():
        problems.append("missing .env")
    if not infra_env.is_file():
        problems.append("missing infra/.env")
    if root_env.is_file():
        if mode(root_env) != "0o600":
            problems.append(f".env mode {mode(root_env)} (expected 0o600)")
        missing = set(ROOT_REQUIRED_VARS) - names_only(root_env)
        if missing:
            problems.append(".env missing names: " + ",".join(sorted(missing)))
        values = values_only(root_env)
        if require_model_key and not values.get("EVOMAP_MODEL_API_KEY", "").strip():
            problems.append("EVOMAP_MODEL_API_KEY is empty (business readiness is unavailable)")
        for name, (lower, upper, parser) in NUMERIC_RULES.items():
            raw = values.get(name, "")
            if not raw:
                continue
            try:
                number = parser(raw)
                if not lower <= number <= upper:
                    raise ValueError
            except (TypeError, ValueError):
                problems.append(f"{name} must be {parser.__name__} in [{lower}, {upper}]")
    if infra_env.is_file():
        if mode(infra_env) != "0o600":
            problems.append(f"infra/.env mode {mode(infra_env)} (expected 0o600)")
        missing = set(required) - names_only(infra_env)
        if missing:
            problems.append("infra/.env missing compose names: " + ",".join(sorted(missing)))
    if problems:
        print("check=fail; " + "; ".join(problems))
        return 1
    print(f"check=pass; compose_names={len(required)}; values_not_printed")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--check", action="store_true", help="strict read-only check, including a non-empty model key")
    parser.add_argument("--check-runtime", action="store_true", help="read-only startup check; an empty model key is allowed")
    parser.add_argument("--require-model-key", action="store_true", help="fail when the model key is empty")
    args = parser.parse_args()
    root = args.root.expanduser().resolve()
    required = compose_vars(root)
    if args.check or args.check_runtime:
        return check(root, required, require_model_key=args.check and not args.check_runtime or args.require_model_key)

    root_env = root / ".env"
    infra_env = root / "infra" / ".env"
    if root_env.exists():
        print("preserved existing .env")
    else:
        write_new(root_env, root_values())
        print("created .env (model key intentionally empty; values omitted)")
    if infra_env.exists():
        print("preserved existing infra/.env")
    else:
        values = infra_values()
        missing = set(required) - set(values)
        if missing:
            raise SystemExit("generator missing compose names: " + ",".join(sorted(missing)))
        write_new(infra_env, values)
        print(f"created infra/.env (compose_names={len(required)}; values omitted)")
    return check(root, required, require_model_key=False)


if __name__ == "__main__":
    sys.exit(main())
