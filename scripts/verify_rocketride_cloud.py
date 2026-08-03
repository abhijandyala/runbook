"""Produce sanitized M5 evidence for the long-lived RocketRide Cloud pipeline."""

from __future__ import annotations

import ast
import asyncio
import hashlib
import hmac
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic
from typing import Any
from uuid import UUID, uuid4

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agents.diagnostician import diagnose
from agents.remediator import ALLOWED_ACTIONS, remediate
from contracts import ActionProposal, Alert, HypothesisSet, TaskEnvelope
from orchestration.rocketride_client import RunbookInference

PIPELINE_PATH = PROJECT_ROOT / "runbook.pipe"
PACK_PATHS = {
    "pack_1": PROJECT_ROOT / "scenarios" / "s01_bad_deploy.json",
    "pack_2": PROJECT_ROOT / "scenarios" / "s02_upstream_failure.json",
}
JSON_PROOF_PATH = PROJECT_ROOT / "docs" / "evidence" / "rocketride-cloud-proof.json"
MARKDOWN_PROOF_PATH = (
    PROJECT_ROOT / "docs" / "evidence" / "rocketride-cloud-proof.md"
)
MIN_IDLE_SECONDS = 65.0
MAX_CLOUD_ATTEMPTS = 3
LOW_CONFIDENCE_MAX = 0.5
SAFE_ACTIONS = {
    "scale",
    "flush_cache",
    "notify",
    "diagnostic",
    "none",
}
FORBIDDEN_KEY_PARTS = {
    "apikey",
    "api_key",
    "credential",
    "endpoint",
    "host",
    "hostname",
    "password",
    "secret",
    "uri",
    "url",
}
FINGERPRINT_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class VerificationFailed(RuntimeError):
    """Raised after failed checks have been written to sanitized evidence."""


def utc_now() -> str:
    """Return a stable UTC timestamp for evidence."""

    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def token_fingerprint(token: str) -> str:
    """Reduce a pipeline token immediately to a one-way SHA-256 fingerprint."""

    if not token:
        raise ValueError("Cannot fingerprint an empty pipeline token")
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def current_pipeline_fingerprint(inference: RunbookInference) -> str:
    """Fingerprint the active token without returning or retaining its plaintext."""

    if inference.token is None:
        raise RuntimeError("RocketRide pipeline token is unavailable")
    return token_fingerprint(inference.token)


def read_project_uuid(path: Path = PIPELINE_PATH) -> str:
    """Read and validate the public RocketRide project identifier."""

    project_id = json.loads(path.read_text(encoding="utf-8"))["project_id"]
    return str(UUID(project_id))


def reasoning_received(reasoning: str) -> bool:
    """Reject local fallback text so proof requires a Cloud inference response."""

    normalized = reasoning.strip().lower()
    return bool(normalized) and "rocketride returned" not in normalized


def validate_sanitized_evidence(evidence: dict[str, Any]) -> None:
    """Fail closed if evidence contains endpoint or credential-shaped data."""

    fingerprint = evidence.get("token_fingerprint")
    if not isinstance(fingerprint, str) or not FINGERPRINT_PATTERN.fullmatch(
        fingerprint
    ):
        raise ValueError("Evidence must contain one SHA-256 token fingerprint")

    def visit(value: Any, path: tuple[str, ...] = ()) -> None:
        if isinstance(value, dict):
            for raw_key, child in value.items():
                key = str(raw_key)
                lowered = key.lower()
                if "token" in lowered and key != "token_fingerprint":
                    raise ValueError(f"Forbidden token field at {'.'.join(path + (key,))}")
                if any(part in lowered for part in FORBIDDEN_KEY_PARTS):
                    raise ValueError(
                        f"Forbidden sensitive field at {'.'.join(path + (key,))}"
                    )
                visit(child, path + (key,))
        elif isinstance(value, list):
            for index, child in enumerate(value):
                visit(child, path + (str(index),))
        elif isinstance(value, str):
            lowered = value.lower()
            if "://" in value or lowered.startswith(("sk-ant-", "sk_live_")):
                raise ValueError(
                    f"Forbidden endpoint or credential value at {'.'.join(path)}"
                )

    visit(evidence)


def direct_anthropic_imports(root: Path = PROJECT_ROOT) -> list[str]:
    """Return direct provider imports while excluding vendored/local environments."""

    findings: list[str] = []
    ignored_parts = {".git", ".local", ".venv"}
    for path in sorted(root.rglob("*.py")):
        relative = path.relative_to(root)
        if any(part in ignored_parts for part in relative.parts):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(relative))
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules = [node.module]
            if any(name == "anthropic" or name.startswith("anthropic.") for name in modules):
                findings.append(f"{relative}:{node.lineno}")
    return findings


