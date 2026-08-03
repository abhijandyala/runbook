"""Human-review policy and resolution assembly."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from contracts import ActionProposal, Alert, Resolution, ReviewerDecision

OUTCOME_TIMEOUT_SECONDS = 60.0
Outcome = Literal["verified", "partial", "no_effect"]


@dataclass(frozen=True)
class OutcomeObservation:
    """Result of watching for a correlated simulated recovery signal."""

    status: Literal["verified", "partial", "no_effect", "timeout"]
    outcome: Outcome
    alert: Alert | None


def classify_outcome(
    *,
    baseline_value: float,
    observed_value: float,
    threshold: float,
) -> Outcome:
    """Classify metric movement without assuming that an action was real."""

    if observed_value <= threshold:
        return "verified"
    if observed_value < baseline_value:
        return "partial"
    return "no_effect"


def is_correlated_simulated_alert(alert: Alert, proposal_id: str) -> bool:
    """Exclude ordinary/judge alerts from simulated action verification."""

    return (
        alert.annotations.get("simulated") == "true"
        and alert.annotations.get("proposal_id") == proposal_id
    )


async def watch_for_outcome(
    alerts: AsyncIterator[Alert | dict[str, object]],
    *,
    proposal_id: str,
    baseline_value: float,
    threshold: float,
    timeout_seconds: float = OUTCOME_TIMEOUT_SECONDS,
) -> OutcomeObservation:
    """Wait at most 60 seconds for a correlated simulated post-action alert."""

    try:
        async with asyncio.timeout(timeout_seconds):
            async for raw_alert in alerts:
                alert = Alert.model_validate(raw_alert)
                if not is_correlated_simulated_alert(alert, proposal_id):
                    continue
                outcome = classify_outcome(
                    baseline_value=baseline_value,
                    observed_value=alert.value,
                    threshold=threshold,
                )
                return OutcomeObservation(
                    status=outcome,
                    outcome=outcome,
                    alert=alert,
                )
    except TimeoutError:
        return OutcomeObservation(
            status="timeout",
            outcome="no_effect",
            alert=None,
        )
    return OutcomeObservation(
        status="timeout",
        outcome="no_effect",
        alert=None,
    )


def requires_human_review(
    proposal: ActionProposal,
    *,
    diagnostician_confidence: float,
) -> bool:
    return (
        diagnostician_confidence < 0.5
        or proposal.safety_class in {"standard", "destructive"}
        or proposal.remediator_confidence <= 0.9
    )


def decide(
    proposal: ActionProposal,
    *,
    decision: str,
    modified_action: ActionProposal | None = None,
    reviewer_note: str | None = None,
    typed_confirmation: str | None = None,
    guild_task_id: str | None = None,
) -> ReviewerDecision:
    if decision not in {"approve", "reject", "modify"}:
        raise ValueError(f"Unsupported reviewer decision: {decision}")
    if decision == "modify" and modified_action is None:
        raise ValueError("modified_action is required when decision is modify")
    effective_action = (
        modified_action
        if decision == "modify"
        else proposal
        if decision == "approve"
        else None
    )
    if (
        effective_action is not None
        and effective_action.safety_class == "destructive"
        and typed_confirmation != effective_action.action_target
    ):
        raise ValueError(
            "Destructive proposals require typed_confirmation to exactly match "
            "action_target"
        )
    return ReviewerDecision(
        proposal_id=proposal.proposal_id,
        decision=decision,
        modified_action=modified_action,
        reviewer_note=reviewer_note,
        timestamp=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        guild_task_id=guild_task_id,
    )


def effective_action_for(
    proposal: ActionProposal,
    reviewer_decision: ReviewerDecision,
) -> ActionProposal | None:
    if reviewer_decision.decision == "reject":
        return None
    if reviewer_decision.decision == "approve":
        return proposal
    modified = reviewer_decision.modified_action
    if modified is None:
        raise ValueError("A modify decision requires modified_action")
    if (
        modified.proposal_id != proposal.proposal_id
        or modified.alert_id != proposal.alert_id
    ):
        raise ValueError(
            "modified_action must preserve proposal_id and alert_id"
        )
    return modified


def resolve(
    proposal: ActionProposal,
    reviewer_decision: ReviewerDecision,
    *,
    outcome: str,
    total_latency_ms: int,
    cost_usd: float = 0.0,
) -> Resolution:
    if reviewer_decision.decision == "reject":
        final_action = None
        outcome = "rejected"
    else:
        final_action = effective_action_for(proposal, reviewer_decision)
    return Resolution(
        incident_id=f"inc_{uuid4().hex[:10]}",
        alert_id=proposal.alert_id,
        final_action=final_action,
        outcome=outcome,
        reviewer_decision=reviewer_decision,
        total_latency_ms=total_latency_ms,
        cost_usd=cost_usd,
    )
