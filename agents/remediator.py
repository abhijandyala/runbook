"""Graph-backed remediation selection with RocketRide justification."""

from __future__ import annotations

import json
from typing import Any, Protocol
from uuid import uuid4

from contracts import ActionProposal, HypothesisSet, TaskEnvelope
from falkor.queries import find_runbook

ALLOWED_ACTIONS = {
    "rollback",
    "restart",
    "scale",
    "flush_cache",
    "notify",
    "diagnostic",
    "none",
}

SAFETY_CLASS_FOR_ACTION = {
    "rollback": "standard",
    "restart": "standard",
    "scale": "safe",
    "flush_cache": "safe",
    "notify": "safe",
    "diagnostic": "safe",
    "none": "safe",
}


class Inference(Protocol):
    async def invoke(self, envelope: TaskEnvelope) -> dict[str, Any]: ...


def enforce_action_policy(proposal: ActionProposal) -> ActionProposal:
    """Validate that a final proposal complies with the public action policy."""
    if proposal.action_type not in ALLOWED_ACTIONS:
        raise ValueError(f"Unsupported remediation action: {proposal.action_type!r}")

    expected_safety_class = SAFETY_CLASS_FOR_ACTION[proposal.action_type]
    if proposal.safety_class != expected_safety_class:
        raise ValueError(
            "Invalid safety class for remediation action "
            f"{proposal.action_type!r}: expected {expected_safety_class!r}, "
            f"got {proposal.safety_class!r}"
        )

    return proposal


async def remediate(
    hypotheses: HypothesisSet,
    service: str,
    inference: Inference,
    *,
    graph_enabled: bool = True,
) -> ActionProposal:
    if not hypotheses.hypotheses:
        raise ValueError("Cannot remediate an empty HypothesisSet")

    selected = hypotheses.hypotheses[0]
    runbook = find_runbook(selected.type, service) if graph_enabled else None
    if runbook is None:
        action_type = "diagnostic"
        action_target = service
        action_params: dict[str, Any] = {}
        runbook_source = ""
        confidence = min(selected.confidence, 0.4)
    else:
        runbook_action = runbook["action"]
        if not isinstance(runbook_action, str) or runbook_action not in ALLOWED_ACTIONS:
            action_type = "none"
            action_target = service
            action_params = {}
            runbook_source = runbook["id"]
            confidence = min(selected.confidence, 0.2)
        else:
            action_type = runbook_action
            action_target = runbook["action_target"]
            if action_target == "latest_deploy":
                action_target = selected.affected_entity
            action_params = json.loads(runbook["parameters_json"] or "{}")
            runbook_source = runbook["id"]
            confidence = round(
                min(selected.confidence, float(runbook["success_rate"])),
                2,
            )

    safety_class = SAFETY_CLASS_FOR_ACTION[action_type]
    proposal_id = f"prop_{uuid4().hex[:10]}"
    try:
        response = await inference.invoke(
            TaskEnvelope(
                role="remediator",
                trace_id=hypotheses.alert_id,
                payload={
                    "hypothesis": selected.model_dump(mode="json"),
                    "action": {
                        "proposal_id": proposal_id,
                        "action_type": action_type,
                        "action_target": action_target,
                        "action_params": action_params,
                        "safety_class": safety_class,
                        "runbook_source": runbook_source,
                        "confidence": confidence,
                    },
                },
            )
        )
        reasoning = str(
            response.get("reasoning", "RocketRide returned no justification.")
        )
    except Exception:  # noqa: BLE001 - reasoning failure must not alter the action.
        reasoning = "RocketRide returned no justification."

    proposal = ActionProposal(
        proposal_id=proposal_id,
        alert_id=hypotheses.alert_id,
        target_hypothesis_id=selected.hypothesis_id,
        action_type=action_type,
        action_target=action_target,
        action_params=action_params,
        safety_class=safety_class,
        remediator_confidence=confidence,
        runbook_source=runbook_source,
        reasoning=reasoning,
    )
    return enforce_action_policy(proposal)
