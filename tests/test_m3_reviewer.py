"""Focused unit tests for M3 reviewer confirmation policy."""

from __future__ import annotations

import unittest

from agents.reviewer import decide
from contracts import ActionProposal


def proposal(
    *,
    safety_class: str = "destructive",
    action_target: str = "deploy-prod-123",
) -> ActionProposal:
    return ActionProposal(
        proposal_id="prop_m3_review",
        alert_id="alrt_m3_review",
        target_hypothesis_id="hyp_m3_review",
        action_type="rollback",
        action_target=action_target,
        safety_class=safety_class,
        remediator_confidence=0.8,
        runbook_source="rb_m3_review",
        reasoning="A focused reviewer-policy fixture.",
    )


class M3ReviewerTests(unittest.TestCase):
    def test_destructive_approval_requires_exact_target(self) -> None:
        with self.assertRaisesRegex(ValueError, "typed_confirmation"):
            decide(proposal(), decision="approve")
        with self.assertRaisesRegex(ValueError, "typed_confirmation"):
            decide(
                proposal(),
                decision="approve",
                typed_confirmation="DEPLOY-PROD-123",
            )

    def test_destructive_approval_accepts_exact_target(self) -> None:
        decision = decide(
            proposal(),
            decision="approve",
            typed_confirmation="deploy-prod-123",
        )

        self.assertEqual(decision.decision, "approve")
        self.assertIsNone(decision.guild_task_id)

    def test_modify_to_standard_needs_no_confirmation(self) -> None:
        modified = proposal(safety_class="standard")
        decision = decide(
            proposal(),
            decision="modify",
            modified_action=modified,
        )

        self.assertEqual(decision.modified_action, modified)

    def test_destructive_modify_requires_modified_target_confirmation(self) -> None:
        modified = proposal(action_target="payments-prod-456")
        with self.assertRaisesRegex(ValueError, "typed_confirmation"):
            decide(
                proposal(),
                decision="modify",
                modified_action=modified,
                typed_confirmation="deploy-prod-123",
            )
        decision = decide(
            proposal(),
            decision="modify",
            modified_action=modified,
            typed_confirmation="payments-prod-456",
        )

        self.assertEqual(decision.modified_action, modified)

    def test_destructive_reject_needs_no_confirmation(self) -> None:
        decision = decide(proposal(), decision="reject")

        self.assertEqual(decision.decision, "reject")
        self.assertIsNone(decision.guild_task_id)


if __name__ == "__main__":
    unittest.main()
