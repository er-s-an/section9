from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
load_dotenv(ROOT / "infra" / ".env", override=False)
os.environ["NO_PROXY"] = ",".join(filter(None, [os.environ.get("NO_PROXY", ""), "127.0.0.1", "localhost", "host.docker.internal"]))

MODEL = os.getenv("S9_MODEL", "evomap-gpt-5.6-luna")
MODEL_URL = os.getenv("S9_MODEL_URL", "https://api.evomap.ai/v1/chat/completions")
MODEL_KEY = os.getenv("EVOMAP_MODEL_API_KEY", "")


def _text(name: str, default: str) -> str:
    value = os.getenv(name)
    return default if value is None or value.strip() == "" else value


def _integer(name: str, default: int) -> int:
    value = _text(name, str(default))
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer; got a non-empty invalid value") from exc


def _number(name: str, default: float) -> float:
    value = _text(name, str(default))
    try:
        return float(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be numeric; got a non-empty invalid value") from exc


DATA = Path(_text("S9_DATA_DIR", str(ROOT / "data")))
DATA.mkdir(parents=True, exist_ok=True)
PORT = _integer("S9_PORT", 9019)
MODEL_CONCURRENCY = _integer("S9_MODEL_CONCURRENCY", 2)
MODEL_TIMEOUT = _number("S9_MODEL_TIMEOUT", 60)
RUN_TIMEOUT = _number("S9_RUN_TIMEOUT", 180)
RUN_TOKEN_BUDGET = _integer("S9_RUN_TOKEN_BUDGET", 16000)
