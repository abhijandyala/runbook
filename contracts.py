"""Stable message contracts shared by every runbook pipeline stage."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class Contract(BaseModel):
    """Base contract that tolerates additive fields but never renames them."""

    model_config = ConfigDict(extra="allow")


class TaskEnvelope(Contract):
    role: Literal["diagnostician", "remediator"]
    payload: dict[str, Any]
    trace_id: str


class Alert(Contract):
    alert_id: str
    fired_at: str
    severity: Literal["critical", "warning", "info"]
    service: str
    metric: str
    value: float
    threshold: float
    labels: dict[str, str] = Field(default_factory=dict)
    annotations: dict[str, str] = Field(default_factory=dict)


class Evidence(Contract):
    source: Literal["graph", "linkup", "past_incident", "alert"]
    ref: str
    detail: str


class Hypothesis(Contract):
    hypothesis_id: str
    type: Literal[
        "recent_deploy",
        "dependency_failure",
        "past_pattern",
        "external_event",
        "unknown",
    ]
    root_cause_description: str
    affected_entity: str
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: list[Evidence] = Field(default_factory=list)
    reasoning: str


class HypothesisSet(Contract):
    alert_id: str
    hypotheses: list[Hypothesis]
    linkup_hits: int = Field(default=0, ge=0)
    graph_query_ms: int = Field(ge=0)
    generated_at: str
    guild_handoff_id: str | None = None


ActionType = Literal[
    "rollback",
    "restart",
    "scale",
    "flush_cache",
    "notify",
    "diagnostic",
    "none",
]
SafetyClass = Literal["safe", "standard", "destructive"]


class ActionProposal(Contract):
    proposal_id: str
    alert_id: str
    target_hypothesis_id: str
    action_type: ActionType
    action_target: str
    action_params: dict[str, Any] = Field(default_factory=dict)
    safety_class: SafetyClass
    remediator_confidence: float = Field(ge=0.0, le=1.0)
    runbook_source: str
    reasoning: str
    guild_task_id: str | None = None


class ReviewerDecision(Contract):
    proposal_id: str
    decision: Literal["approve", "reject", "modify"]
    modified_action: ActionProposal | None = None
    reviewer_note: str | None = None
    timestamp: str
    guild_task_id: str | None = None


class Resolution(Contract):
    incident_id: str
    alert_id: str
    final_action: ActionProposal | None = None
    outcome: Literal["verified", "partial", "no_effect", "rejected"]
    reviewer_decision: ReviewerDecision
    total_latency_ms: int = Field(ge=0)
    cost_usd: float = Field(ge=0.0)
