"""Proof that M7 is cut and Pack 3 degrades safely without Linkup."""

from __future__ import annotations

import ast
import io
import json
import tokenize
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from agents.diagnostician import diagnose
from agents.remediator import remediate
from contracts import Alert, TaskEnvelope
from falkor.queries import (
    DEPENDENCY_QUERY,
    PAST_INCIDENT_QUERY,
    RECENT_DEPLOY_QUERY,
    RUNBOOK_QUERY,
    SERVICE_QUERY,
)

ROOT = Path(__file__).resolve().parents[1]


class _Result:
    def __init__(self, rows: list[list[object]]) -> None:
        self.result_set = rows


class _Pack3Graph:
    """Deterministic Falkor query double for the seeded Pack 3 shape."""

    def __init__(self) -> None:
        self.queries: list[str] = []

    def query(
        self,
        query: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> _Result:
        del params
        self.queries.append(query)
        rows_by_query = {
            SERVICE_QUERY: [["checkout-service", "tier-1", "commerce", "critical"]],
            DEPENDENCY_QUERY: [
                ["payments-api", "tier-1", 1],
                ["cart-service", "tier-2", 1],
                ["session-service", "tier-2", 1],
            ],
            RECENT_DEPLOY_QUERY: [],
            PAST_INCIDENT_QUERY: [],
            RUNBOOK_QUERY: [
                [
                    "rb_collect_diagnostics",
                    "diagnostic",
                    "alerting-service",
                    "{}",
                    "safe",
                    1.0,
                ]
            ],
        }
        return _Result(rows_by_query[query])


class _DeterministicInference:
    async def invoke(self, envelope: TaskEnvelope) -> dict[str, Any]:
        if envelope.payload.get("mode") == "alert_only_guess":
            return {
                "guess": {
                    "type": "unknown",
                    "affected_entity": envelope.payload["alert"]["service"],
                    "confidence": 0.2,
                    "reasoning": "The alert alone cannot establish an external cause.",
                }
            }
        if envelope.role == "diagnostician":
            return {
                "reasoning_by_hypothesis": {
                    "deterministic": (
                        "The graph has no matching deploy or incident; escalate "
                        "instead of inventing external evidence."
                    )
                }
            }
        return {"reasoning": "Collect diagnostics and escalate for human review."}


class _AlertOnlyConfidenceInference:
    def __init__(self, confidence: Any) -> None:
        self.confidence = confidence

    async def invoke(self, envelope: TaskEnvelope) -> dict[str, Any]:
        return {
            "guess": {
                "type": "unknown",
                "affected_entity": envelope.payload["alert"]["service"],
                "confidence": self.confidence,
                "reasoning": "Alert-only confidence parsing regression fixture.",
            }
        }


class M7LinkupCutTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        scenario = json.loads(
            (ROOT / "scenarios" / "s03_cloud_outage.json").read_text(
                encoding="utf-8"
            )
        )
        cls.alert = Alert.model_validate(scenario["trigger"])

    async def test_pack_3_graph_path_escalates_without_external_fabrication(
        self,
    ) -> None:
        graph = _Pack3Graph()
        inference = _DeterministicInference()

        with patch("falkor.queries.get_graph", return_value=graph):
            hypotheses = await diagnose(self.alert, inference)
            proposal = await remediate(
                hypotheses,
                self.alert.service,
                inference,
            )

        self.assertTrue(hypotheses.hypotheses)
        self.assertLess(hypotheses.hypotheses[0].confidence, 0.5)
        self.assertEqual(hypotheses.linkup_hits, 0)
        self.assertNotIn(
            "external_event",
            {hypothesis.type for hypothesis in hypotheses.hypotheses},
        )
        self.assertNotIn(
            "linkup",
            {
                evidence.source
                for hypothesis in hypotheses.hypotheses
                for evidence in hypothesis.evidence
            },
        )
        self.assertIn(proposal.action_type, {"diagnostic", "none"})
        self.assertEqual(proposal.safety_class, "safe")
        self.assertLess(proposal.remediator_confidence, 0.5)
        for expected_query in {
            SERVICE_QUERY,
            DEPENDENCY_QUERY,
            RECENT_DEPLOY_QUERY,
            PAST_INCIDENT_QUERY,
            RUNBOOK_QUERY,
        }:
            self.assertIn(expected_query, graph.queries)

    async def test_graph_off_remains_low_confidence(self) -> None:
        hypotheses = await diagnose(
            self.alert,
            _DeterministicInference(),
            graph_enabled=False,
        )

        self.assertTrue(hypotheses.hypotheses)
        self.assertLess(hypotheses.hypotheses[0].confidence, 0.5)
        self.assertEqual(hypotheses.hypotheses[0].type, "unknown")
        self.assertEqual(hypotheses.linkup_hits, 0)

    async def test_graph_off_safely_parses_confidence_variants(self) -> None:
        cases = [
            (0.25, 0.25),
            ("0.3", 0.3),
            (1.2, 0.5),
            ("-1", 0.0),
            ("low", 0.4),
            (None, 0.4),
            (True, 0.4),
            (False, 0.4),
            (float("nan"), 0.4),
            (float("inf"), 0.4),
            (float("-inf"), 0.4),
            ("NaN", 0.4),
            ("Infinity", 0.4),
        ]

        for raw_confidence, expected in cases:
            with self.subTest(raw_confidence=raw_confidence):
                hypotheses = await diagnose(
                    self.alert,
                    _AlertOnlyConfidenceInference(raw_confidence),
                    graph_enabled=False,
                )

                confidence = hypotheses.hypotheses[0].confidence
                self.assertEqual(confidence, expected)
                self.assertGreaterEqual(confidence, 0.0)
                self.assertLessEqual(confidence, 0.5)

    def test_runtime_has_no_linkup_sdk_or_api_key_use(self) -> None:
        runtime_paths = list(ROOT.glob("*.py"))
        for directory in ("agents", "falkor", "orchestration", "scripts", "ui"):
            runtime_paths.extend((ROOT / directory).rglob("*.py"))

        for path in runtime_paths:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
            imported_roots = {
                alias.name.split(".", maxsplit=1)[0]
                for node in ast.walk(tree)
                if isinstance(node, ast.Import)
                for alias in node.names
            }
            imported_roots.update(
                node.module.split(".", maxsplit=1)[0]
                for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom) and node.module
            )
            self.assertNotIn("linkup", imported_roots, path)

            executable_tokens = [
                token.string
                for token in tokenize.generate_tokens(io.StringIO(source).readline)
                if token.type != tokenize.COMMENT
            ]
            executable_source = " ".join(executable_tokens).lower()
            self.assertNotIn("linkup_sdk", executable_source, path)
            self.assertNotIn("linkup-sdk", executable_source, path)
            self.assertNotIn("linkupclient", executable_source, path)
            self.assertNotIn("linkup_api_key", executable_source, path)

        requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
        active_requirements = "\n".join(
            line.split("#", maxsplit=1)[0] for line in requirements.splitlines()
        ).lower()
        self.assertNotIn("linkup-sdk", active_requirements)


if __name__ == "__main__":
    unittest.main()
