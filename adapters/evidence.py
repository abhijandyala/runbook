"""Assemble immutable, credential-free Complaint Bridge report contracts."""

from __future__ import annotations

from datetime import datetime, timezone

from adapters.config import (
    connector_dry_run,
    connector_status,
    github_base_branch,
    github_repository,
    linear_team_id,
)
from adapters.github import build_github_pull_request_preview
from adapters.linear import build_linear_ticket_preview
from adapters.redaction import redact_data
from adapters.reply import build_slack_reply_preview
from contracts import (
    ActionProposal,
    Alert,
    BridgeActionPreviews,
    BridgeComplaintRequest,
    BridgeReport,
    EvidenceBrief,
    HypothesisSet,
    Resolution,
    ReviewerDecision,
)

SPONSOR_BOUNDARIES = {
    "LaserData": "Owns alert, hypothesis, proposal, and resolution transport.",
    "FalkorDB": "Owns graph context and durable resolution memory.",
    "RocketRide": "Owns all runbook inference; no provider is called directly.",
    "Guild.ai": "Owns agent handoff and the human-review boundary.",
    "Complaint Bridge": (
        "Reads Slack and assembles previews; connector writes are not implemented."
    ),
}


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _stage(
    *,
    hypotheses: HypothesisSet | None,
    proposal: ActionProposal | None,
    decision: ReviewerDecision | None,
    resolution: Resolution | None,
) -> str:
    if resolution is not None:
        return "resolved"
    if decision is not None:
        return "reviewed"
    if proposal is not None:
        return "proposed"
    if hypotheses is not None:
        return "diagnosed"
    return "accepted"


def assemble_bridge_report(
    *,
    alert: Alert,
    complaint: BridgeComplaintRequest,
    hypotheses: HypothesisSet | None,
    proposal: ActionProposal | None,
    decision: ReviewerDecision | None,
    resolution: Resolution | None,
    guild_mode: str,
) -> BridgeReport:
    status = connector_status()
    evidence = [
        item
        for hypothesis in (hypotheses.hypotheses if hypotheses else [])
        for item in hypothesis.evidence
    ]
    leading = (
        hypotheses.hypotheses[0].root_cause_description
        if hypotheses and hypotheses.hypotheses
        else None
    )
    proposed_action = (
        f"{proposal.action_type} on {proposal.action_target}" if proposal else None
    )
    previews = BridgeActionPreviews(
        linear=build_linear_ticket_preview(
            alert=alert,
            complaint=complaint,
            hypotheses=hypotheses,
            proposal=proposal,
            configured=status["linear_configured"],
            team_id=linear_team_id(),
        ),
        github=build_github_pull_request_preview(
            alert=alert,
            complaint=complaint,
            hypotheses=hypotheses,
            proposal=proposal,
            configured=status["github_configured"],
            repository=github_repository(),
            base_branch=github_base_branch(),
        ),
        slack_reply=build_slack_reply_preview(
            alert=alert,
            complaint=complaint,
            proposal=proposal,
            resolution=resolution,
            configured=status["slack_configured"],
        ),
    )
    brief = EvidenceBrief(
        alert_id=alert.alert_id,
        summary=alert.annotations.get("summary", complaint.text.strip()),
        complaint_text=complaint.text.strip(),
        current_stage=_stage(
            hypotheses=hypotheses,
            proposal=proposal,
            decision=decision,
            resolution=resolution,
        ),
        leading_hypothesis=leading,
        evidence=evidence,
        proposed_action=proposed_action,
        decision=decision.decision if decision else None,
        outcome=resolution.outcome if resolution else None,
        assembled_at=_iso_now(),
    )
    report = BridgeReport(
        alert_id=alert.alert_id,
        alert=alert.model_copy(deep=True),
        complaint=complaint.model_copy(deep=True),
        hypotheses=hypotheses.model_copy(deep=True) if hypotheses else None,
        proposal=proposal.model_copy(deep=True) if proposal else None,
        decision=decision.model_copy(deep=True) if decision else None,
        resolution=resolution.model_copy(deep=True) if resolution else None,
        action_previews=previews,
        evidence_brief=brief,
        sponsor_boundaries=dict(SPONSOR_BOUNDARIES),
        guild_mode=guild_mode,
        connector_dry_run=connector_dry_run(),
    )
    return BridgeReport.model_validate(
        redact_data(report.model_dump(mode="python"))
    )
