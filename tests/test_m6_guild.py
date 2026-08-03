"""M6 Guild seam tests without any assumed Guild SDK surface."""

from __future__ import annotations

import os
import unittest
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

from fastapi import Request

from contracts import (
    ActionProposal,
    Alert,
    Hypothesis,
    HypothesisSet,
    ReviewerDecision,
)
from orchestration.guild_client import (
    GuildNotConfigured,
    LocalHumanReviewFallback,
    build_guild_coordinator,
)
from streams.stream_names import (
    TOPIC_ALERTS,
    TOPIC_HYPOTHESES,
    TOPIC_PROPOSALS,
    TOPIC_RESOLUTIONS,
)
from ui.server import DecisionRequest, M3Runtime, OwnedAlert, health


def _alert() -> Alert:
    return Alert(
        alert_id="alrt_m6",
        fired_at="2026-08-03T18:00:00Z",
        severity="warning",
        service="checkout",
        metric="latency",
        value=900,
        threshold=500,
        annotations={"m3_session_id": "replaced-by-runtime"},
    )


def _hypotheses(*, handoff_id: str | None = None) -> HypothesisSet:
    return HypothesisSet(
        alert_id="alrt_m6",
        hypotheses=[
            Hypothesis(
                hypothesis_id="hyp_m6",
                type="recent_deploy",
                root_cause_description="A recent deployment is correlated.",
                affected_entity="checkout",
                confidence=0.8,
                reasoning="Focused M6 fixture.",
            )
        ],
        graph_query_ms=1,
        generated_at="2026-08-03T18:00:01Z",
        guild_handoff_id=handoff_id,
    )


def _proposal(*, task_id: str | None = None) -> ActionProposal:
    return ActionProposal(
        proposal_id="prop_m6",
        alert_id="alrt_m6",
        target_hypothesis_id="hyp_m6",
        action_type="restart",
        action_target="checkout",
        safety_class="standard",
        remediator_confidence=0.8,
        runbook_source="rb_m6",
        reasoning="Focused M6 fixture.",
        guild_task_id=task_id,
    )


class _QualifyingGuild:
    mode = "verified-test-adapter"
    qualifying = True

    def __init__(
        self,
        timeline: list[str],
        *,
        handoff_id: str | None = "handoff_m6",
        task_id: str | None = "task_m6",
        fail_decision: bool = False,
    ) -> None:
        self.timeline = timeline
        self.handoff_id = handoff_id
        self.task_id = task_id
        self.fail_decision = fail_decision
        self.handoff_payload: dict[str, Any] | None = None
        self.task_proposal: ActionProposal | None = None
        self.decision: ReviewerDecision | None = None

    async def ensure_registered(self) -> None:
        self.timeline.append("guild:ensure_registered")

    async def record_handoff(
        self,
        from_role: str,
        to_role: str,
        trace_id: str,
        payload: dict[str, Any],
    ) -> str | None:
        self.timeline.append(
            f"guild:handoff:{from_role}:{to_role}:{trace_id}"
        )
        self.handoff_payload = payload
        return self.handoff_id

    async def create_review_task(
        self,
        proposal: ActionProposal,
    ) -> str | None:
        self.timeline.append("guild:create_review_task")
        self.task_proposal = proposal
        return self.task_id

    async def record_decision(self, decision: ReviewerDecision) -> None:
        self.timeline.append("guild:record_decision")
        self.decision = decision
        if self.fail_decision:
            raise RuntimeError("Guild decision write failed")


class _Streams:
    def __init__(
        self,
        timeline: list[str],
        records: dict[str, list[object]] | None = None,
    ) -> None:
        self.timeline = timeline
        self.records = records or {}
        self.published: list[tuple[str, object]] = []

    async def subscribe(
        self,
        topic: str,
        reader_name: str,
    ) -> AsyncIterator[object]:
        del reader_name
        for record in self.records.get(topic, []):
            yield record

    async def publish(self, topic: str, payload: object) -> None:
        self.timeline.append(f"publish:{topic}")
        self.published.append((topic, payload))


def _runtime(
    guild: _QualifyingGuild,
    streams: _Streams,
) -> M3Runtime:
    runtime = M3Runtime(guild=guild)
    runtime.started = True
    runtime.streams = streams  # type: ignore[assignment]
    runtime.inference = AsyncMock()
    runtime._owned_alerts["alrt_m6"] = OwnedAlert(
        service="checkout",
        graph_enabled=True,
    )
    return runtime


