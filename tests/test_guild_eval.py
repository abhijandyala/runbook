"""Focused tests for legacy Guild AI experiment tracking."""

from __future__ import annotations

import ast
import hashlib
import json
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any, Self
from unittest.mock import AsyncMock, patch

from contracts import (
    ActionProposal,
    Alert,
    Evidence,
    Hypothesis,
    HypothesisSet,
)
from eval.run import (
    SCALAR_KEYS,
    _scenario_result,
    build_sanitized_results,
    grade_scenario,
    run_evaluation,
    scalar_lines,
)
from pipeline import run_alert

PROJECT_ROOT = Path(__file__).parents[1]


def _hypotheses(
    hypothesis_type: str = "recent_deploy",
    *,
    entity: str = "deploy_checkout_a7f3d2",
    confidence: float = 0.8,
    graph_query_ms: int = 4,
    evidence_source: str = "graph",
) -> HypothesisSet:
    return HypothesisSet(
        alert_id="alrt_eval",
        hypotheses=[
            Hypothesis(
                hypothesis_id="hyp_eval",
                type=hypothesis_type,
                root_cause_description="DO_NOT_PERSIST_ROOT_CAUSE",
                affected_entity=entity,
                confidence=confidence,
                evidence=[
                    Evidence(
                        source=evidence_source,
                        ref="sensitive-ref",
                        detail="DO_NOT_PERSIST_EVIDENCE_BODY",
                    )
                ],
                reasoning="DO_NOT_PERSIST_REASONING_BODY",
            )
        ],
        linkup_hits=0,
        graph_query_ms=graph_query_ms,
        generated_at="2026-08-03T18:00:00Z",
    )


def _proposal(
    action_type: str = "rollback",
    *,
    target: str = "deploy_checkout_a7f3d2",
    confidence: float = 0.8,
) -> ActionProposal:
    safety_class = "standard" if action_type in {"rollback", "restart"} else "safe"
    return ActionProposal(
        proposal_id="prop_eval",
        alert_id="alrt_eval",
        target_hypothesis_id="hyp_eval",
        action_type=action_type,
        action_target=target,
        safety_class=safety_class,
        remediator_confidence=confidence,
        runbook_source="rb_eval",
        reasoning="DO_NOT_PERSIST_ACTION_REASONING",
    )


