"""Deterministic diagnosis flow with explanations supplied by RocketRide."""

from __future__ import annotations

from datetime import datetime, timezone
from math import isfinite
from typing import Any, Protocol
from uuid import uuid4

from contracts import Alert, Evidence, Hypothesis, HypothesisSet, TaskEnvelope
from falkor.queries import fetch_graph_context


class Inference(Protocol):
    async def invoke(self, envelope: TaskEnvelope) -> dict[str, Any]: ...


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _hours_between(earlier: str, later: str) -> float:
    start = datetime.fromisoformat(earlier.replace("Z", "+00:00"))
    end = datetime.fromisoformat(later.replace("Z", "+00:00"))
    return max(0.0, (end - start).total_seconds() / 3600)


def _candidate_score(
    *,
    age_hours: float,
    graph_distance: int,
    matching_incidents: int,
) -> float:
    recency = max(0.0, 1.0 - age_hours / 8.0)
    frequency = 1.0 if matching_incidents else 0.0
    distance = 1.0 / (1.0 + graph_distance)
    return round(min(0.95, 0.4 * recency + 0.3 * frequency + 0.3 * distance), 2)


def _rank_candidates(alert: Alert, context: dict[str, Any]) -> list[dict[str, Any]]:
    matching_counts: dict[str, int] = {}
    for incident in context["past_incidents"]:
        cause = incident["root_cause"]
        matching_counts[cause] = matching_counts.get(cause, 0) + 1

    candidates: list[dict[str, Any]] = []
    for deploy in context["recent_deploys"]:
        hypothesis_type = (
            "recent_deploy" if deploy["distance"] == 0 else "dependency_failure"
        )
        score = _candidate_score(
            age_hours=_hours_between(deploy["deployed_at"], alert.fired_at),
            graph_distance=deploy["distance"],
            matching_incidents=matching_counts.get(hypothesis_type, 0),
        )
        candidates.append(
            {
                "hypothesis_id": f"hyp_{uuid4().hex[:10]}",
                "type": hypothesis_type,
                "root_cause_description": (
                    f"Deploy {deploy['id']} affecting {deploy['service']} occurred "
                    "inside the four-hour incident window."
                ),
                "affected_entity": deploy["id"]
                if deploy["distance"] == 0
                else deploy["service"],
                "confidence": score,
                "evidence": [
                    {
                        "source": "graph",
                        "ref": deploy["id"],
                        "detail": (
                            f"{deploy['service']} is {deploy['distance']} graph hops "
                            f"from {alert.service}; commit {deploy['commit_hash']} "
                            f"deployed at {deploy['deployed_at']}."
                        ),
                    }
                ],
            }
        )

    if not candidates and context["past_incidents"]:
        incident = context["past_incidents"][0]
        candidates.append(
            {
                "hypothesis_id": f"hyp_{uuid4().hex[:10]}",
                "type": "past_pattern",
                "root_cause_description": (
                    f"Alert matches resolved incident {incident['id']}."
                ),
                "affected_entity": alert.service,
                "confidence": 0.6,
                "evidence": [
                    {
                        "source": "past_incident",
                        "ref": incident["id"],
                        "detail": (
                            f"Prior root cause {incident['root_cause']} was resolved "
                            f"with {incident['resolution']}."
                        ),
                    }
                ],
            }
        )

    return sorted(candidates, key=lambda item: item["confidence"], reverse=True)[:3]


def _reasoning_map(
    response: dict[str, Any],
    candidate_ids: list[str],
) -> dict[str, str]:
    raw = response.get("reasoning_by_hypothesis", {})
    if isinstance(raw, dict):
        mapped = {str(key): str(value) for key, value in raw.items()}
        if any(candidate_id in mapped for candidate_id in candidate_ids):
            return mapped
        return dict(zip(candidate_ids, mapped.values(), strict=False))
    if isinstance(raw, list):
        mapped = {
            str(item.get("hypothesis_id")): str(
                item.get("explanation") or item.get("reasoning") or ""
            )
            for item in raw
            if isinstance(item, dict) and item.get("hypothesis_id")
        }
        if any(candidate_id in mapped for candidate_id in candidate_ids):
            return mapped
        explanations = [
            str(item.get("explanation") or item.get("reasoning") or "")
            for item in raw
            if isinstance(item, dict)
        ]
        return dict(zip(candidate_ids, explanations, strict=False))
    return {}


