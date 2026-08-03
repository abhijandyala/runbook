"""All read-only Cypher used by runbook."""

from __future__ import annotations

from datetime import datetime, timedelta
from time import perf_counter
from typing import Any

from contracts import Alert
from falkor.client import get_graph

PING_QUERY = "RETURN 1 AS ok"

SERVICE_QUERY = """
MATCH (svc:Service {name: $service})
RETURN svc.name, svc.tier, svc.owner_team, svc.criticality
"""

DEPENDENCY_QUERY = """
MATCH path=(svc:Service {name: $service})-[:DEPENDS_ON*1..3]->(dep:Service)
RETURN DISTINCT dep.name, dep.tier, length(path) AS distance
ORDER BY distance ASC, dep.name ASC
"""

RECENT_DEPLOY_QUERY = """
MATCH path=(svc:Service {name: $service})-[:DEPENDS_ON*0..3]->(entity:Service)
MATCH (deploy:Deploy)-[:AFFECTS]->(entity)
WHERE deploy.deployed_at_epoch >= $cutoff_epoch
  AND deploy.deployed_at_epoch <= $fired_epoch
RETURN DISTINCT deploy.id, deploy.commit_hash, deploy.deployed_at, entity.name,
       length(path) AS distance
ORDER BY deploy.deployed_at_epoch DESC
"""

PAST_INCIDENT_QUERY = """
MATCH (incident:Incident)-[:INVOLVED]->(:Service {name: $service})
WHERE incident.resolved = true AND incident.metric = $metric
RETURN incident.id, incident.timestamp, incident.root_cause,
       incident.resolution, incident.outcome
ORDER BY incident.timestamp_epoch DESC
LIMIT 5
"""

RUNBOOK_QUERY = """
MATCH (runbook:Runbook)-[:APPLIES_TO]->(pattern:CausePattern)
WHERE pattern.type = $hypothesis_type
  AND (pattern.service = $service OR pattern.service = "*")
RETURN runbook.id, runbook.action, runbook.action_target,
       runbook.parameters_json, runbook.safety_class, runbook.success_rate
ORDER BY runbook.success_rate DESC
LIMIT 1
"""

WRITE_INCIDENT_QUERY = """
MERGE (service:Service {name: $service})
ON CREATE SET service.tier = "unknown",
              service.owner_team = "unassigned",
              service.criticality = "unknown"
MERGE (incident:Incident {id: $incident_id})
SET incident.alert_id = $alert_id,
    incident.timestamp = $timestamp,
    incident.timestamp_epoch = $timestamp_epoch,
    incident.metric = $metric,
    incident.root_cause = $root_cause,
    incident.resolution = $resolution,
    incident.action_taken = $action_taken,
    incident.outcome = $outcome,
    incident.reviewer_decision = $reviewer_decision,
    incident.resolved = $resolved,
    incident.total_latency_ms = $total_latency_ms,
    incident.cost_usd = $cost_usd
MERGE (incident)-[:INVOLVED]->(service)
RETURN incident.id
"""

GET_INCIDENT_BY_ID_QUERY = """
MATCH (incident:Incident {id: $incident_id})
RETURN incident.id, incident.alert_id, incident.outcome, incident.resolved,
       incident.resolution, incident.action_taken, incident.reviewer_decision,
       incident.root_cause, incident.total_latency_ms, incident.cost_usd
LIMIT 1
"""

LINK_RUNBOOK_QUERY = """
MATCH (incident:Incident {id: $incident_id})
MATCH (runbook:Runbook {id: $runbook_id})
MERGE (incident)-[:USED_RUNBOOK]->(runbook)
RETURN runbook.id
"""

CLEAR_GRAPH_QUERY = "MATCH (node) DETACH DELETE node"

UPSERT_SERVICE_QUERY = """
MERGE (service:Service {name: $name})
SET service.tier = $tier,
    service.owner_team = $owner_team,
    service.criticality = $criticality
"""

UPSERT_DEPENDENCY_QUERY = """
MATCH (source:Service {name: $source})
MATCH (target:Service {name: $target})
MERGE (source)-[:DEPENDS_ON]->(target)
"""

UPSERT_DEPLOY_QUERY = """
MERGE (deploy:Deploy {id: $id})
SET deploy.commit_hash = $commit_hash,
    deploy.deployed_at = $deployed_at,
    deploy.deployed_at_epoch = $deployed_at_epoch,
    deploy.deployed_by = $deployed_by
WITH deploy
MATCH (service:Service {name: $service})
MERGE (deploy)-[:AFFECTS]->(service)
"""

