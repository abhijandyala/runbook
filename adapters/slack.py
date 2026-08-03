"""Read-only Slack ingestion for complaint messages."""

from __future__ import annotations

from typing import Any

import httpx

from adapters.redaction import redact_secrets, redact_text

SLACK_API = "https://slack.com/api"
SKIPPABLE_HISTORY_ERRORS = frozenset(
    {"not_in_channel", "channel_not_found", "missing_scope"}
)


class SlackReadError(RuntimeError):
    """A sanitized Slack read failure that never includes credentials."""


class SlackWriteError(RuntimeError):
    """A sanitized Slack write failure that never includes credentials."""


async def post_completion_reply(
    *,
    token: str,
    channel: str | None,
    thread_ts: str | None,
    linear_url: str,
    github_url: str,
    client: httpx.AsyncClient,
) -> dict[str, str]:
    """Post one completion reply to the complaint's originating thread."""

    if not token:
        raise SlackWriteError("Slack connector is not configured")
    if not channel or not thread_ts:
        raise SlackWriteError(
            "Slack reply requires originating channel and thread_ts correlation"
        )
    text = (
        "Complaint Bridge remediation is complete and ready for human review. "
        f"Linear: {linear_url} Draft PR: {github_url}"
    )
    try:
        response = await client.post(
            f"{SLACK_API}/chat.postMessage",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json; charset=utf-8",
            },
            json={
                "channel": channel,
                "thread_ts": thread_ts,
                "text": text,
                "unfurl_links": False,
            },
        )
        response.raise_for_status()
        body = response.json()
    except (httpx.HTTPError, ValueError):
        raise SlackWriteError("Slack chat.postMessage request failed") from None
    if not isinstance(body, dict) or body.get("ok") is not True:
        error = body.get("error", "unknown_error") if isinstance(body, dict) else ""
        raise SlackWriteError(
            f"Slack chat.postMessage failed: {redact_secrets(str(error), token)}"
        )
    if not body.get("ts"):
        raise SlackWriteError("Slack chat.postMessage response omitted ts")
    return {"ts": redact_secrets(str(body["ts"]), token)}


def _payload(
    response: httpx.Response,
    operation: str,
    *,
    skippable_errors: frozenset[str] = frozenset(),
) -> dict[str, Any] | None:
    try:
        response.raise_for_status()
        body = response.json()
    except (httpx.HTTPError, ValueError):
        raise SlackReadError(f"Slack {operation} request failed") from None
    if not isinstance(body, dict) or not body.get("ok"):
        error = body.get("error", "unknown_error") if isinstance(body, dict) else ""
        if error in skippable_errors:
            return None
        raise SlackReadError(
            f"Slack {operation} failed: {redact_text(str(error))}"
        )
    return body


async def fetch_slack_messages(
    *,
    token: str,
    limit: int,
    client: httpx.AsyncClient | None = None,
) -> list[dict[str, str]]:
    """List accessible channels and read their history without any writes."""

    if not token:
        raise SlackReadError("SLACK_TOKEN is not configured")
    if not 1 <= limit <= 200:
        raise ValueError("limit must be between 1 and 200")

    headers = {"Authorization": f"Bearer {token}"}
    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=15.0)
    try:
        channel_response = await http.get(
            f"{SLACK_API}/conversations.list",
            headers=headers,
            params={
                "types": "public_channel",
                "exclude_archived": "true",
                "limit": 200,
            },
        )
        channel_body = _payload(channel_response, "conversations.list")
        if channel_body is None:
            raise SlackReadError("Slack conversations.list failed")
        messages: list[dict[str, str]] = []
        for raw_channel in channel_body.get("channels", []):
            if len(messages) >= limit:
                break
            if not isinstance(raw_channel, dict) or not raw_channel.get("id"):
                continue
            channel_id = str(raw_channel["id"])
            channel_name = str(raw_channel.get("name") or channel_id)
            history_response = await http.get(
                f"{SLACK_API}/conversations.history",
                headers=headers,
                params={
                    "channel": channel_id,
                    "limit": min(100, limit - len(messages)),
                },
            )
            history_body = _payload(
                history_response,
                "conversations.history",
                skippable_errors=SKIPPABLE_HISTORY_ERRORS,
            )
            if history_body is None:
                continue
            for raw_message in history_body.get("messages", []):
                if len(messages) >= limit:
                    break
                if (
                    not isinstance(raw_message, dict)
                    or raw_message.get("subtype")
                    or not raw_message.get("text")
                    or not raw_message.get("ts")
                ):
                    continue
                messages.append(
                    {
                        "channel": redact_text(channel_id),
                        "name": redact_text(channel_name),
                        "ts": redact_text(str(raw_message["ts"])),
                        "user": redact_text(str(raw_message.get("user") or "")),
                        "text": redact_text(str(raw_message["text"])),
                    }
                )
        return messages
    finally:
        if owns_client:
            await http.aclose()
