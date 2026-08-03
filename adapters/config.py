"""Environment-only connector configuration with safe defaults."""

from __future__ import annotations

import os
import re

from dotenv import load_dotenv

load_dotenv()

LIVE_WRITE_REPOSITORY = "abhijandyala/testing24"


def _value(name: str) -> str:
    return (os.getenv(name) or "").strip()


def _boolean(name: str, *, default: bool) -> bool:
    raw = _value(name)
    if not raw:
        return default
    normalized = raw.lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(
        f"{name} must be true/false, yes/no, on/off, or 1/0"
    )


def connector_dry_run() -> bool:
    return _boolean("CONNECTOR_DRY_RUN", default=True)


def connector_live_writes() -> bool:
    return _boolean("CONNECTOR_LIVE_WRITES", default=False)


def slack_token() -> str:
    return _value("SLACK_TOKEN")


def linear_token() -> str:
    return _value("LINEAR_TOKEN")


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


def github_token() -> str:
    return _value("GITHUB_TOKEN")


def github_base_branch() -> str:
    return _value("GITHUB_BASE_BRANCH") or "main"


def live_writes_environment_enabled() -> bool:
    return not connector_dry_run() and connector_live_writes()


def live_writes_eligible() -> bool:
    status = connector_status()
    return (
        live_writes_environment_enabled()
        and all(status.values())
        and github_repository() == LIVE_WRITE_REPOSITORY
    )


def connector_status() -> dict[str, bool]:
    return {
        "slack_configured": bool(slack_token()),
        "linear_configured": bool(linear_token() and linear_team_id()),
        "github_configured": bool(github_token() and github_repository()),
    }
