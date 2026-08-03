"""Ordered, opt-in Complaint Bridge connector writes."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime, timezone

import httpx

from adapters.config import (
    LIVE_WRITE_REPOSITORY,
    github_base_branch,
    github_repository,
    github_token,
    linear_team_id,
    linear_token,
    live_writes_eligible,
    slack_token,
)
from adapters.github import (
    GitHubWriteError,
    create_github_draft_pull_request,
    github_runbook_branch,
)
from adapters.linear import LinearWriteError, create_linear_issue
from adapters.redaction import redact_data, redact_secrets
from adapters.slack import SlackWriteError, post_completion_reply
from contracts import (
    ActionProposal,
    Alert,
    BridgeActionPreviews,
    BridgeComplaintRequest,
    ConnectorActionResult,
    ConnectorWriteResult,
)

ActionCallback = Callable[[ConnectorActionResult], Awaitable[None]]

_ACTION_SPECS = (
    ("linear", "issueCreate"),
    ("github", "draftPullRequest"),
    ("slack", "chat.postMessage"),
)


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _action(
    index: int,
    status: str,
    **updates: object,
) -> ConnectorActionResult:
    connector, operation = _ACTION_SPECS[index]
    return ConnectorActionResult.model_validate(
        redact_data(
            {
                "connector": connector,
                "operation": operation,
                "status": status,
                **updates,
            }
        )
    )


async def execute_connector_writes(
    *,
    alert: Alert,
    complaint: BridgeComplaintRequest,
    proposal: ActionProposal,
    previews: BridgeActionPreviews,
    on_action: ActionCallback,
    client: httpx.AsyncClient | None = None,
) -> ConnectorWriteResult:
    """Execute the fixed Linear → GitHub → Slack workflow once."""

    if not live_writes_eligible():
        raise RuntimeError("Connector live writes are not eligible")
    if github_repository() != LIVE_WRITE_REPOSITORY:
        raise RuntimeError("GitHub repository is not live-write allowlisted")

    started_at = _iso_now()
    for index in range(len(_ACTION_SPECS)):
        await on_action(_action(index, "pending"))

    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=20.0)
    results: list[ConnectorActionResult] = []
    linear_url: str | None = None
    github_url: str | None = None
    slack_ts: str | None = None

    try:
        for index in range(len(_ACTION_SPECS)):
            await on_action(_action(index, "running"))
            try:
                if index == 0:
                    description = str(previews.linear.payload["description"]).replace(
                        "Complaint Bridge dry-run preview. No Linear issue was created.",
                        "Complaint Bridge live write approved by a human reviewer.",
                        1,
                    )
                    artifact = await create_linear_issue(
                        token=linear_token(),
                        team_id=linear_team_id(),
                        title=str(previews.linear.payload["title"]),
                        description=description,
                        client=http,
                    )
                    linear_url = artifact["url"]
                    result = _action(
                        index,
                        "succeeded",
                        resource_id=artifact["id"],
                        identifier=artifact["identifier"],
                        url=linear_url,
                    )
                elif index == 1:
                    if linear_url is None:
                        raise RuntimeError("Linear issue link is unavailable")
                    branch, base = github_runbook_branch(
                        proposal.proposal_id,
                        github_base_branch(),
                    )
                    pull_body = previews.github.body.replace(
                        "Complaint Bridge dry-run plan. No repository was modified.",
                        "Complaint Bridge live write approved by a human reviewer.",
                        1,
                    )
                    artifact = await create_github_draft_pull_request(
                        token=github_token(),
                        repository=github_repository() or "",
                        base=base,
                        branch=branch,
                        title=previews.github.title,
                        body=pull_body,
                        linear_url=linear_url,
                        client=http,
                    )
                    github_url = artifact["url"]
                    result = _action(
                        index,
                        "succeeded",
                        resource_id=artifact["id"],
                        url=github_url,
                        metadata={"branch": artifact["branch"], "base": base},
                    )
                else:
                    if linear_url is None or github_url is None:
                        raise RuntimeError("Connector artifact links are unavailable")
                    artifact = await post_completion_reply(
                        token=slack_token(),
                        channel=complaint.channel,
                        thread_ts=complaint.external_id,
                        linear_url=linear_url,
                        github_url=github_url,
                        client=http,
                    )
                    slack_ts = artifact["ts"]
                    result = _action(
                        index,
                        "succeeded",
                        resource_id=slack_ts,
                    )
            except (
                GitHubWriteError,
                KeyError,
                LinearWriteError,
                RuntimeError,
                SlackWriteError,
                ValueError,
            ) as exc:
                error = redact_secrets(
                    str(exc),
                    linear_token(),
                    github_token(),
                    slack_token(),
                ) or "Connector action failed"
                result = _action(index, "failed", error=error)
                results.append(result)
                await on_action(result)
                for skipped_index in range(index + 1, len(_ACTION_SPECS)):
                    skipped = _action(
                        skipped_index,
                        "failed",
                        error=(
                            "Not attempted because an earlier connector action failed"
                        ),
                    )
                    results.append(skipped)
                    await on_action(skipped)
                break
            results.append(result)
            await on_action(result)
    finally:
        if owns_client:
            await http.aclose()

    succeeded = sum(item.status == "succeeded" for item in results)
    status = (
        "succeeded"
        if succeeded == len(_ACTION_SPECS)
        else "partial"
        if succeeded
        else "failed"
    )
    return ConnectorWriteResult(
        proposal_id=proposal.proposal_id,
        alert_id=alert.alert_id,
        status=status,
        actions=results,
        linear_issue_url=linear_url,
        github_pull_request_url=github_url,
        slack_message_ts=slack_ts,
        started_at=started_at,
        completed_at=_iso_now(),
    )