UPSERT_PAST_INCIDENT_QUERY = """
MERGE (incident:Incident {id: $id})
SET incident.metric = $metric,
    incident.timestamp = $timestamp,
    incident.timestamp_epoch = $timestamp_epoch,
    incident.root_cause = $root_cause,
    incident.resolution = $resolution,
    incident.outcome = $outcome,
    incident.resolved = true
WITH incident
MATCH (service:Service {name: $service})
MERGE (incident)-[:INVOLVED]->(service)
"""

UPSERT_PATTERN_QUERY = """
MERGE (pattern:CausePattern {id: $id})
SET pattern.type = $type,
    pattern.service = $service,
    pattern.metric = $metric
"""

UPSERT_RUNBOOK_QUERY = """
MERGE (runbook:Runbook {id: $id})
SET runbook.description = $description,
    runbook.action = $action,
    runbook.action_target = $action_target,
    runbook.parameters_json = $parameters_json,
    runbook.safety_class = $safety_class,
    runbook.success_rate = $success_rate
WITH runbook
MATCH (pattern:CausePattern {id: $pattern_id})
MERGE (runbook)-[:APPLIES_TO]->(pattern)
"""


def _epoch_seconds(timestamp: str) -> int:
    parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    return int(parsed.timestamp())


def fetch_graph_context(alert: Alert, *, enabled: bool = True) -> dict[str, Any]:
    """Return graph evidence only; disabled mode is deliberately an empty context."""

    if not enabled:
        return {
            "service": None,
            "dependencies": [],
            "recent_deploys": [],
            "past_incidents": [],
            "graph_query_ms": 0,
            "graph_enabled": False,
        }

    started = perf_counter()
    graph = get_graph()
    params = {"service": alert.service, "metric": alert.metric}

    service_rows = graph.query(SERVICE_QUERY, params=params).result_set
    dependency_rows = graph.query(DEPENDENCY_QUERY, params=params).result_set
    fired_epoch = _epoch_seconds(alert.fired_at)
    cutoff = fired_epoch - int(timedelta(hours=4).total_seconds())
    deploy_rows = graph.query(
        RECENT_DEPLOY_QUERY,
        params={
            **params,
            "cutoff_epoch": cutoff,
            "fired_epoch": fired_epoch,
        },
    ).result_set
    incident_rows = graph.query(PAST_INCIDENT_QUERY, params=params).result_set

    service = None
    if service_rows:
        row = service_rows[0]
        service = {
            "name": row[0],
            "tier": row[1],
            "owner_team": row[2],
            "criticality": row[3],
        }

    return {
        "service": service,
        "dependencies": [
            {"name": row[0], "tier": row[1], "distance": row[2]}
            for row in dependency_rows
        ],
        "recent_deploys": [
            {
                "id": row[0],
                "commit_hash": row[1],
                "deployed_at": row[2],
                "service": row[3],
                "distance": row[4],
            }
            for row in deploy_rows
        ],
        "past_incidents": [
            {
                "id": row[0],
                "timestamp": row[1],
                "root_cause": row[2],
                "resolution": row[3],
                "outcome": row[4],
            }
            for row in incident_rows
        ],
        "graph_query_ms": round((perf_counter() - started) * 1000),
        "graph_enabled": True,
    }


def find_runbook(
    hypothesis_type: str,
    service: str,
) -> dict[str, Any] | None:
    rows = get_graph().query(
        RUNBOOK_QUERY,
        params={"hypothesis_type": hypothesis_type, "service": service},
    ).result_set
    if not rows:
        return None
    row = rows[0]
    return {
        "id": row[0],
        "action": row[1],
        "action_target": row[2],
        "parameters_json": row[3],
        "safety_class": row[4],
        "success_rate": row[5],
    }


def get_incident_by_id(incident_id: str) -> dict[str, Any] | None:
    """Read an incident through a newly acquired graph connection."""

    rows = get_graph().query(
        GET_INCIDENT_BY_ID_QUERY,
        params={"incident_id": incident_id},
    ).result_set
    if not rows:
        return None
    row = rows[0]
    return {
        "id": row[0],
        "alert_id": row[1],
        "outcome": row[2],
        "resolved": row[3],
        "resolution": row[4],
        "action_taken": row[5],
        "reviewer_decision": row[6],
        "root_cause": row[7],
        "total_latency_ms": row[8],
        "cost_usd": row[9],
    }