class GuildEvalGradingTests(unittest.TestCase):
    def test_grounded_pack_expectations(self) -> None:
        pack1 = grade_scenario(
            "pack1",
            "grounded",
            _hypotheses(),
            _proposal(),
        )
        pack2 = grade_scenario(
            "pack2",
            "grounded",
            _hypotheses(
                "dependency_failure",
                entity="postgres-primary",
            ),
            _proposal("restart", target="payments-api"),
        )
        pack3 = grade_scenario(
            "pack3",
            "grounded",
            _hypotheses(
                "unknown",
                entity="checkout-service",
                confidence=0.25,
            ),
            _proposal(
                "diagnostic",
                target="checkout-service",
                confidence=0.25,
            ),
        )

        self.assertTrue(pack1.overall_pass)
        self.assertTrue(pack2.overall_pass)
        self.assertTrue(pack3.overall_pass)

        fabricated = grade_scenario(
            "pack3",
            "grounded",
            _hypotheses(
                "external_event",
                entity="cloud-provider",
                confidence=0.25,
            ),
            _proposal(
                "diagnostic",
                target="checkout-service",
                confidence=0.25,
            ),
        )
        self.assertFalse(fabricated.hypothesis_pass)

    def test_graph_off_grades_structure_not_guessed_type(self) -> None:
        proposal = _proposal(
            "diagnostic",
            target="checkout-service",
            confidence=0.4,
        )
        grades = [
            grade_scenario(
                "pack1",
                "graph-off",
                _hypotheses(
                    guessed_type,
                    entity="checkout-service",
                    confidence=0.5,
                    graph_query_ms=0,
                    evidence_source="alert",
                ),
                proposal,
            )
            for guessed_type in ("recent_deploy", "external_event", "unknown")
        ]

        self.assertTrue(all(grade.overall_pass for grade in grades))

        graph_evidence = grade_scenario(
            "pack1",
            "graph-off",
            _hypotheses(
                confidence=0.4,
                graph_query_ms=0,
                evidence_source="graph",
            ),
            proposal,
        )
        self.assertFalse(graph_evidence.hypothesis_pass)

    def test_scalar_output_is_exact_and_numeric(self) -> None:
        metrics = {
            key: index / 10 for index, key in enumerate(SCALAR_KEYS, start=1)
        }

        lines = scalar_lines(metrics)

        self.assertEqual(
            lines,
            [
                f"{key}: {metrics[key]:.6f}"
                for key in SCALAR_KEYS
            ],
        )
        for line in lines:
            self.assertRegex(line, re.compile(r"^[a-z_]+: -?\d+\.\d{6}$"))

    def test_results_are_allowlisted_and_sanitized(self) -> None:
        scenario = _scenario_result(
            pack_name="pack1",
            mode="grounded",
            hypotheses=_hypotheses(),
            proposal=_proposal(),
            timings_ms={"diagnosis": 1.0, "remediation": 2.0, "total": 3.0},
        )
        raw_token = "raw-task-token-must-not-appear"
        fingerprint = hashlib.sha256(raw_token.encode()).hexdigest()

        results = build_sanitized_results(
            mode="grounded",
            scenario_pack="pack1",
            scenarios=[scenario],
            token_fingerprint=fingerprint,
            rocketride_call_success=1.0,
        )
        serialized = json.dumps(results)

        self.assertEqual(results["token_fingerprint"], fingerprint)
        self.assertIn("observed", results["scenarios"][0])
        self.assertIn("expected", results["scenarios"][0])
        self.assertIn("timings_ms", results["scenarios"][0])
        self.assertIn("evidence_source_counts", results["scenarios"][0])
        for forbidden in (
            raw_token,
            "DO_NOT_PERSIST_ROOT_CAUSE",
            "DO_NOT_PERSIST_EVIDENCE_BODY",
            "DO_NOT_PERSIST_REASONING_BODY",
            "DO_NOT_PERSIST_ACTION_REASONING",
            "sensitive-ref",
        ):
            self.assertNotIn(forbidden, serialized)


class GuildExperimentConfigurationTests(unittest.TestCase):
    def test_named_operations_track_expected_modes_and_artifact(self) -> None:
        guild_config = (PROJECT_ROOT / "guild.yml").read_text(encoding="utf-8")

        self.assertIn("grounded-all:", guild_config)
        self.assertIn("graph-off-all:", guild_config)
        self.assertIn("default: grounded", guild_config)
        self.assertIn("default: graph-off", guild_config)
        self.assertEqual(guild_config.count("output_path:"), 2)
        self.assertEqual(guild_config.count("overall_pass: '^"), 2)
        for excluded in (".env", ".venv", ".guild-venv", ".guild-home"):
            self.assertIn(excluded, guild_config)

    def test_wrapper_uses_non_printing_dotenv_and_local_guild_home(self) -> None:
        wrapper_path = PROJECT_ROOT / "scripts" / "run_guild_experiments.sh"
        wrapper = wrapper_path.read_text(encoding="utf-8")

        self.assertIn('export GUILD_HOME="${PROJECT_ROOT}/.guild-home"', wrapper)
        self.assertIn('"${DOTENV}" -f "${ENV_FILE}" run --', wrapper)
        self.assertIn('"project_python=${PROJECT_PYTHON}"', wrapper)
        self.assertNotIn("source ", wrapper)
        subprocess.run(["sh", "-n", str(wrapper_path)], check=True)

    def test_generated_guild_state_is_ignored(self) -> None:
        ignored = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")

        for path in (
            ".guild-venv/",
            ".guild-home/",
            "/results.json",
            "/guild-comparison.csv",
        ):
            self.assertIn(path, ignored)


