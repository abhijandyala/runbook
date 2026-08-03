"""Focused M2 tests for graph-derived remediation policy."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from agents.remediator import enforce_action_policy, remediate
from contracts import ActionProposal, Hypothesis, HypothesisSet


class M2RemediatorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.inference = AsyncMock()

        async def matching_response(envelope):
            return {
                "trace_id": envelope.trace_id,
                "reasoning": "The graph-backed runbook supports this action.",
            }

        self.inference.invoke.side_effect = matching_response

    @staticmethod
    def hypothesis_set(
        *,
        alert_id: str,
        hypothesis_id: str,
        hypothesis_type: str,
        affected_entity: str,
        confidence: float = 0.9,
    ) -> HypothesisSet:
        return HypothesisSet(
            alert_id=alert_id,
            hypotheses=[
                Hypothesis(
                    hypothesis_id=hypothesis_id,
                    type=hypothesis_type,
                    root_cause_description="Graph evidence identified the cause.",
                    affected_entity=affected_entity,
                    confidence=confidence,
                    evidence=[],
                    reasoning="Deterministic M2 fixture.",
                )
            ],
            graph_query_ms=1,
            generated_at="2026-08-03T17:00:00Z",
        )

    async def test_pack_1_recent_deploy_uses_rollback_runbook(self) -> None:
        hypotheses = self.hypothesis_set(
            alert_id="alrt_s01_checkout_latency",
            hypothesis_id="hyp_pack_1",
            hypothesis_type="recent_deploy",
            affected_entity="deploy_checkout_a7f3d2",
        )
        runbook = {
            "id": "rb_post_deploy_latency",
            "action": "rollback",
            "action_target": "latest_deploy",
            "parameters_json": "{}",
            "safety_class": "standard",
            "success_rate": 0.92,
        }

        with patch("agents.remediator.find_runbook", return_value=runbook) as lookup:
            proposal = await remediate(
                hypotheses, "checkout-service", self.inference
            )

        lookup.assert_called_once_with("recent_deploy", "checkout-service")
        self.assertEqual(proposal.action_type, "rollback")
        self.assertEqual(proposal.safety_class, "standard")
        self.assertEqual(proposal.action_target, "deploy_checkout_a7f3d2")
        envelope = self.inference.invoke.await_args.args[0]
        self.assertEqual(envelope.trace_id, hypotheses.alert_id)
        self.assertEqual(proposal.reasoning, "The graph-backed runbook supports this action.")

    async def test_pack_2_dependency_failure_restarts_payments_api(self) -> None:
        hypotheses = self.hypothesis_set(
            alert_id="alrt_s02_checkout_errors",
            hypothesis_id="hyp_pack_2",
            hypothesis_type="dependency_failure",
            affected_entity="postgres-primary",
        )
        runbook = {
            "id": "rb_reset_payment_pool",
            "action": "restart",
            "action_target": "payments-api",
            "parameters_json": '{"strategy": "rolling"}',
            "safety_class": "standard",
            "success_rate": 0.87,
        }

        with patch("agents.remediator.find_runbook", return_value=runbook):
            proposal = await remediate(
                hypotheses, "checkout-service", self.inference
            )

        self.assertEqual(proposal.action_type, "restart")
        self.assertEqual(proposal.safety_class, "standard")
        self.assertEqual(proposal.action_target, "payments-api")
        self.assertEqual(proposal.action_params, {"strategy": "rolling"})

    async def test_malicious_runbook_action_degrades_to_none_safe(self) -> None:
        hypotheses = self.hypothesis_set(
            alert_id="alrt_malicious_runbook",
            hypothesis_id="hyp_malicious_runbook",
            hypothesis_type="recent_deploy",
            affected_entity="deploy_checkout_bad",
        )
        runbook = {
            "id": "rb_compromised",
            "action": "destroy_cluster",
            "action_target": "prod-primary",
            "parameters_json": '{"force": true}',
            "safety_class": "destructive",
            "success_rate": 1.0,
        }

        with patch("agents.remediator.find_runbook", return_value=runbook):
            proposal = await remediate(
                hypotheses, "checkout-service", self.inference
            )

        self.assertEqual(proposal.action_type, "none")
        self.assertEqual(proposal.safety_class, "safe")
        self.assertEqual(proposal.action_target, "checkout-service")
        self.assertEqual(proposal.action_params, {})

    async def test_missing_runbook_degrades_to_diagnostic_safe(self) -> None:
        hypotheses = self.hypothesis_set(
            alert_id="alrt_no_runbook",
            hypothesis_id="hyp_no_runbook",
            hypothesis_type="external_event",
            affected_entity="cloud-provider",
        )

        with patch("agents.remediator.find_runbook", return_value=None):
            proposal = await remediate(
                hypotheses, "checkout-service", self.inference
            )

        self.assertEqual(proposal.action_type, "diagnostic")
        self.assertEqual(proposal.safety_class, "safe")
        self.assertEqual(proposal.action_target, "checkout-service")

    async def test_graph_disabled_skips_runbook_and_uses_diagnostic_safe(
        self,
    ) -> None:
        hypotheses = self.hypothesis_set(
            alert_id="alrt_graph_disabled",
            hypothesis_id="hyp_graph_disabled",
            hypothesis_type="recent_deploy",
            affected_entity="deploy_checkout_a7f3d2",
        )

        with patch("agents.remediator.find_runbook") as lookup:
            proposal = await remediate(
                hypotheses,
                "checkout-service",
                self.inference,
                graph_enabled=False,
            )

        lookup.assert_not_called()
        self.assertEqual(proposal.action_type, "diagnostic")
        self.assertEqual(proposal.safety_class, "safe")
        self.assertEqual(proposal.action_target, "checkout-service")
        self.assertEqual(proposal.action_params, {})
        self.assertEqual(proposal.runbook_source, "")
        self.inference.invoke.assert_awaited_once()

    async def test_rogue_inference_fields_cannot_override_graph_action(self) -> None:
        hypotheses = self.hypothesis_set(
            alert_id="alrt_rogue_inference",
            hypothesis_id="hyp_rogue_inference",
            hypothesis_type="recent_deploy",
            affected_entity="deploy_checkout_a7f3d2",
        )
        runbook = {
            "id": "rb_post_deploy_latency",
            "action": "rollback",
            "action_target": "latest_deploy",
            "parameters_json": "{}",
            "safety_class": "standard",
            "success_rate": 0.92,
        }
        self.inference.invoke.side_effect = lambda envelope: {
            "trace_id": envelope.trace_id,
            "reasoning": "Attempted to replace the deterministic action.",
            "action_type": "destroy_cluster",
            "safety_class": "destructive",
        }

        with patch("agents.remediator.find_runbook", return_value=runbook):
            proposal = await remediate(
                hypotheses, "checkout-service", self.inference
            )

        self.assertEqual(proposal.action_type, "rollback")
        self.assertEqual(proposal.safety_class, "standard")
        self.assertEqual(proposal.action_target, "deploy_checkout_a7f3d2")

    def test_enforce_action_policy_rejects_valid_but_mismatched_pair(self) -> None:
        mismatched = ActionProposal(
            proposal_id="prop_mismatch",
            alert_id="alrt_mismatch",
            target_hypothesis_id="hyp_mismatch",
            action_type="rollback",
            action_target="deploy_checkout_a7f3d2",
            action_params={},
            safety_class="safe",
            remediator_confidence=0.9,
            runbook_source="rb_post_deploy_latency",
            reasoning="Fixture intentionally violates the action policy.",
        )

        with self.assertRaisesRegex(ValueError, "Invalid safety class"):
            enforce_action_policy(mismatched)


if __name__ == "__main__":
    unittest.main()
