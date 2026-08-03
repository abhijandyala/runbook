"""Environment-only connector configuration with safe defaults."""

from __future__ import annotations

import os
import re

from dotenv import load_dotenv

load_dotenv()


def _value(name: str) -> str:
    return (os.getenv(name) or "").strip()


def connector_dry_run() -> bool:
    raw = _value("CONNECTOR_DRY_RUN")
    if not raw:
        return True
    normalized = raw.lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError("CONNECTOR_DRY_RUN must be true/false, yes/no, on/off, or 1/0")


def slack_token() -> str:
    return _value("SLACK_TOKEN")


def linear_team_id() -> str:
    return _value("LINEAR_TEAM_ID")


_GITHUB_OWNER = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?")
_GITHUB_REPOSITORY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}")


def validate_github_repository(raw: str | None) -> str | None:
    raw = (raw or "").strip()
    parts = raw.split("/")
    if (
        len(parts) != 2
        or not _GITHUB_OWNER.fullmatch(parts[0])
        or not _GITHUB_REPOSITORY.fullmatch(parts[1])
        or ".." in parts[1]
        or parts[1].endswith(".")
    ):
        return None
    return raw


def github_repository() -> str | None:
    return validate_github_repository(_value("GITHUB_REPO"))


def github_base_branch() -> str:
    return _value("GITHUB_BASE_BRANCH") or "main"


def connector_status() -> dict[str, bool]:
    return {
        "slack_configured": bool(slack_token()),
        "linear_configured": bool(_value("LINEAR_TOKEN") and linear_team_id()),
        "github_configured": bool(_value("GITHUB_TOKEN") and github_repository()),
    }