class _FakeInferenceContext:
    enters = 0
    exits = 0

    def __init__(self) -> None:
        self.token = "eval-test-token"

    async def __aenter__(self) -> Self:
        type(self).enters += 1
        return self

    async def __aexit__(self, *args: object) -> None:
        del args
        type(self).exits += 1

    async def invoke(self, envelope: object) -> dict[str, Any]:
        del envelope
        return {}


class GuildEvalExecutionTests(unittest.IsolatedAsyncioTestCase):
    def test_evaluator_has_no_stream_graph_write_or_linkup_imports(self) -> None:
        source = (Path(__file__).parents[1] / "eval" / "run.py").read_text(
            encoding="utf-8"
        )
        tree = ast.parse(source)
        imported_modules = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imported_modules.update(
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        )

        for forbidden in (
            "falkor.writes",
            "orchestration.guild_client",
            "streams",
            "streams.iggy_client",
            "linkup",
        ):
            self.assertNotIn(forbidden, imported_modules)

    async def test_graph_off_forwards_flag_and_never_writes_graph(self) -> None:
        _FakeInferenceContext.enters = 0
        _FakeInferenceContext.exits = 0
        hypotheses = _hypotheses(
            "unknown",
            entity="checkout-service",
            confidence=0.2,
            graph_query_ms=0,
            evidence_source="alert",
        )
        proposal = _proposal(
            "diagnostic",
            target="checkout-service",
            confidence=0.2,
        )
        diagnose_mock = AsyncMock(return_value=hypotheses)
        remediate_mock = AsyncMock(return_value=proposal)

        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "results.json"
            with (
                patch("eval.run.RunbookInference", _FakeInferenceContext),
                patch("eval.run.diagnose", diagnose_mock),
                patch("eval.run.remediate", remediate_mock),
                patch("falkor.writes.write_resolution") as graph_write,
            ):
                results = await run_evaluation(
                    mode="graph-off",
                    scenario_pack="pack1",
                    output_path=output_path,
                )

            self.assertTrue(output_path.is_file())

        self.assertEqual(_FakeInferenceContext.enters, 1)
        self.assertEqual(_FakeInferenceContext.exits, 1)
        diagnose_mock.assert_awaited_once()
        remediate_mock.assert_awaited_once()
        self.assertFalse(diagnose_mock.await_args.kwargs["graph_enabled"])
        self.assertFalse(remediate_mock.await_args.kwargs["graph_enabled"])
        self.assertIs(
            diagnose_mock.await_args.args[1],
            remediate_mock.await_args.args[2],
        )
        graph_write.assert_not_called()
        self.assertEqual(results["scenarios"][0]["observed"]["graph_query_ms"], 0)

    async def test_pipeline_forwards_graph_off_to_remediator(self) -> None:
        alert = Alert(
            alert_id="alrt_eval",
            fired_at="2026-08-03T18:00:00Z",
            severity="warning",
            service="checkout-service",
            metric="latency",
            value=900,
            threshold=500,
        )
        streams = _FakeStreams()
        hypotheses = _hypotheses(
            "unknown",
            entity="checkout-service",
            confidence=0.2,
            graph_query_ms=0,
            evidence_source="alert",
        )
        proposal = _proposal(
            "diagnostic",
            target="checkout-service",
            confidence=0.2,
        )
        remediate_mock = AsyncMock(return_value=proposal)

        with (
            patch("pipeline.IggyClient", return_value=streams),
            patch("pipeline.RunbookInference", _FakeInferenceContext),
            patch("pipeline.diagnose", AsyncMock(return_value=hypotheses)),
            patch("pipeline.remediate", remediate_mock),
            patch("pipeline.write_resolution", return_value="inc_eval"),
        ):
            await run_alert(alert, graph_enabled=False)

        self.assertFalse(remediate_mock.await_args.kwargs["graph_enabled"])


class _FakeStreams:
    def __init__(self) -> None:
        self.messages: dict[str, object] = {}

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: object) -> None:
        del args

    async def ensure_topology(self) -> None:
        return None

    async def publish(self, topic: str, payload: object) -> None:
        self.messages[topic] = payload

    async def subscribe(self, topic: str, reader_name: str):
        del reader_name
        yield self.messages[topic]


if __name__ == "__main__":
    unittest.main()