class M6GuildPipelineTests(unittest.IsolatedAsyncioTestCase):
    async def test_qualifying_ids_survive_contracts_and_publication_order(
        self,
    ) -> None:
        timeline: list[str] = []
        alert = _alert()
        guild = _QualifyingGuild(timeline)
        streams = _Streams(timeline, {TOPIC_ALERTS: [alert]})
        runtime = _runtime(guild, streams)
        alert = alert.model_copy(
            update={
                "annotations": {"m3_session_id": runtime.session_id},
            }
        )
        streams.records[TOPIC_ALERTS] = [alert]

        with patch(
            "ui.server.diagnose",
            new=AsyncMock(return_value=_hypotheses()),
        ):
            await runtime._diagnosis_worker()

        published_hypotheses = HypothesisSet.model_validate(
            streams.published[-1][1]
        )
        self.assertEqual(published_hypotheses.guild_handoff_id, "handoff_m6")
        self.assertEqual(
            timeline,
            [
                "guild:handoff:diagnostician:remediator:alrt_m6",
                f"publish:{TOPIC_HYPOTHESES}",
            ],
        )

        streams.records = {TOPIC_HYPOTHESES: [published_hypotheses]}
        with patch(
            "ui.server.remediate",
            new=AsyncMock(return_value=_proposal()),
        ):
            await runtime._remediation_worker()

        published_proposal = ActionProposal.model_validate(
            streams.published[-1][1]
        )
        self.assertEqual(published_proposal.guild_task_id, "task_m6")
        self.assertEqual(guild.task_proposal.guild_task_id, None)
        self.assertEqual(
            timeline[-2:],
            [
                "guild:create_review_task",
                f"publish:{TOPIC_PROPOSALS}",
            ],
        )

        runtime._pending_proposals[published_proposal.proposal_id] = (
            published_proposal
        )
        await runtime.submit_decision(
            DecisionRequest(
                proposal_id=published_proposal.proposal_id,
                decision="reject",
            )
        )

        published_decision = ReviewerDecision.model_validate(
            streams.published[-1][1]
        )
        self.assertEqual(published_decision.guild_task_id, "task_m6")
        self.assertEqual(guild.decision.guild_task_id, "task_m6")
        self.assertEqual(
            timeline[-2:],
            [
                "guild:record_decision",
                f"publish:{TOPIC_RESOLUTIONS}",
            ],
        )

    async def test_missing_qualifying_handoff_blocks_publication(self) -> None:
        timeline: list[str] = []
        alert = _alert()
        guild = _QualifyingGuild(timeline, handoff_id=None)
        streams = _Streams(timeline, {TOPIC_ALERTS: [alert]})
        runtime = _runtime(guild, streams)
        streams.records[TOPIC_ALERTS] = [
            alert.model_copy(
                update={
                    "annotations": {"m3_session_id": runtime.session_id},
                }
            )
        ]

        with patch(
            "ui.server.diagnose",
            new=AsyncMock(return_value=_hypotheses()),
        ):
            await runtime._diagnosis_worker()

        self.assertFalse(streams.published)

    async def test_missing_qualifying_task_blocks_publication(self) -> None:
        timeline: list[str] = []
        guild = _QualifyingGuild(timeline, task_id=None)
        hypotheses = _hypotheses(handoff_id="handoff_m6")
        streams = _Streams(timeline, {TOPIC_HYPOTHESES: [hypotheses]})
        runtime = _runtime(guild, streams)

        with patch(
            "ui.server.remediate",
            new=AsyncMock(return_value=_proposal()),
        ):
            await runtime._remediation_worker()

        self.assertFalse(streams.published)

    async def test_decision_failure_does_not_publish_or_pop(self) -> None:
        timeline: list[str] = []
        guild = _QualifyingGuild(timeline, fail_decision=True)
        streams = _Streams(timeline)
        runtime = _runtime(guild, streams)
        proposal = _proposal(task_id="task_m6")
        runtime._pending_proposals[proposal.proposal_id] = proposal

        with self.assertRaisesRegex(RuntimeError, "decision write failed"):
            await runtime.submit_decision(
                DecisionRequest(
                    proposal_id=proposal.proposal_id,
                    decision="reject",
                )
            )

        self.assertFalse(streams.published)
        self.assertIn(proposal.proposal_id, runtime._pending_proposals)


class M6GuildConfigurationTests(unittest.IsolatedAsyncioTestCase):
    async def test_required_startup_fails_before_cloud_connections(
        self,
    ) -> None:
        with (
            patch.dict(
                os.environ,
                {
                    "GUILD_REQUIRED": "true",
                    "GUILD_ALLOW_LOCAL_FALLBACK": "false",
                },
            ),
            patch("ui.server.IggyClient") as iggy,
        ):
            runtime = M3Runtime()
            with self.assertRaisesRegex(RuntimeError, "no verified real Guild"):
                await runtime.start()

        iggy.assert_not_called()
        self.assertFalse(runtime.started)

    async def test_local_fallback_is_explicitly_non_qualifying(self) -> None:
        with patch.dict(
            os.environ,
            {
                "GUILD_REQUIRED": "false",
                "GUILD_ALLOW_LOCAL_FALLBACK": "true",
            },
        ):
            guild = build_guild_coordinator()

        self.assertIsInstance(guild, LocalHumanReviewFallback)
        self.assertEqual(guild.mode, "local-human-review-fallback")
        self.assertFalse(guild.qualifying)
        self.assertIsNone(
            await guild.record_handoff(
                "diagnostician",
                "remediator",
                "trace",
                {},
            )
        )
        self.assertIsNone(await guild.create_review_task(_proposal()))

    async def test_not_configured_fails_every_operation(self) -> None:
        guild = GuildNotConfigured()
        decision = ReviewerDecision(
            proposal_id="prop_m6",
            decision="reject",
            timestamp="2026-08-03T18:00:02Z",
        )

        with self.assertRaises(RuntimeError):
            await guild.ensure_registered()
        with self.assertRaises(RuntimeError):
            await guild.record_handoff(
                "diagnostician",
                "remediator",
                "trace",
                {},
            )
        with self.assertRaises(RuntimeError):
            await guild.create_review_task(_proposal())
        with self.assertRaises(RuntimeError):
            await guild.record_decision(decision)

    async def test_health_reports_non_qualifying_fallback_mode(self) -> None:
        runtime = M3Runtime(guild=LocalHumanReviewFallback())
        runtime.started = True
        request = Request(
            {
                "type": "http",
                "app": SimpleNamespace(
                    state=SimpleNamespace(runtime=runtime),
                ),
            }
        )

        result = await health(request)

        self.assertEqual(
            result,
            {
                "status": "ok",
                "guild_mode": "local-human-review-fallback",
                "guild_qualifying": False,
            },
        )


if __name__ == "__main__":
    unittest.main()
