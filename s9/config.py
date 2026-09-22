from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
load_dotenv(ROOT / "infra" / ".env", override=False)
os.environ["NO_PROXY"] = ",".join(filter(None, [os.environ.get("NO_PROXY", ""), "127.0.0.1", "localhost", "host.docker.internal"]))
DATA = Path(os.getenv("S9_DATA_DIR", str(ROOT / "data")))
DATA.mkdir(parents=True, exist_ok=True)

MODEL = os.getenv("S9_MODEL", "evomap-gpt-5.6-luna")
MODEL_URL = os.getenv("S9_MODEL_URL", "https://api.evomap.ai/v1/chat/completions")
MODEL_KEY = os.getenv("EVOMAP_MODEL_API_KEY", "")
PORT = int(os.getenv("S9_PORT", "9019"))
MODEL_CONCURRENCY = int(os.getenv("S9_MODEL_CONCURRENCY", "2"))
MODEL_TIMEOUT = float(os.getenv("S9_MODEL_TIMEOUT", "60"))
RUN_TIMEOUT = float(os.getenv("S9_RUN_TIMEOUT", "180"))
RUN_TOKEN_BUDGET = int(os.getenv("S9_RUN_TOKEN_BUDGET", "16000"))
