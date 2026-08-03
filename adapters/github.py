"""GitHub draft-PR plans and a constrained runbook write adapter."""

from __future__ import annotations

import base64
import hashlib
import re
import unicodedata
from typing import Any
from urllib.parse import quote

import httpx

from adapters.config import LIVE_WRITE_REPOSITORY, validate_github_repository
from adapters.redaction import redact_secrets, redact_text
from contracts import (
    ActionProposal,
    Alert,
    BridgeComplaintRequest,
    GitHubPullRequestPreview,
    HypothesisSet,
)

GITHUB_API = "https://api.github.com"
BUGGY_PRICE_LINE = (
    "const price = yearly ? el.dataset.monthly : el.dataset.yearly;"
)
CORRECT_PRICE_LINE = (
    "const price = yearly ? el.dataset.yearly : el.dataset.monthly;"
)


class GitHubWriteError(RuntimeError):
    """Sanitized GitHub failure safe for reports and SSE."""


def patch_app_js(source: str) -> str:
    """Replace exactly one known buggy line, or fail without a write."""

    if source.count(BUGGY_PRICE_LINE) != 1:
        raise GitHubWriteError(
            "GitHub app.js patch precondition failed; exact buggy line "
            "must appear once"
        )
    return source.replace(BUGGY_PRICE_LINE, CORRECT_PRICE_LINE, 1)


def _github_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


async def _github_json(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    token: str,
    json_body: dict[str, object] | None = None,
) -> dict[str, Any]:
    try:
        response = await client.request(
            method,
            url,
            headers=_github_headers(token),
            json=json_body,
        )
        response.raise_for_status()
        body: Any = response.json()
    except (httpx.HTTPError, ValueError):
        raise GitHubWriteError(f"GitHub {method} request failed") from None
    if not isinstance(body, dict):
        raise GitHubWriteError("GitHub returned an invalid response")
    return body


async def create_github_draft_pull_request(
    *,
    token: str,
    repository: str,
    base: str,
    branch: str,
    title: str,
    body: str,
    linear_url: str,
    client: httpx.AsyncClient,
) -> dict[str, str]:
    """Patch app.js on one safe branch and open a draft PR."""

    if not token:
        raise GitHubWriteError("GitHub connector is not configured")
    if repository != LIVE_WRITE_REPOSITORY:
        raise GitHubWriteError("GitHub repository is not live-write allowlisted")
    safe_base = _safe_base_branch(base)
    if (
        not re.fullmatch(r"runbook/[a-z0-9][a-z0-9-]{0,90}", branch)
        or branch == safe_base
    ):
        raise GitHubWriteError("GitHub runbook branch is unsafe")

    repo_url = f"{GITHUB_API}/repos/{LIVE_WRITE_REPOSITORY}"
    encoded_base = quote(safe_base, safe="")
    base_ref = await _github_json(
        client,
        "GET",
        f"{repo_url}/git/ref/heads/{encoded_base}",
        token=token,
    )
    base_object = base_ref.get("object")
    base_sha = base_object.get("sha") if isinstance(base_object, dict) else None
    if not base_sha:
        raise GitHubWriteError("GitHub base branch did not return a commit SHA")

    await _github_json(
        client,
        "POST",
        f"{repo_url}/git/refs",
        token=token,
        json_body={"ref": f"refs/heads/{branch}", "sha": str(base_sha)},
    )

    encoded_branch = quote(branch, safe="")
    contents = await _github_json(
        client,
        "GET",
        f"{repo_url}/contents/app.js?ref={encoded_branch}",
        token=token,
    )
    content = contents.get("content")
    file_sha = contents.get("sha")
    if not isinstance(content, str) or not file_sha:
        raise GitHubWriteError("GitHub app.js response was incomplete")
    try:
        encoded_content = "".join(content.split())
        source = base64.b64decode(encoded_content, validate=True).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        raise GitHubWriteError("GitHub app.js content was invalid") from None
    patched = patch_app_js(source)

    await _github_json(
        client,
        "PUT",
        f"{repo_url}/contents/app.js",
        token=token,
        json_body={
            "message": "fix: restore yearly pricing selection",
            "content": base64.b64encode(patched.encode("utf-8")).decode("ascii"),
            "sha": str(file_sha),
            "branch": branch,
        },
    )

    pull = await _github_json(
        client,
        "POST",
        f"{repo_url}/pulls",
        token=token,
        json_body={
            "title": title,
            "head": branch,
            "base": safe_base,
            "body": f"{body}\n\nLinear issue: {linear_url}",
            "draft": True,
        },
    )
    if not pull.get("id") or not pull.get("html_url"):
        raise GitHubWriteError("GitHub draft PR response was incomplete")
    return {
        "id": redact_secrets(str(pull["id"]), token),
        "url": redact_secrets(str(pull["html_url"]), token),
        "branch": branch,
    }


def _safe_base_branch(value: str) -> str:
    candidate = value.strip()
    if (
        not candidate
        or len(candidate) > 128
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", candidate)
        or ".." in candidate
        or "//" in candidate
        or candidate.endswith((".", "/"))
        or any(part.endswith(".lock") for part in candidate.split("/"))
    ):
        return "main"
    return candidate


def github_runbook_branch(alert_id: str, base_branch: str) -> tuple[str, str]:
    """Return a safe runbook branch and validated base ref."""

    normalized = unicodedata.normalize("NFKD", alert_id)
    ascii_id = normalized.encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_id).strip("-")[:80].rstrip("-")
    digest = hashlib.sha256(alert_id.encode("utf-8")).hexdigest()[:10]
    if not slug:
        slug = f"alert-{digest}"

    base = _safe_base_branch(base_branch)
    branch = f"runbook/{slug}"
    if branch == base:
        branch = f"{branch}-{digest}"
    return branch, base


def build_github_pull_request_preview(
    *,
    alert: Alert,
    complaint: BridgeComplaintRequest,
    hypotheses: HypothesisSet | None,
    proposal: ActionProposal | None,
    configured: bool,
    repository: str | None,
    base_branch: str,
) -> GitHubPullRequestPreview:
    branch, safe_base = github_runbook_branch(alert.alert_id, base_branch)
    safe_repository = validate_github_repository(repository)
    leading = (
        redact_text(hypotheses.hypotheses[0].root_cause_description)
        if hypotheses and hypotheses.hypotheses
        else "Diagnosis pending"
    )
    action = (
        f"{proposal.action_type} on {redact_text(proposal.action_target)}"
        if proposal
        else "Remediation proposal pending"
    )
    body = "\n".join(
        (
            "Complaint Bridge dry-run plan. No repository was modified.",
            "",
            f"Alert: {alert.alert_id}",
            f"Complaint: {redact_text(complaint.text.strip())}",
            f"Leading hypothesis: {leading}",
            f"Runbook proposal: {action}",
            "",
            "Draft only. Human review is required; no merge is planned.",
        )
    )
    return GitHubPullRequestPreview(
        configured=configured and safe_repository is not None,
        repository=safe_repository,
        branch=branch,
        base=safe_base,
        title=f"runbook: investigate {alert.alert_id}",
        body=body,
    )
