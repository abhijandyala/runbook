"""Pure Linear issue payload previews; this module performs no I/O."""

from __future__ import annotations

from adapters.redaction import redact_text
from contracts import (
    ActionProposal,
    Alert,
    BridgeComplaintRequest,
    HypothesisSet,
    LinearTicketPreview,
)


def build_linear_ticket_preview(
    *,
    alert: Alert,
    complaint: BridgeComplaintRequest,
    hypotheses: HypothesisSet | None,
    proposal: ActionProposal | None,
    configured: bool,
    team_id: str,
) -> LinearTicketPreview:
    leading = (
        redact_text(hypotheses.hypotheses[0].root_cause_description)
        if hypotheses and hypotheses.hypotheses
        else "Diagnosis pending"
    )
    proposed_action = (
        f"{proposal.action_type} {redact_text(proposal.action_target)}"
        if proposal
        else "Remediation proposal pending"
    )
    description = "\n".join(
        (
            "Complaint Bridge dry-run preview. No Linear issue was created.",
            "",
            f"Alert: {alert.alert_id}",
            f"Service: {alert.service}",
            f"Severity: {alert.severity}",
            f"Slack channel: {complaint.channel or 'unknown'}",
            f"Customer report: {redact_text(complaint.text.strip())}",
            f"Leading hypothesis: {leading}",
            f"Proposed action: {proposed_action}",
        )
    )
    return LinearTicketPreview(
        configured=configured,
        payload={
            "teamId": team_id or None,
            "title": f"[{alert.severity}] Customer-reported failure in {alert.service}",
            "description": description,
        },
    )