def load_scenario(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


async def diagnose_with_cloud_reasoning(
    alert: Alert,
    inference: RunbookInference,
) -> tuple[HypothesisSet, int]:
    """Retry only transient invalid reasoning, always on the same pipeline."""

    hypotheses: HypothesisSet | None = None
    for attempt in range(1, MAX_CLOUD_ATTEMPTS + 1):
        hypotheses = await diagnose(alert, inference, graph_enabled=True)
        if hypotheses.hypotheses and reasoning_received(
            hypotheses.hypotheses[0].reasoning
        ):
            return hypotheses, attempt
    if hypotheses is None:
        raise RuntimeError("Diagnosis did not run")
    return hypotheses, MAX_CLOUD_ATTEMPTS


async def remediate_with_cloud_reasoning(
    hypotheses: HypothesisSet,
    service: str,
    inference: RunbookInference,
) -> tuple[ActionProposal, int]:
    """Retry only transient invalid justification on the active pipeline."""

    proposal: ActionProposal | None = None
    for attempt in range(1, MAX_CLOUD_ATTEMPTS + 1):
        proposal = await remediate(
            hypotheses,
            service,
            inference,
            graph_enabled=True,
        )
        if reasoning_received(proposal.reasoning):
            return proposal, attempt
    if proposal is None:
        raise RuntimeError("Remediation did not run")
    return proposal, MAX_CLOUD_ATTEMPTS


async def run_grounded_pack(
    label: str,
    path: Path,
    inference: RunbookInference,
) -> dict[str, Any]:
    """Run one fixture through graph-grounded diagnosis and remediation."""

    scenario = load_scenario(path)
    alert = Alert.model_validate(scenario["trigger"])
    expected = scenario["expected"]
    hypotheses, diagnosis_attempts = await diagnose_with_cloud_reasoning(
        alert,
        inference,
    )
    proposal, remediation_attempts = await remediate_with_cloud_reasoning(
        hypotheses,
        alert.service,
        inference,
    )
    selected = hypotheses.hypotheses[0]

    grounded = bool(selected.evidence) and all(
        item.source in {"graph", "past_incident"} for item in selected.evidence
    )
    expected_match = all(
        (
            selected.type == expected["hypothesis_type"],
            selected.affected_entity == expected["affected_entity"],
            proposal.action_type == expected["action_type"],
            proposal.action_target == expected["action_target"],
            proposal.safety_class == expected["safety_class"],
        )
    )
    diagnosis_cloud_pass = reasoning_received(selected.reasoning)
    remediation_cloud_pass = reasoning_received(proposal.reasoning)

    return {
        "scenario_id": scenario["scenario_id"],
        "alert_id": alert.alert_id,
        "trace_id": alert.alert_id,
        "grounded": grounded,
        "observed": {
            "hypothesis_type": selected.type,
            "affected_entity": selected.affected_entity,
            "confidence": selected.confidence,
            "action_type": proposal.action_type,
            "action_target": proposal.action_target,
            "safety_class": proposal.safety_class,
        },
        "expected": {
            "hypothesis_type": expected["hypothesis_type"],
            "affected_entity": expected["affected_entity"],
            "action_type": expected["action_type"],
            "action_target": expected["action_target"],
            "safety_class": expected["safety_class"],
        },
        "passes": {
            "expected_outcome": expected_match,
            "grounded_diagnosis": grounded,
            "diagnosis_cloud_reasoning": diagnosis_cloud_pass,
            "remediation_cloud_reasoning": remediation_cloud_pass,
        },
        "cloud_attempts": {
            "diagnosis": diagnosis_attempts,
            "remediation": remediation_attempts,
        },
        "pass": all(
            (
                expected_match,
                grounded,
                diagnosis_cloud_pass,
                remediation_cloud_pass,
            )
        ),
        "label": label,
    }


def idle_health_envelope() -> TaskEnvelope:
    trace_id = f"trace_rr_idle_health_{uuid4().hex[:12]}"
    return TaskEnvelope(
        role="remediator",
        trace_id=trace_id,
        payload={
            "hypothesis": {
                "type": "unknown",
                "affected_entity": "runbook-health",
                "confidence": 0.1,
                "evidence": [],
            },
            "action": {
                "action_type": "diagnostic",
                "action_target": "runbook-health",
                "action_params": {},
                "safety_class": "safe",
                "runbook_source": "",
                "confidence": 0.1,
            },
        },
    )


async def run_judge_novel_alert(
    inference: RunbookInference,
) -> dict[str, Any]:
    """Verify an unknown service degrades to a low-confidence safe action."""

    unique_suffix = uuid4().hex[:12]
    service = f"judge-unknown-service-{unique_suffix}"
    alert = Alert(
        alert_id=f"alrt_judge_unknown_{unique_suffix}",
        fired_at=utc_now(),
        severity="warning",
        service=service,
        metric="queue_depth",
        value=91,
        threshold=20,
        labels={"env": "prod", "source": "judge-input"},
        annotations={"summary": "Judge-entered novel unknown-service alert"},
    )
    hypotheses, diagnosis_attempts = await diagnose_with_cloud_reasoning(
        alert,
        inference,
    )
    proposal, remediation_attempts = await remediate_with_cloud_reasoning(
        hypotheses,
        service,
        inference,
    )
    selected = hypotheses.hypotheses[0]

    grounded_graph_miss = bool(selected.evidence) and all(
        item.source == "graph" for item in selected.evidence
    )
    low_confidence = (
        selected.confidence <= LOW_CONFIDENCE_MAX
        and proposal.remediator_confidence <= LOW_CONFIDENCE_MAX
    )
    safe_action = (
        proposal.action_type in ALLOWED_ACTIONS
        and proposal.action_type in SAFE_ACTIONS
        and proposal.safety_class == "safe"
    )
    unknown_service = (
        selected.type == "unknown"
        and selected.affected_entity == service
        and service.endswith(unique_suffix)
    )
    diagnosis_cloud_pass = reasoning_received(selected.reasoning)
    remediation_cloud_pass = reasoning_received(proposal.reasoning)

    return {
        "scenario_id": "judge_novel_unknown_service",
        "alert_id": alert.alert_id,
        "trace_id": alert.alert_id,
        "service_id": service,
        "grounded": grounded_graph_miss,
        "observed": {
            "hypothesis_type": selected.type,
            "confidence": selected.confidence,
            "action_type": proposal.action_type,
            "action_target": proposal.action_target,
            "safety_class": proposal.safety_class,
            "remediator_confidence": proposal.remediator_confidence,
        },
        "passes": {
            "unique_unknown_service": unknown_service,
            "low_confidence": low_confidence,
            "allowed_safe_action": safe_action,
            "grounded_graph_miss": grounded_graph_miss,
            "diagnosis_cloud_reasoning": diagnosis_cloud_pass,
            "remediation_cloud_reasoning": remediation_cloud_pass,
        },
        "cloud_attempts": {
            "diagnosis": diagnosis_attempts,
            "remediation": remediation_attempts,
        },
        "pass": all(
            (
                unknown_service,
                low_confidence,
                safe_action,
                grounded_graph_miss,
                diagnosis_cloud_pass,
                remediation_cloud_pass,
            )
        ),
    }


async def collect_evidence() -> dict[str, Any]:
    """Run every M5 check through one uninterrupted inference context."""

    project_uuid = read_project_uuid()
    import_findings = direct_anthropic_imports()

    async with RunbookInference(str(PIPELINE_PATH)) as inference:
        initial_fingerprint = current_pipeline_fingerprint(inference)

        pack_outcomes = {
            label: await run_grounded_pack(label, path, inference)
            for label, path in PACK_PATHS.items()
        }
        continuity_after_packs = hmac.compare_digest(
            initial_fingerprint,
            current_pipeline_fingerprint(inference),
        )

        idle_started = monotonic()
        await asyncio.sleep(MIN_IDLE_SECONDS)
        idle_duration = monotonic() - idle_started

        health_envelope = idle_health_envelope()
        health_response = await inference.invoke(health_envelope)
        health_reasoning = str(health_response.get("reasoning", ""))
        health_pass = (
            health_response.get("trace_id") == health_envelope.trace_id
            and reasoning_received(health_reasoning)
        )
        continuity_after_idle = hmac.compare_digest(
            initial_fingerprint,
            current_pipeline_fingerprint(inference),
        )

        judge_outcome = await run_judge_novel_alert(inference)
        continuity_after_judge = hmac.compare_digest(
            initial_fingerprint,
            current_pipeline_fingerprint(inference),
        )

    scenarios = {**pack_outcomes, "judge_novel": judge_outcome}
    passes = {
        "pack_1": pack_outcomes["pack_1"]["pass"],
        "pack_2": pack_outcomes["pack_2"]["pass"],
        "pipeline_continuity_after_packs": continuity_after_packs,
        "idle_duration_at_least_65_seconds": idle_duration >= MIN_IDLE_SECONDS,
        "post_idle_health_task": health_pass,
        "pipeline_continuity_after_idle": continuity_after_idle,
        "judge_novel_alert": judge_outcome["pass"],
        "pipeline_continuity_after_judge": continuity_after_judge,
        "direct_anthropic_import_audit": not import_findings,
    }
    evidence = {
        "schema_version": 1,
        "timestamp": utc_now(),
        "project_uuid": project_uuid,
        "token_fingerprint": initial_fingerprint,
        "scenario_outcomes": scenarios,
        "idle_duration_seconds": round(idle_duration, 3),
        "idle_health": {
            "trace_id": health_envelope.trace_id,
            "role": health_envelope.role,
            "action_type": "diagnostic",
            "safety_class": "safe",
            "pass": health_pass,
        },
        "trace_ids": [
            pack_outcomes["pack_1"]["trace_id"],
            pack_outcomes["pack_2"]["trace_id"],
            health_envelope.trace_id,
            judge_outcome["trace_id"],
        ],
        "alert_ids": [
            pack_outcomes["pack_1"]["alert_id"],
            pack_outcomes["pack_2"]["alert_id"],
            judge_outcome["alert_id"],
        ],
        "passes": passes,
        "overall_pass": all(passes.values()),
    }
    validate_sanitized_evidence(evidence)
    return evidence


def render_markdown(evidence: dict[str, Any]) -> str:
    """Render concise, credential-free Q&A proof from machine evidence."""

    pack_1 = evidence["scenario_outcomes"]["pack_1"]["observed"]
    pack_2 = evidence["scenario_outcomes"]["pack_2"]["observed"]
    judge = evidence["scenario_outcomes"]["judge_novel"]["observed"]
    status = "PASS" if evidence["overall_pass"] else "FAIL"
    packs_status = "Yes" if (
        evidence["passes"]["pack_1"] and evidence["passes"]["pack_2"]
    ) else "No"
    idle_status = "Yes" if (
        evidence["passes"]["post_idle_health_task"]
        and evidence["passes"]["pipeline_continuity_after_idle"]
    ) else "No"
    judge_status = "Yes" if evidence["passes"]["judge_novel_alert"] else "No"
    import_status = (
        "No"
        if evidence["passes"]["direct_anthropic_import_audit"]
        else "Yes"
    )
    return "\n".join(
        [
            "# RocketRide Cloud M5 proof",
            "",
            f"- **Overall:** {status}",
            f"- **Observed at:** {evidence['timestamp']}",
            f"- **Project UUID:** `{evidence['project_uuid']}`",
            (
                "- **Pipeline token fingerprint (SHA-256 only):** "
                f"`{evidence['token_fingerprint']}`"
            ),
            "",
            "## Q&A",
            "",
            (
                "**Did both grounded packs use the deployed pipeline?** "
                f"{packs_status}. "
                f"Pack 1 returned `{pack_1['hypothesis_type']}` → "
                f"`{pack_1['action_type']}`; Pack 2 returned "
                f"`{pack_2['hypothesis_type']}` → `{pack_2['action_type']}`."
            ),
            "",
            (
                "**Did the pipeline survive an idle interval without restart?** "
                f"{idle_status}. No verification call was sent for "
                f"{evidence['idle_duration_seconds']:.3f} seconds; the subsequent "
                "health remediation passed and the token fingerprint was unchanged."
            ),
            "",
            (
                "**Did a novel unknown service fail safely?** "
                f"{judge_status}. It returned "
                f"`{judge['hypothesis_type']}` at confidence "
                f"{judge['confidence']:.2f}, then `{judge['action_type']}` with "
                f"safety class `{judge['safety_class']}`."
            ),
            "",
            (
                "**Were direct Anthropic SDK imports found?** "
                f"{import_status}. All inference "
                "continued through RocketRide."
            ),
            "",
            (
                "The JSON companion contains sanitized trace IDs, alert IDs, outcomes, "
                "durations, and individual pass booleans. It contains no plaintext "
                "pipeline token, credentials, or endpoint hosts."
            ),
            "",
        ]
    )


def write_proof(evidence: dict[str, Any]) -> None:
    """Atomically write sanitized JSON and Markdown evidence."""

    validate_sanitized_evidence(evidence)
    JSON_PROOF_PATH.parent.mkdir(parents=True, exist_ok=True)
    json_temp = JSON_PROOF_PATH.with_suffix(".json.tmp")
    markdown_temp = MARKDOWN_PROOF_PATH.with_suffix(".md.tmp")
    json_temp.write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    markdown_temp.write_text(render_markdown(evidence), encoding="utf-8")
    json_temp.replace(JSON_PROOF_PATH)
    markdown_temp.replace(MARKDOWN_PROOF_PATH)


async def async_main() -> None:
    evidence = await collect_evidence()
    write_proof(evidence)
    if not evidence["overall_pass"]:
        raise VerificationFailed("One or more sanitized M5 checks failed")
    print(f"RocketRide Cloud M5 verification passed: {JSON_PROOF_PATH.relative_to(PROJECT_ROOT)}")
    print(f"Q&A proof: {MARKDOWN_PROOF_PATH.relative_to(PROJECT_ROOT)}")


def main() -> int:
    try:
        asyncio.run(async_main())
    except Exception as exc:  # noqa: BLE001 - never print exception data that may be sensitive.
        print(f"RocketRide Cloud M5 verification failed ({type(exc).__name__}).")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
