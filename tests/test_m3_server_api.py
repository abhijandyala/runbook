"""FastAPI contract tests without claiming cloud integration."""

from __future__ import annotations

import asyncio
import json
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from contracts import (
    ActionProposal,
    Alert,
    Hypothesis,
    HypothesisSet,
    ReviewerDecision,
)
from ui.server import (
    AlertRequest,
    DecisionRequest,
    EventBroker,
    M3Runtime,
    OwnedAlert,
    app,
)


class _Runtime:
    started = True

    async def create_alert(self, request: AlertRequest) -> str:
        self.alert_request = request
        return "alrt_ui_contract"

    async def submit_decision(
        self,
        request: DecisionRequest,
    ) -> ReviewerDecision:
        self.decision_request = request
        return ReviewerDecision(
            proposal_id=request.proposal_id,
            decision=request.decision,
            reviewer_note=request.reviewer_note,
            timestamp="2026-08-03T17:00:00Z",
            guild_task_id=None,
        )


class M3ServerApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runtime = _Runtime()
        app.state.runtime = self.runtime
        self.client = TestClient(app)

    def test_health(self) -> None:
        response = self.client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_dashboard(self) -> None:
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn("text/html", response.headers["content-type"])
        self.assertIn('id="alertForm"', response.text)
        self.assertIn("Judge alert input", response.text)
        self.assertIn('id="graphEnabled"', response.text)
        self.assertIn('id="graphToggleState"', response.text)
        self.assertIn("FalkorDB", response.text)

    def test_alert_contract(self) -> None:
        response = self.client.post(
            "/alerts",
            json={
                "service": "checkout",
                "metric": "latency_p99_ms",
                "value": 900,
                "threshold": 500,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"alert_id": "alrt_ui_contract"})
        self.assertEqual(self.runtime.alert_request.severity, "warning")
        self.assertTrue(self.runtime.alert_request.graph_enabled)

    def test_decision_contract(self) -> None:
        response = self.client.post(
            "/decisions",
            json={
                "proposal_id": "prop_m3",
                "decision": "reject",
                "reviewer_note": "Not safe during peak traffic.",
            },
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["proposal_id"], "prop_m3")
        self.assertEqual(body["decision"], "reject")
        self.assertIsNone(body["guild_task_id"])


class M3SseTests(unittest.IsolatedAsyncioTestCase):
    async def test_sse_encodes_named_json_event(self) -> None:
        broker = EventBroker()
        stream = broker.stream()
        next_event = asyncio.create_task(anext(stream))
        await asyncio.sleep(0)
        await broker.broadcast(
            "alert",
            Alert(
                alert_id="alrt_sse",
                fired_at="2026-08-03T17:00:00Z",
                severity="warning",
                service="checkout",
                metric="latency",
                value=900,
                threshold=500,
            ),
        )

        encoded = await next_event
        await stream.aclose()

        event_line, data_line, *_ = encoded.split("\n")
        self.assertEqual(event_line, "event: alert")
        self.assertEqual(json.loads(data_line.removeprefix("data: "))["alert_id"], "alrt_sse")


class _WorkerStreams:
    def __init__(self, hypotheses: HypothesisSet) -> None:
        self.hypotheses = hypotheses
        self.published: list[tuple[str, object]] = []

    async def subscribe(self, topic: str, reader_name: str):
        del topic, reader_name
        yield self.hypotheses

    async def publish(self, topic: str, payload: object) -> None:
        self.published.append((topic, payload))


class M3RemediationWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_worker_passes_owned_alert_graph_toggle(self) -> None:
        hypotheses = HypothesisSet(
            alert_id="alrt_graph_disabled",
            hypotheses=[
                Hypothesis(
                    hypothesis_id="hyp_graph_disabled",
                    type="recent_deploy",
                    root_cause_description="A deploy may have caused the alert.",
                    affected_entity="deploy_checkout_a7f3d2",
                    confidence=0.8,
                    evidence=[],
                    reasoning="Focused worker fixture.",
                )
            ],
            graph_query_ms=0,
            generated_at="2026-08-03T17:00:00Z",
        )
        proposal = ActionProposal(
            proposal_id="prop_graph_disabled",
            alert_id=hypotheses.alert_id,
            target_hypothesis_id="hyp_graph_disabled",
            action_type="diagnostic",
            action_target="checkout-service",
            action_params={},
            safety_class="safe",
            remediator_confidence=0.4,
            runbook_source="",
            reasoning="Graph-free diagnostic.",
        )
        runtime = M3Runtime()
        runtime.started = True
        runtime.streams = _WorkerStreams(hypotheses)
        runtime.inference = AsyncMock()
        runtime._owned_alerts[hypotheses.alert_id] = OwnedAlert(
            service="checkout-service",
            graph_enabled=False,
        )

        with patch(
            "ui.server.remediate",
            new=AsyncMock(return_value=proposal),
        ) as remediate_mock:
            await runtime._remediation_worker()

        remediate_mock.assert_awaited_once_with(
            hypotheses,
            "checkout-service",
            runtime.inference,
            graph_enabled=False,
        )


if __name__ == "__main__":
    unittest.main()