def _safe_alert_only_confidence(value: Any) -> float:
    if isinstance(value, bool):
        return 0.4
    try:
        confidence = float(value)
    except (TypeError, ValueError, OverflowError):
        return 0.4
    if not isfinite(confidence):
        return 0.4
    return min(0.5, max(0.0, confidence))


async def _alert_only_guess(alert: Alert, inference: Inference) -> Hypothesis:
    try:
        response = await inference.invoke(
            TaskEnvelope(
                role="diagnostician",
                trace_id=alert.alert_id,
                payload={
                    "mode": "alert_only_guess",
                    "graph_context": None,
                    "alert": alert.model_dump(mode="json"),
                    "allowed_types": [
                        "recent_deploy",
                        "dependency_failure",
                        "past_pattern",
                        "external_event",
                        "unknown",
                    ],
                    "instruction": (
                        "No graph context is available. Guess from the raw alert "
                        "alone and return "
                        "guess={type, affected_entity, confidence, reasoning}."
                    ),
                },
            )
        )
        guess = response.get("guess", {})
    except Exception:  # noqa: BLE001 - RocketRide failures degrade to a safe guess.
        guess = {
            "type": "unknown",
            "affected_entity": alert.service,
            "confidence": 0.1,
            "reasoning": (
                "RocketRide returned an invalid response; no diagnosis was attempted."
            ),
        }
    allowed = {
        "recent_deploy",
        "dependency_failure",
        "past_pattern",
        "external_event",
        "unknown",
    }
    guessed_type = guess.get("type", "unknown")
    if guessed_type not in allowed:
        guessed_type = "unknown"
    return Hypothesis(
        hypothesis_id=f"hyp_{uuid4().hex[:10]}",
        type=guessed_type,
        root_cause_description=str(
            guess.get("reasoning", "Alert-only inference returned no explanation.")
        ),
        affected_entity=str(guess.get("affected_entity", alert.service)),
        confidence=_safe_alert_only_confidence(guess.get("confidence")),
        evidence=[
            Evidence(
                source="alert",
                ref=alert.alert_id,
                detail="No FalkorDB graph context was available.",
            )
        ],
        reasoning=str(
            guess.get("reasoning", "Guessed from the raw alert without graph context.")
        ),
    )


async def diagnose(
    alert: Alert,
    inference: Inference,
    *,
    graph_enabled: bool = True,
) -> HypothesisSet:
    context = fetch_graph_context(alert, enabled=graph_enabled)
    if not graph_enabled:
        hypotheses = [await _alert_only_guess(alert, inference)]
    else:
        candidates = _rank_candidates(alert, context)
        if not candidates:
            candidates = [
                {
                    "hypothesis_id": f"hyp_{uuid4().hex[:10]}",
                    "type": "unknown",
                    "root_cause_description": (
                        f"No operational memory matched {alert.service}."
                    ),
                    "affected_entity": alert.service,
                    "confidence": 0.25,
                    "evidence": [
                        {
                            "source": "graph",
                            "ref": alert.service,
                            "detail": "No matching service, deploy, or incident was found.",
                        }
                    ],
                }
            ]

        try:
            response = await inference.invoke(
                TaskEnvelope(
                    role="diagnostician",
                    trace_id=alert.alert_id,
                    payload={
                        "mode": "grounded_candidates",
                        "alert": alert.model_dump(mode="json"),
                        "candidates": candidates,
                    },
                )
            )
            reasoning = _reasoning_map(
                response,
                [candidate["hypothesis_id"] for candidate in candidates],
            )
        except Exception:  # noqa: BLE001 - RocketRide failures degrade safely.
            reasoning = {
                candidate["hypothesis_id"]: (
                    "RocketRide returned an invalid response; human review is required."
                )
                for candidate in candidates
            }
        hypotheses = [
            Hypothesis(
                **candidate,
                reasoning=reasoning.get(
                    candidate["hypothesis_id"],
                    "RocketRide returned no explanation for this candidate.",
                ),
            )
            for candidate in candidates
        ]

    return HypothesisSet(
        alert_id=alert.alert_id,
        hypotheses=hypotheses,
        linkup_hits=0,
        graph_query_ms=context["graph_query_ms"],
        generated_at=_iso_now(),
    )
