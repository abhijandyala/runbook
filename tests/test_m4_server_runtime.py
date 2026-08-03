"""M4 consumed-decision action, outcome, and writeback tests."""

from __future__ import annotations

import asyncio
import unittest
from collections import defaultdict
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from pydantic import BaseModel

from agents.reviewer import OUTCOME_TIMEOUT_SECONDS, OutcomeObservation
from contracts import ActionProposal, Alert, Resolution, ReviewerDecision
from streams.stream_names import TOPIC_ALERTS, TOPIC_RESOLUTIONS
from ui.server import DecisionRequest, M3Runtime, OwnedAlert


def _alert() -> Alert:
    return Alert(
        alert_id="alrt_m4",
        fired_at="2026-08-03T17:00:00Z",
        severity="warning",
        service="checkout",
        metric="latency_p99_ms",
        value=900,
        threshold=500,
    )


def _proposal() -> ActionProposal:
    return ActionProposal(
        proposal_id="prop_m4",
        alert_id="alrt_m4",
        target_hypothesis_id="hyp_m4",
        action_type="restart",
        action_target="checkout",
        action_params={},
        safety_class="standard",
        remediator_confidence=0.95,
        runbook_source="rb_m4",
        reasoning="Focused M4 fixture.",
    )


def _decision(kind: str) -> ReviewerDecision:
    return ReviewerDecision(
        proposal_id="prop_m4",
        decision=kind,
        timestamp="2026-08-03T17:00:01Z",
    )


class _Streams:
    def __init__(self) -> None:
        self.published: list[tuple[str, object]] = []
        self.timeline: list[str] = []
        self.queues: defaultdict[str, asyncio.Queue[object]] = defaultdict(
            asyncio.Queue
        )

    async def publish(self, topic: str, payload: object) -> None:
        self.timeline.append(f"publish:{topic}")
        self.published.append((topic, payload))
        await self.queues[topic].put(payload)

    async def subscribe(self, topic: str, reader_name: str):
        del reader_name
        while True:
            yield await self.queues[topic].get()


class _Events:
    def __init__(self) -> None:
        self.items: list[tuple[str, dict[str, object]]] = []

    async def broadcast(
        self,
        event: str,
        payload: BaseModel | dict[str, object],
    ) -> None:
        body = (
            payload.model_dump(mode="json")
            if isinstance(payload, BaseModel)
            else payload
        )
        self.items.append((event, body))


def _runtime() -> tuple[M3Runtime, _Streams, _Events]:
    runtime = M3Runtime()
    streams = _Streams()
    events = _Events()
    alert = _alert()
    proposal = _proposal()
    runtime.started = True
    runtime.streams = streams
    runtime.events = events
    runtime._owned_alerts[alert.alert_id] = OwnedAlert(
        service=alert.service,
        graph_enabled=True,
    )
    runtime._alert_history[alert.alert_id] = alert
    runtime._proposal_history[proposal.proposal_id] = proposal
    runtime._owned_proposal_ids.add(proposal.proposal_id)
    return runtime, streams, events


def _assert_iso_utc(test: unittest.TestCase, value: object) -> None:
    test.assertIsInstance(value, str)
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    test.assertEqual(parsed.utcoffset(), timezone.utc.utcoffset(parsed))


