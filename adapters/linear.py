"""Linear preview and narrowly scoped issue-create adapter."""

from __future__ import annotations

from typing import Any

import httpx

from adapters.redaction import redact_secrets, redact_text
from contracts import (
    ActionProposal,
    Alert,
    BridgeComplaintRequest,
    HypothesisSet,
    LinearTicketPreview,
)

LINEAR_GRAPHQL_API = "https://api.linear.app/graphql"
ISSUE_CREATE_MUTATION = """
mutation ComplaintBridgeIssueCreate($input: IssueCreateInput!) {
  issueCreate(input: $input) {
    success
    issue { id identifier url }
  }
}
"""


class LinearWriteError(RuntimeError):
    """Sanitized Linear failure safe for reports and SSE."""


def _linear_error(value: object, token: str) -> LinearWriteError:
    return LinearWriteError(
        f"Linear issueCreate failed: {redact_secrets(str(value), token)}"
    )


async def create_linear_issue(
    *,
    token: str,
    team_id: str,
    title: str,
    description: str,
    client: httpx.AsyncClient,
) -> dict[str, str]:
    """Create exactly one Linear issue through GraphQL."""

    if not token or not team_id:
        raise LinearWriteError("Linear connector is not configured")
    try:
        response = await client.post(
            LINEAR_GRAPHQL_API,
            headers={"Authorization": token, "Content-Type": "application/json"},
            json={
                "query": ISSUE_CREATE_MUTATION,
                "variables": {
                    "input": {
                        "teamId": team_id,
                        "title": title,
                        "description": description,
                    }
                },
            },
        )
        response.raise_for_status()
        body: Any = response.json()
    except (httpx.HTTPError, ValueError):
        raise LinearWriteError("Linear issueCreate request failed") from None

    if not isinstance(body, dict):
        raise LinearWriteError("Linear issueCreate returned an invalid response")
    if body.get("errors"):
        raise _linear_error(body["errors"], token)
    data = body.get("data")
    payload = data.get("issueCreate") if isinstance(data, dict) else None
    issue = payload.get("issue") if isinstance(payload, dict) else None
    if (
        not isinstance(payload, dict)
        or payload.get("success") is not True
        or not isinstance(issue, dict)
        or not issue.get("id")
        or not issue.get("identifier")
        or not issue.get("url")
    ):
        raise LinearWriteError("Linear issueCreate did not return an issue")
    return {
        "id": redact_secrets(str(issue["id"]), token),
        "identifier": redact_secrets(str(issue["identifier"]), token),
        "url": redact_secrets(str(issue["url"]), token),
    }


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
