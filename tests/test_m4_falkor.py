"""Unit tests for M4 durable write and fresh-query verification."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from contracts import ActionProposal, Alert, Resolution, ReviewerDecision
from falkor.queries import GET_INCIDENT_BY_ID_QUERY
from falkor.writes import write_resolution


class _Result:
    def __init__(self, rows: list[list[object]]) -> None:
        self.result_set = rows


def _records() -> tuple[Alert, Resolution]:
    alert = Alert(
        alert_id="alrt_memory",
        fired_at="2026-08-03T17:00:00Z",
        severity="warning",
        service="checkout",
        metric="latency",
        value=900,
        threshold=500,
    )
    proposal = ActionProposal(
        proposal_id="prop_memory",
        alert_id=alert.alert_id,
        target_hypothesis_id="hyp_memory",
        action_type="restart",
        action_target="checkout",
        safety_class="safe",
        remediator_confidence=0.9,
        runbook_source="rb_memory",
        reasoning="Memory fixture.",
    )
    decision = ReviewerDecision(
        proposal_id=proposal.proposal_id,
        decision="approve",
        timestamp="2026-08-03T17:00:01Z",
    )
    resolution = Resolution(
        incident_id="inc_memory",
        alert_id=alert.alert_id,
        final_action=proposal,
        outcome="verified",
        reviewer_decision=decision,
        total_latency_ms=250,
        cost_usd=0,
    )
    return alert, resolution


class M4FalkorWriteTests(unittest.TestCase):
    def test_write_is_verified_through_fresh_graph_query(self) -> None:
        alert, resolution = _records()
        write_graph = MagicMock()
        write_graph.query.return_value = _Result([["inc_memory"]])
        fresh_graph = MagicMock()
        fresh_graph.query.return_value = _Result(
            [
                [
                    "inc_memory",
                    "alrt_memory",
                    "verified",
                    True,
                    "restart",
                    "{}",
                    "{}",
                    "recent_deploy",
                    250,
                    0,
                ]
            ]
        )

        with (
            patch("falkor.writes.get_graph", return_value=write_graph),
            patch("falkor.queries.get_graph", return_value=fresh_graph),
        ):
            incident_id = write_resolution(
                alert,
                resolution,
                root_cause="recent_deploy",
            )

        self.assertEqual(incident_id, "inc_memory")
        fresh_graph.query.assert_called_once_with(
            GET_INCIDENT_BY_ID_QUERY,
            params={"incident_id": "inc_memory"},
        )
        self.assertIsNot(write_graph, fresh_graph)

    def test_write_raises_when_fresh_query_cannot_find_incident(self) -> None:
        alert, resolution = _records()
        write_graph = MagicMock()
        write_graph.query.return_value = _Result([["inc_memory"]])
        fresh_graph = MagicMock()
        fresh_graph.query.return_value = _Result([])

        with (
            patch("falkor.writes.get_graph", return_value=write_graph),
            patch("falkor.queries.get_graph", return_value=fresh_graph),
            self.assertRaisesRegex(RuntimeError, "fresh query"),
        ):
            write_resolution(
                alert,
                resolution,
                root_cause="recent_deploy",
            )


if __name__ == "__main__":
    unittest.main()
