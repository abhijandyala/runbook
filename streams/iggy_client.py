"""Thin Laser SDK wrapper for runbook's durable event topics."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from types import TracebackType
from typing import Any, Self

import laser_sdk as ls
from dotenv import load_dotenv
from pydantic import BaseModel, TypeAdapter

from contracts import ActionProposal, Alert, HypothesisSet, Resolution, ReviewerDecision
from streams.stream_names import (
    ALL_TOPICS,
    STREAM_SENTRY,
    TOPIC_ALERTS,
    TOPIC_HYPOTHESES,
    TOPIC_PROPOSALS,
    TOPIC_RESOLUTIONS,
)

TOPIC_CONTRACTS: dict[str, type[BaseModel | dict]] = {
    TOPIC_ALERTS: Alert,
    TOPIC_HYPOTHESES: HypothesisSet,
    TOPIC_PROPOSALS: ActionProposal,
    TOPIC_RESOLUTIONS: dict,
}
RESOLUTION_CONTRACT = TypeAdapter(ReviewerDecision | Resolution)


class IggyClient:
    """Own one Laser connection and expose only runbook's required operations."""

    def __init__(
        self,
        connection_string: str | None = None,
        stream_name: str | None = None,
    ) -> None:
        load_dotenv()
        self.connection_string = connection_string or os.getenv(
            "LASER_CONNECTION_STRING"
        )
        if not self.connection_string:
            raise ValueError("LASER_CONNECTION_STRING is required")
        self.stream_name = stream_name or os.getenv("LASER_STREAM", STREAM_SENTRY)
        self._laser: ls.Laser | None = None

    async def __aenter__(self) -> Self:
        self._laser = await ls.Laser.connect(
            self.connection_string,
            stream=self.stream_name,
        )
        await self._laser.__aenter__()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._laser is not None:
            await self._laser.__aexit__(exc_type, exc, traceback)
            self._laser = None

    @property
    def laser(self) -> ls.Laser:
        if self._laser is None:
            raise RuntimeError("Use IggyClient as an async context manager")
        return self._laser

    def _topic(self, topic_name: str) -> ls.Topic:
        contract = TOPIC_CONTRACTS.get(topic_name)
        if contract is None:
            raise ValueError(f"Unknown runbook topic: {topic_name}")
        return self.laser.stream(self.stream_name).topic(topic_name, cls=contract)

    async def ensure_topology(self) -> None:
        stream = self.laser.stream(self.stream_name)
        await stream.ensure()
        for topic_name in ALL_TOPICS:
            await self._topic(topic_name).ensure(partitions=1)

    async def publish(
        self,
        topic_name: str,
        message: BaseModel | dict[str, Any],
    ) -> None:
        expected = TOPIC_CONTRACTS.get(topic_name)
        if expected is None:
            raise ValueError(f"Unknown runbook topic: {topic_name}")
        if topic_name == TOPIC_RESOLUTIONS:
            validated = RESOLUTION_CONTRACT.validate_python(message)
            body: BaseModel | dict[str, Any] = validated.model_dump(mode="json")
        else:
            assert expected is not dict
            body = expected.model_validate(message)
        await self._topic(topic_name).publish(body).send()

    async def subscribe(
        self,
        topic_name: str,
        reader_name: str,
        *,
        from_offsets: dict[int, int] | None = None,
        poll_interval: float = 0.2,
    ) -> AsyncIterator[BaseModel | dict[str, Any]]:
        records = self._topic(topic_name).records(
            reader_name,
            from_offsets=from_offsets,
        )
        while True:
            record = await records.next()
            if record is None:
                await asyncio.sleep(poll_interval)
                continue
            yield record.value
