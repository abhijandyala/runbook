"""Grounded Slack reply previews with no post-message implementation."""

from __future__ import annotations

from adapters.redaction import redact_text
from contracts import (
    ActionProposal,
    Alert,
    BridgeComplaintRequest,
    Resolution,
    SlackReplyPreview,
)


def build_slack_reply_preview(
    *,
    alert: Alert,
    complaint: BridgeComplaintRequest,
    proposal: ActionProposal | None,
    resolution: Resolution | None,
    configured: bool,
) -> SlackReplyPreview:
    lines = [
        (
            f"We received your report about {redact_text(alert.service)} and opened "
            f"investigation {alert.alert_id}."
        ),
    ]
    if proposal is not None:
        lines.append(
            "The current runbook proposal is "
            f"{proposal.action_type} on {redact_text(proposal.action_target)}; "
            "it remains subject to human review."
        )
    else:
        lines.append("Diagnosis and a reviewed remediation proposal are pending.")
    if resolution is not None:
        lines.append(f"The final recorded outcome is {resolution.outcome}.")
    lines.append("This is a preview; no Slack message was posted.")
    return SlackReplyPreview(
        configured=configured,
        channel=redact_text(complaint.channel) if complaint.channel else None,
        thread_ts=(
            redact_text(complaint.external_id) if complaint.external_id else None
        ),
        text=" ".join(lines),
    )
