"""Focused tests for reusable M4 outcome policy."""

from __future__ import annotations

import inspect
import unittest

from agents.reviewer import (
    OUTCOME_TIMEOUT_SECONDS,
    classify_outcome,
    watch_for_outcome,
)


class M4OutcomePolicyTests(unittest.TestCase):
    def test_verified_at_or_below_threshold(self) -> None:
        self.assertEqual(
            classify_outcome(
                baseline_value=900,
                observed_value=500,
                threshold=500,
            ),
            "verified",
        )

    def test_partial_when_improved_above_threshold(self) -> None:
        self.assertEqual(
            classify_outcome(
                baseline_value=900,
                observed_value=700,
                threshold=500,
            ),
            "partial",
        )

    def test_no_effect_when_not_improved(self) -> None:
        self.assertEqual(
            classify_outcome(
                baseline_value=900,
                observed_value=950,
                threshold=500,
            ),
            "no_effect",
        )

    def test_watcher_has_hard_sixty_second_default(self) -> None:
        timeout_default = inspect.signature(watch_for_outcome).parameters[
            "timeout_seconds"
        ].default

        self.assertEqual(OUTCOME_TIMEOUT_SECONDS, 60.0)
        self.assertEqual(timeout_default, 60.0)


if __name__ == "__main__":
    unittest.main()
