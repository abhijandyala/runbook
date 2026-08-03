"""Guild integration seam awaiting the event mentor's verified API."""

from __future__ import annotations

import os
from typing import Any, Protocol

from dotenv import load_dotenv

from contracts import ActionProposal, ReviewerDecision


class GuildCoordinator(Protocol):
    """Operations the runtime needs from either Guild or its local fallback."""

    mode: str
    qualifying: bool

    async def ensure_registered(self) -> None:
        """Verify that the coordinator is ready before workers start."""
        ...

    async def record_handoff(
        self,
        from_role: str,
        to_role: str,
        trace_id: str,
        payload: dict[str, Any],
    ) -> str | None:
        """Record an agent handoff and return its Guild identity, if any."""
        ...

    async def create_review_task(
        self,
        proposal: ActionProposal,
    ) -> str | None:
        """Create a human-review task and return its Guild identity, if any."""
        ...

    async def record_decision(self, decision: ReviewerDecision) -> None:
        """Record the human reviewer's decision."""
        ...


class LocalHumanReviewFallback:
    """Keep local review working without representing it as Guild-backed."""

    mode = "local-human-review-fallback"
    qualifying = False

    async def ensure_registered(self) -> None:
        pass

    async def record_handoff(
        self,
        from_role: str,
        to_role: str,
        trace_id: str,
        payload: dict[str, Any],
    ) -> str | None:
        del from_role, to_role, trace_id, payload

    async def create_review_task(
        self,
        proposal: ActionProposal,
    ) -> str | None:
        del proposal

    async def record_decision(self, decision: ReviewerDecision) -> None:
        del decision


class GuildNotConfigured:
    """Fail every Guild operation until a verified adapter is installed."""

    mode = "not-configured"
    qualifying = False

    @staticmethod
    def _error() -> RuntimeError:
        return RuntimeError(
            "Guild is required or local fallback is disabled, but no verified "
            "real Guild adapter is configured."
        )

    async def ensure_registered(self) -> None:
        raise self._error()

    async def record_handoff(
        self,
        from_role: str,
        to_role: str,
        trace_id: str,
        payload: dict[str, Any],
    ) -> str | None:
        del from_role, to_role, trace_id, payload
        raise self._error()

    async def create_review_task(
        self,
        proposal: ActionProposal,
    ) -> str | None:
        del proposal
        raise self._error()

    async def record_decision(self, decision: ReviewerDecision) -> None:
        del decision
        raise self._error()


def _env_flag(name: str, *, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(
        f"{name} must be one of true/false, yes/no, on/off, or 1/0"
    )


def build_guild_coordinator(
    real_adapter: GuildCoordinator | None = None,
) -> GuildCoordinator:
    """Select a verified adapter or the explicitly permitted local mode."""

    load_dotenv()
    required = _env_flag("GUILD_REQUIRED", default=False)
    allow_local = _env_flag("GUILD_ALLOW_LOCAL_FALLBACK", default=True)

    if real_adapter is not None and real_adapter.qualifying:
        return real_adapter
    if required or not allow_local:
        return GuildNotConfigured()
    return LocalHumanReviewFallback()
