from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Autonomy(StrEnum):
    L0 = "L0"
    L1 = "L1"
    L2 = "L2"


class ChatRequest(StrictModel):
    message: str = Field(min_length=1, max_length=4000)


class Injection(StrictModel):
    scenario: Literal["prompt", "cost", "loop", "composite"]
    condition: Literal["swarm", "single", "muted", "memory", "single_memory"] = "swarm"
    seed: int = 42


class Action(StrictModel):
    type: Literal["set_prompt_revision", "apply_context_budget", "apply_retry_policy", "apply_config_bundle", "rollback_action"]
    values: dict = Field(default_factory=dict)


class PlanRequest(StrictModel):
    run_id: str
    task_id: str
    task_epoch: str
    expected_revision: str
    actions: list[Action] = Field(min_length=1, max_length=4)
    rationale: str = Field(min_length=1, max_length=3000)
    evidence_ids: list[str] = Field(default_factory=list)


class ExecuteRequest(StrictModel):
    plan_id: str
    grant_id: str
    idempotency_key: str = Field(min_length=1, max_length=128)


class MessageRequest(StrictModel):
    run_id: str
    kind: Literal["challenge", "agree", "disagree", "build_on", "synthesize", "hypothesis", "result"]
    content: str = Field(min_length=1, max_length=6000)
    evidence_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0, le=1)


class ClaimRequest(StrictModel):
    task_id: str


class AgentStatusRequest(StrictModel):
    status: str = Field(max_length=40)
    detail: str = Field(default="", max_length=500)
    task_id: str | None = None


class ModelRequest(StrictModel):
    run_id: str
    purpose: Literal["diagnose", "repair", "review", "single", "cost"]
    messages: list[dict] = Field(max_length=12)
    max_tokens: int = Field(default=700, ge=64, le=1800)
