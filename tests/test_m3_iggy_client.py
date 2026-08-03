"""Unit tests for mixed resolution-topic contract handling."""

from __future__ import annotations

import unittest
from typing import Any

from pydantic import ValidationError

from contracts import Alert, Resolution, ReviewerDecision
from streams.iggy_client import IggyClient
from streams.stream_names import TOPIC_ALERTS, TOPIC_RESOLUTIONS


class _Send:
    async def send(self) -> None:
        return None


class _Topic:
    def __init__(self) -> None:
        self.body: Any = None

    def publish(self, body: Any) -> _Send:
        self.body = body
        return _Send()


class _Stream:
    def __init__(self) -> None:
        self.topics: dict[str, _Topic] = {}
        self.contracts: dict[str, Any] = {}

    def topic(self, name: str, *, cls: Any = None) -> _Topic:
        self.contracts[name] = cls
        return self.topics.setdefault(name, _Topic())


class _Laser:
    def __init__(self) -> None:
        self.value = _Stream()

    def stream(self, name: str) -> _Stream:
        return self.value


class M3IggyClientTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.client = IggyClient(connection_string="unit-test")
        self.laser = _Laser()
        self.client._laser = self.laser

    async def test_resolution_decision_is_validated_and_published_as_dict(
        self,
    ) -> None:
        decision = ReviewerDecision(
            proposal_id="prop_m3",
            decision="reject",
            timestamp="2026-08-03T17:00:00Z",
            guild_task_id=None,
        )

        await self.client.publish(TOPIC_RESOLUTIONS, decision)

        self.assertIs(self.laser.value.contracts[TOPIC_RESOLUTIONS], dict)
        body = self.laser.value.topics[TOPIC_RESOLUTIONS].body
        self.assertIsInstance(body, dict)
        self.assertEqual(body["proposal_id"], "prop_m3")
        self.assertEqual(body["decision"], "reject")

    async def test_resolution_rejects_unknown_dict_shape(self) -> None:
        with self.assertRaises(ValidationError):
            await self.client.publish(
                TOPIC_RESOLUTIONS,
                {"kind": "not-a-reviewer-decision-or-resolution"},
            )

    async def test_resolution_record_is_also_published_as_dict(self) -> None:
        decision = ReviewerDecision(
            proposal_id="prop_m3_resolution",
            decision="reject",
            timestamp="2026-08-03T17:00:00Z",
        )
        resolution = Resolution(
            incident_id="inc_m3",
            alert_id="alrt_m3",
            outcome="rejected",
            reviewer_decision=decision,
            total_latency_ms=100,
            cost_usd=0,
        )

        await self.client.publish(TOPIC_RESOLUTIONS, resolution)

        body = self.laser.value.topics[TOPIC_RESOLUTIONS].body
        self.assertIsInstance(body, dict)
        self.assertEqual(body["incident_id"], "inc_m3")
        self.assertEqual(body["reviewer_decision"]["decision"], "reject")

    async def test_alert_topic_preserves_typed_model(self) -> None:
        alert = Alert(
            alert_id="alrt_m3",
            fired_at="2026-08-03T17:00:00Z",
            severity="warning",
            service="checkout",
            metric="latency",
            value=900,
            threshold=500,
        )

        await self.client.publish(TOPIC_ALERTS, alert)

        self.assertIs(self.laser.value.contracts[TOPIC_ALERTS], Alert)
        self.assertIs(self.laser.value.topics[TOPIC_ALERTS].body, alert)


if __name__ == "__main__":
    unittest.main()
