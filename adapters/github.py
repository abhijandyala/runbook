"""Pure GitHub draft-PR plans; this module never clones, pushes, or writes."""

from __future__ import annotations

import hashlib
import re
import unicodedata

from adapters.config import validate_github_repository
from adapters.redaction import redact_text
from contracts import (
    ActionProposal,
    Alert,
    BridgeComplaintRequest,
    GitHubPullRequestPreview,
    HypothesisSet,
)


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