class M4DecisionRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_submit_decision_retains_resolution_history(self) -> None:
        runtime, _, _ = _runtime()
        proposal = _proposal()
        runtime._pending_proposals[proposal.proposal_id] = proposal

        await runtime.submit_decision(
            DecisionRequest(
                proposal_id=proposal.proposal_id,
                decision="reject",
            )
        )

        self.assertNotIn(proposal.proposal_id, runtime._pending_proposals)
        self.assertEqual(
            runtime._proposal_history[proposal.proposal_id],
            proposal,
        )
        self.assertEqual(
            runtime._alert_history[proposal.alert_id].alert_id,
            proposal.alert_id,
        )

    async def test_approved_verified_resolution_is_written(self) -> None:
        runtime, streams, events = _runtime()

        def persist_resolution(*args: object, **kwargs: object) -> str:
            del args, kwargs
            streams.timeline.append("persistence-verified")
            return "inc_verified"

        with (
            patch("ui.server.SIMULATED_RECOVERY_DELAY_SECONDS", 0),
            patch(
                "ui.server.write_resolution",
                side_effect=persist_resolution,
            ) as write_mock,
            patch(
                "ui.server.resolve",
                wraps=lambda proposal, decision, **kwargs: Resolution(
                    incident_id="inc_verified",
                    alert_id=proposal.alert_id,
                    final_action=proposal,
                    outcome=kwargs["outcome"],
                    reviewer_decision=decision,
                    total_latency_ms=kwargs["total_latency_ms"],
                    cost_usd=0,
                ),
            ),
        ):
            await runtime._process_decision(_decision("approve"))

        recovery = next(
            payload
            for topic, payload in streams.published
            if topic == TOPIC_ALERTS
        )
        self.assertIsInstance(recovery, Alert)
        self.assertEqual(recovery.annotations["simulated"], "true")
        self.assertEqual(recovery.annotations["proposal_id"], "prop_m4")

        resolution = next(
            payload
            for topic, payload in streams.published
            if topic == TOPIC_RESOLUTIONS
        )
        self.assertIsInstance(resolution, Resolution)
        self.assertEqual(resolution.outcome, "verified")
        write_mock.assert_called_once()
        self.assertLess(
            streams.timeline.index("persistence-verified"),
            streams.timeline.index(f"publish:{TOPIC_RESOLUTIONS}"),
        )
        action_events = [
            body for event, body in events.items if event == "action"
        ]
        self.assertEqual(
            [body["status"] for body in action_events],
            ["dispatching", "executed"],
        )
        for body in action_events:
            self.assertEqual(
                set(body),
                {
                    "proposal_id",
                    "alert_id",
                    "action_type",
                    "action_target",
                    "status",
                    "simulated",
                    "timestamp",
                    "message",
                },
            )
            self.assertEqual(body["proposal_id"], "prop_m4")
            self.assertEqual(body["alert_id"], "alrt_m4")
            self.assertEqual(body["action_type"], "restart")
            self.assertEqual(body["action_target"], "checkout")
            self.assertIs(body["simulated"], True)
            _assert_iso_utc(self, body["timestamp"])

        outcome_events = [
            body for event, body in events.items if event == "outcome"
        ]
        self.assertEqual(
            [body["status"] for body in outcome_events],
            ["watching", "verified"],
        )
        self.assertEqual(
            set(outcome_events[0]),
            {
                "proposal_id",
                "alert_id",
                "status",
                "simulated",
                "timestamp",
                "timeout_seconds",
            },
        )
        self.assertEqual(
            set(outcome_events[1]),
            {
                "proposal_id",
                "alert_id",
                "status",
                "outcome",
                "observed_value",
                "threshold",
                "simulated",
                "timestamp",
                "timeout_seconds",
            },
        )
        self.assertTrue(
            all(
                body["simulated"] is True
                for event, body in events.items
                if event in {"action", "outcome"}
            )
        )
        for body in outcome_events:
            self.assertEqual(
                body["timeout_seconds"],
                OUTCOME_TIMEOUT_SECONDS,
            )
            self.assertEqual(body["proposal_id"], "prop_m4")
            self.assertEqual(body["alert_id"], "alrt_m4")
            _assert_iso_utc(self, body["timestamp"])

    async def test_all_final_outcomes_have_complete_contract(self) -> None:
        cases = (
            ("verified", "verified", 400.0),
            ("partial", "partial", 700.0),
            ("no_effect", "no_effect", 950.0),
            ("timeout", "no_effect", None),
        )

        for status, outcome, observed_value in cases:
            with self.subTest(status=status):
                runtime, _, events = _runtime()
                observed_alert = (
                    _alert().model_copy(update={"value": observed_value})
                    if observed_value is not None
                    else None
                )
                observation = OutcomeObservation(
                    status=status,
                    outcome=outcome,
                    alert=observed_alert,
                )
                with (
                    patch("ui.server.SIMULATED_RECOVERY_DELAY_SECONDS", 0),
                    patch(
                        "ui.server.watch_for_outcome",
                        new=AsyncMock(return_value=observation),
                    ),
                ):
                    await runtime._watch_simulated_outcome(
                        alert=_alert(),
                        action=_proposal(),
                    )

                outcome_events = [
                    body
                    for event, body in events.items
                    if event == "outcome"
                ]
                self.assertEqual(len(outcome_events), 2)
                final = outcome_events[-1]
                self.assertEqual(final["status"], status)
                self.assertEqual(final["outcome"], outcome)
                self.assertEqual(final["observed_value"], observed_value)
                self.assertEqual(
                    final["timeout_seconds"],
                    OUTCOME_TIMEOUT_SECONDS,
                )
                self.assertIs(final["simulated"], True)
                _assert_iso_utc(self, final["timestamp"])

    async def test_failed_persistence_does_not_publish_resolution(self) -> None:
        runtime, streams, _ = _runtime()
        alert = _alert()
        resolution = Resolution(
            incident_id="inc_failed",
            alert_id=alert.alert_id,
            outcome="rejected",
            reviewer_decision=_decision("reject"),
            total_latency_ms=10,
            cost_usd=0,
        )

        with (
            patch(
                "ui.server.write_resolution",
                side_effect=RuntimeError("fresh query failed"),
            ),
            self.assertRaisesRegex(RuntimeError, "fresh query failed"),
        ):
            await runtime._publish_and_persist(resolution, alert)

        self.assertFalse(
            any(topic == TOPIC_RESOLUTIONS for topic, _ in streams.published)
        )

    async def test_rejected_resolution_is_written_without_action(self) -> None:
        runtime, streams, events = _runtime()

        with (
            patch(
                "ui.server.write_resolution",
                return_value="inc_rejected",
            ) as write_mock,
            patch(
                "ui.server.resolve",
                wraps=lambda proposal, decision, **kwargs: Resolution(
                    incident_id="inc_rejected",
                    alert_id=proposal.alert_id,
                    final_action=None,
                    outcome="rejected",
                    reviewer_decision=decision,
                    total_latency_ms=kwargs["total_latency_ms"],
                    cost_usd=0,
                ),
            ),
        ):
            await runtime._process_decision(_decision("reject"))

        resolution = next(
            payload
            for topic, payload in streams.published
            if topic == TOPIC_RESOLUTIONS
        )
        self.assertEqual(resolution.outcome, "rejected")
        self.assertIsNone(resolution.final_action)
        write_mock.assert_called_once()
        self.assertFalse(
            any(event in {"action", "outcome"} for event, _ in events.items)
        )
        self.assertFalse(
            any(topic == TOPIC_ALERTS for topic, _ in streams.published)
        )

    async def test_duplicate_consumed_decision_is_processed_once(self) -> None:
        runtime, _, events = _runtime()
        decision = _decision("reject")

        class DuplicateStreams:
            async def subscribe(self, topic: str, reader_name: str):
                del topic, reader_name
                yield decision
                yield decision

        runtime.streams = DuplicateStreams()
        runtime._process_decision = AsyncMock()

        await runtime._decision_listener()

        runtime._process_decision.assert_awaited_once_with(decision)
        self.assertEqual(
            sum(event == "decision" for event, _ in events.items),
            1,
        )

    async def test_resolution_listener_broadcasts_only_final_once(self) -> None:
        runtime, _, events = _runtime()
        decision = _decision("reject")
        resolution = Resolution(
            incident_id="inc_listener",
            alert_id="alrt_m4",
            outcome="rejected",
            reviewer_decision=decision,
            total_latency_ms=10,
            cost_usd=0,
        )

        class MixedStreams:
            async def subscribe(self, topic: str, reader_name: str):
                del topic, reader_name
                yield decision
                yield resolution
                yield resolution

        runtime.streams = MixedStreams()

        await runtime._resolution_listener()

        resolution_events = [
            body for event, body in events.items if event == "resolution"
        ]
        self.assertEqual(len(resolution_events), 1)
        self.assertEqual(resolution_events[0]["incident_id"], "inc_listener")


if __name__ == "__main__":
    unittest.main()
