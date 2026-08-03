"""Durable incident-resolution writes into FalkorDB."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from contracts import Alert, Resolution
from falkor.client import get_graph
from falkor.queries import (
    LINK_RUNBOOK_QUERY,
    WRITE_INCIDENT_QUERY,
    get_incident_by_id,
)


def write_resolution(
    alert: Alert,
    resolution: Resolution,
    *,
    root_cause: str,
) -> str:
    timestamp = datetime.now(timezone.utc)
    action = resolution.final_action
    params = {
        "service": alert.service,
        "incident_id": resolution.incident_id,
        "alert_id": alert.alert_id,
        "timestamp": timestamp.isoformat().replace("+00:00", "Z"),
        "timestamp_epoch": int(timestamp.timestamp()),
        "metric": alert.metric,
        "root_cause": root_cause,
        "resolution": action.action_type if action else "none",
        "action_taken": (
            json.dumps(action.model_dump(mode="json"), sort_keys=True)
            if action
            else ""
        ),
        "outcome": resolution.outcome,
        "reviewer_decision": json.dumps(
            resolution.reviewer_decision.model_dump(mode="json"),
            sort_keys=True,
        ),
        "resolved": True,
        "total_latency_ms": resolution.total_latency_ms,
        "cost_usd": resolution.cost_usd,
    }

    graph = get_graph()
    rows = graph.query(WRITE_INCIDENT_QUERY, params=params).result_set
    if not rows:
        raise RuntimeError(f"FalkorDB did not create incident {resolution.incident_id}")

    if action and action.runbook_source:
        graph.query(
            LINK_RUNBOOK_QUERY,
            params={
                "incident_id": resolution.incident_id,
                "runbook_id": action.runbook_source,
            },
        )

    created_incident = rows[0][0]
    verified = get_incident_by_id(resolution.incident_id)
    if (
        created_incident != resolution.incident_id
        or verified is None
        or verified["id"] != resolution.incident_id
    ):
        raise RuntimeError(
            f"FalkorDB write for incident {resolution.incident_id} "
            "could not be verified with a fresh query"
        )
    return created_incident
