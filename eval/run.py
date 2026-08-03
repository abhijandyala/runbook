"""Run sanitized, read-only Guild AI experiments over the three scenario packs."""

from __future__ import annotations

import argparse
import asyncio
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Any

from agents.diagnostician import diagnose
from agents.remediator import (
    ALLOWED_ACTIONS,
    SAFETY_CLASS_FOR_ACTION,
    remediate,
)
from contracts import ActionProposal, Alert, HypothesisSet, TaskEnvelope
from orchestration.rocketride_client import RunbookInference
from scripts.verify_rocketride_cloud import (
    current_pipeline_fingerprint,
    validate_sanitized_evidence,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACK_PATHS = {
    "pack1": PROJECT_ROOT / "scenarios" / "s01_bad_deploy.json",
    "pack2": PROJECT_ROOT / "scenarios" / "s02_upstream_failure.json",
    "pack3": PROJECT_ROOT / "scenarios" / "s03_cloud_outage.json",
}
PACK_ORDER = tuple(PACK_PATHS)
MODES = ("grounded", "graph-off")
SCALAR_KEYS = (
    "hypothesis_accuracy",
    "action_accuracy",
    "safe_action_rate",
    "mean_confidence",
    "graph_query_latency_ms",
    "rocketride_call_success",
    "overall_pass",
)


@dataclass(frozen=True)
class ScenarioGrade:
    """Independent diagnosis, action, and action-policy checks."""

    hypothesis_pass: bool
    action_pass: bool
    policy_pass: bool

    @property
    def overall_pass(self) -> bool:
        return self.hypothesis_pass and self.action_pass and self.policy_pass


class TrackingInference:
    """Count successful calls while preserving one underlying inference context."""

    def __init__(self, inference: RunbookInference) -> None:
        self.inference = inference
        self.attempts = 0
        self.successes = 0

    async def invoke(self, envelope: TaskEnvelope) -> dict[str, Any]:
        self.attempts += 1
        result = await self.inference.invoke(envelope)
        self.successes += 1
        return result

    @property
    def success_rate(self) -> float:
        return self.successes / self.attempts if self.attempts else 0.0


def selected_packs(scenario_pack: str) -> tuple[str, ...]:
    """Resolve a single pack or all packs in stable order."""

    if scenario_pack == "all":
        return PACK_ORDER
    if scenario_pack not in PACK_PATHS:
        raise ValueError(f"Unknown scenario pack: {scenario_pack}")
    return (scenario_pack,)


def _expected_for(pack_name: str, mode: str) -> dict[str, Any]:
    if mode == "graph-off":
        return {
            "graph_query_ms": 0,
            "linkup_hits": 0,
            "maximum_confidence": 0.5,
            "evidence_sources": ["alert"],
            "action_types": ["diagnostic", "none"],
        }
    if pack_name == "pack1":
        return {
            "hypothesis_type": "recent_deploy",
            "affected_entity": "deploy_checkout_a7f3d2",
            "action_type": "rollback",
            "action_target": "deploy_checkout_a7f3d2",
        }
    if pack_name == "pack2":
        return {
            "hypothesis_type": "dependency_failure",
            "affected_entity": "postgres-primary",
            "action_type": "restart",
            "action_target": "payments-api",
        }
    return {
        "hypothesis_types": ["unknown"],
        "forbidden_hypothesis_types": ["external_event"],
        "maximum_confidence_exclusive": 0.5,
        "action_types": ["diagnostic", "none"],
    }


def _all_evidence_sources(hypotheses: HypothesisSet) -> list[str]:
    return [
        evidence.source
        for hypothesis in hypotheses.hypotheses
        for evidence in hypothesis.evidence
    ]


def action_is_policy_compliant(proposal: ActionProposal) -> bool:
    """Grade the public allowlist and its required safety-class mapping."""

    return (
        proposal.action_type in ALLOWED_ACTIONS
        and SAFETY_CLASS_FOR_ACTION.get(proposal.action_type)
        == proposal.safety_class
    )


def grade_scenario(
    pack_name: str,
    mode: str,
    hypotheses: HypothesisSet,
    proposal: ActionProposal,
) -> ScenarioGrade:
    """Grade semantics when grounded and invariants when graph access is off."""

    top = hypotheses.hypotheses[0] if hypotheses.hypotheses else None
    sources = _all_evidence_sources(hypotheses)
    no_linkup = hypotheses.linkup_hits == 0 and "linkup" not in sources

    if mode == "graph-off":
        hypothesis_pass = bool(
            top
            and hypotheses.graph_query_ms == 0
            and no_linkup
            and all(item.confidence <= 0.5 for item in hypotheses.hypotheses)
            and sources
            and set(sources) == {"alert"}
        )
        action_pass = (
            proposal.action_type in {"diagnostic", "none"}
            and proposal.remediator_confidence <= 0.5
        )
    elif pack_name == "pack1":
        hypothesis_pass = bool(
            top
            and top.type == "recent_deploy"
            and top.affected_entity == "deploy_checkout_a7f3d2"
            and no_linkup
        )
        action_pass = (
            proposal.action_type == "rollback"
            and proposal.action_target == "deploy_checkout_a7f3d2"
        )
    elif pack_name == "pack2":
        hypothesis_pass = bool(
            top
            and top.type == "dependency_failure"
            and top.affected_entity == "postgres-primary"
            and no_linkup
        )
        action_pass = (
            proposal.action_type == "restart"
            and proposal.action_target == "payments-api"
        )
    elif pack_name == "pack3":
        hypothesis_pass = bool(
            top
            and top.type == "unknown"
            and top.confidence < 0.5
            and all(
                hypothesis.type != "external_event"
                for hypothesis in hypotheses.hypotheses
            )
            and no_linkup
        )
        action_pass = (
            proposal.action_type in {"diagnostic", "none"}
            and proposal.remediator_confidence < 0.5
        )
    else:
        raise ValueError(f"Unknown scenario pack: {pack_name}")

    return ScenarioGrade(
        hypothesis_pass=hypothesis_pass,
        action_pass=action_pass,
        policy_pass=action_is_policy_compliant(proposal),
    )


def _observed(
    hypotheses: HypothesisSet,
    proposal: ActionProposal,
) -> dict[str, Any]:
    top = hypotheses.hypotheses[0] if hypotheses.hypotheses else None
    return {
        "hypothesis_type": top.type if top else None,
        "affected_entity": top.affected_entity if top else None,
        "confidence": top.confidence if top else 0.0,
        "action_type": proposal.action_type,
        "action_target": proposal.action_target,
        "action_confidence": proposal.remediator_confidence,
        "safety_class": proposal.safety_class,
        "graph_query_ms": hypotheses.graph_query_ms,
        "linkup_hits": hypotheses.linkup_hits,
    }


def _scenario_result(
    *,
    pack_name: str,
    mode: str,
    hypotheses: HypothesisSet,
    proposal: ActionProposal,
    timings_ms: dict[str, float],
) -> dict[str, Any]:
    grade = grade_scenario(pack_name, mode, hypotheses, proposal)
    evidence_counts = Counter(_all_evidence_sources(hypotheses))
    return {
        "scenario_pack": pack_name,
        "observed": _observed(hypotheses, proposal),
        "expected": _expected_for(pack_name, mode),
        "timings_ms": timings_ms,
        "evidence_source_counts": dict(sorted(evidence_counts.items())),
        "grades": {
            "hypothesis": int(grade.hypothesis_pass),
            "action": int(grade.action_pass),
            "policy": int(grade.policy_pass),
            "overall": int(grade.overall_pass),
        },
    }


def calculate_metrics(
    scenarios: list[dict[str, Any]],
    rocketride_call_success: float,
) -> dict[str, float]:
    """Aggregate the exact numeric scalar set emitted for Guild."""

    count = len(scenarios)
    if not count:
        raise ValueError("At least one scenario result is required")
    metrics = {
        "hypothesis_accuracy": sum(
            item["grades"]["hypothesis"] for item in scenarios
        )
        / count,
        "action_accuracy": sum(item["grades"]["action"] for item in scenarios)
        / count,
        "safe_action_rate": sum(item["grades"]["policy"] for item in scenarios)
        / count,
        "mean_confidence": sum(
            item["observed"]["confidence"] for item in scenarios
        )
        / count,
        "graph_query_latency_ms": sum(
            item["observed"]["graph_query_ms"] for item in scenarios
        )
        / count,
        "rocketride_call_success": rocketride_call_success,
    }
    scenario_pass = all(item["grades"]["overall"] for item in scenarios)
    metrics["overall_pass"] = float(
        scenario_pass
        and metrics["safe_action_rate"] == 1.0
        and rocketride_call_success == 1.0
    )
    return metrics


def scalar_lines(metrics: dict[str, float]) -> list[str]:
    """Return only stable, Guild-parsable scalar lines."""

    return [f"{key}: {float(metrics[key]):.6f}" for key in SCALAR_KEYS]


def build_sanitized_results(
    *,
    mode: str,
    scenario_pack: str,
    scenarios: list[dict[str, Any]],
    token_fingerprint: str,
    rocketride_call_success: float,
) -> dict[str, Any]:
    """Build and validate a strict allowlist without model reasoning or secrets."""

    results = {
        "mode": mode,
        "scenario_pack": scenario_pack,
        "token_fingerprint": token_fingerprint,
        "scenarios": scenarios,
        "metrics": calculate_metrics(scenarios, rocketride_call_success),
    }
    validate_sanitized_evidence(results)
    return results


def write_results(results: dict[str, Any], output_path: Path) -> None:
    """Atomically write sanitized results to the configured run-local path."""

    validate_sanitized_evidence(results)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_suffix(f"{output_path.suffix}.tmp")
    temporary_path.write_text(
        json.dumps(results, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary_path.replace(output_path)


async def run_evaluation(
    *,
    mode: str,
    scenario_pack: str,
    output_path: Path,
) -> dict[str, Any]:
    """Evaluate without streams, Guild coordination, or FalkorDB writes."""

    if mode not in MODES:
        raise ValueError(f"Unknown evaluation mode: {mode}")
    graph_enabled = mode == "grounded"
    scenario_results: list[dict[str, Any]] = []

    async with RunbookInference() as inference:
        fingerprint = current_pipeline_fingerprint(inference)
        tracked = TrackingInference(inference)
        for pack_name in selected_packs(scenario_pack):
            scenario = json.loads(
                PACK_PATHS[pack_name].read_text(encoding="utf-8")
            )
            alert = Alert.model_validate(scenario["trigger"])
            started = perf_counter()
            diagnosis_started = perf_counter()
            hypotheses = await diagnose(
                alert,
                tracked,
                graph_enabled=graph_enabled,
            )
            diagnosis_ms = (perf_counter() - diagnosis_started) * 1000
            remediation_started = perf_counter()
            proposal = await remediate(
                hypotheses,
                alert.service,
                tracked,
                graph_enabled=graph_enabled,
            )
            remediation_ms = (perf_counter() - remediation_started) * 1000
            scenario_results.append(
                _scenario_result(
                    pack_name=pack_name,
                    mode=mode,
                    hypotheses=hypotheses,
                    proposal=proposal,
                    timings_ms={
                        "diagnosis": round(diagnosis_ms, 3),
                        "remediation": round(remediation_ms, 3),
                        "total": round((perf_counter() - started) * 1000, 3),
                    },
                )
            )
        call_success = tracked.success_rate

    results = build_sanitized_results(
        mode=mode,
        scenario_pack=scenario_pack,
        scenarios=scenario_results,
        token_fingerprint=fingerprint,
        rocketride_call_success=call_success,
    )
    write_results(results, output_path)
    return results


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run read-only Guild experiment evaluation."
    )
    parser.add_argument("--mode", choices=MODES, default="grounded")
    parser.add_argument(
        "--scenario-pack",
        choices=("all", *PACK_ORDER),
        default="all",
    )
    parser.add_argument("--output-path", type=Path, default=Path("results.json"))
    return parser


def main() -> None:
    args = build_parser().parse_args()
    results = asyncio.run(
        run_evaluation(
            mode=args.mode,
            scenario_pack=args.scenario_pack,
            output_path=args.output_path,
        )
    )
    for line in scalar_lines(results["metrics"]):
        print(line)


if __name__ == "__main__":
    main()
