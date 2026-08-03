"""Idempotently seed the TestOps operational-memory graph."""

from __future__ import annotations

import argparse
from datetime import datetime

from falkor.client import get_graph, ping
from falkor.queries import (
    CLEAR_GRAPH_QUERY,
    UPSERT_DEPENDENCY_QUERY,
    UPSERT_DEPLOY_QUERY,
    UPSERT_PAST_INCIDENT_QUERY,
    UPSERT_PATTERN_QUERY,
    UPSERT_RUNBOOK_QUERY,
    UPSERT_SERVICE_QUERY,
)

SERVICES = [
    {
        "name": "checkout-service",
        "tier": "tier-1",
        "owner_team": "commerce",
        "criticality": "critical",
    },
    {
        "name": "payments-api",
        "tier": "tier-1",
        "owner_team": "payments",
        "criticality": "critical",
    },
    {
        "name": "postgres-primary",
        "tier": "tier-0",
        "owner_team": "database",
        "criticality": "critical",
    },
    {
        "name": "cart-service",
        "tier": "tier-2",
        "owner_team": "commerce",
        "criticality": "high",
    },
    {
        "name": "session-service",
        "tier": "tier-2",
        "owner_team": "platform",
        "criticality": "high",
    },
    {
        "name": "notification-worker",
        "tier": "tier-3",
        "owner_team": "engagement",
        "criticality": "medium",
    },
]

DEPENDENCIES = [
    {"source": "checkout-service", "target": "payments-api"},
    {"source": "checkout-service", "target": "cart-service"},
    {"source": "payments-api", "target": "postgres-primary"},
    {"source": "cart-service", "target": "postgres-primary"},
    {"source": "checkout-service", "target": "session-service"},
]

DEPLOYS = [
    {
        "id": "deploy_checkout_a7f3d2",
        "service": "checkout-service",
        "commit_hash": "a7f3d2",
        "deployed_at": "2026-08-03T09:31:00Z",
        "deployed_by": "release-bot",
    },
    {
        "id": "deploy_postgres_patch_8c91ef",
        "service": "postgres-primary",
        "commit_hash": "8c91ef",
        "deployed_at": "2026-08-03T12:22:00Z",
        "deployed_by": "database-team",
    },
]

PAST_INCIDENTS = [
    {
        "id": "inc_checkout_20260713",
        "service": "checkout-service",
        "metric": "latency_p99_ms",
        "root_cause": "recent_deploy",
        "resolution": "rollback",
        "outcome": "verified",
        "timestamp": "2026-07-13T04:12:00Z",
    },
    {
        "id": "inc_postgres_pool_20260618",
        "service": "checkout-service",
        "metric": "error_rate_pct",
        "root_cause": "dependency_failure",
        "resolution": "restart",
        "outcome": "verified",
        "timestamp": "2026-06-18T07:40:00Z",
    },
]

PATTERNS = [
    {
        "id": "pattern_recent_deploy_checkout",
        "type": "recent_deploy",
        "service": "checkout-service",
        "metric": "*",
    },
    {
        "id": "pattern_dependency_checkout",
        "type": "dependency_failure",
        "service": "checkout-service",
        "metric": "*",
    },
    {
        "id": "pattern_unknown",
        "type": "unknown",
        "service": "*",
        "metric": "*",
    },
    {
        "id": "pattern_external",
        "type": "external_event",
        "service": "*",
        "metric": "*",
    },
]

RUNBOOKS = [
    {
        "id": "rb_post_deploy_latency",
        "pattern_id": "pattern_recent_deploy_checkout",
        "description": "Rollback the most recent deploy affecting checkout.",
        "action": "rollback",
        "action_target": "latest_deploy",
        "parameters_json": "{}",
        "safety_class": "standard",
        "success_rate": 0.92,
    },
    {
        "id": "rb_reset_payment_pool",
        "pattern_id": "pattern_dependency_checkout",
        "description": "Restart payment pods to reset exhausted database connections.",
        "action": "restart",
        "action_target": "payments-api",
        "parameters_json": "{\"strategy\":\"rolling\"}",
        "safety_class": "standard",
        "success_rate": 0.87,
    },
    {
        "id": "rb_collect_diagnostics",
        "pattern_id": "pattern_unknown",
        "description": "Collect diagnostics and escalate without mutating production.",
        "action": "diagnostic",
        "action_target": "alerting-service",
        "parameters_json": "{}",
        "safety_class": "safe",
        "success_rate": 1.0,
    },
    {
        "id": "rb_external_notify",
        "pattern_id": "pattern_external",
        "description": "Notify on-call and wait for the external provider.",
        "action": "notify",
        "action_target": "on-call",
        "parameters_json": "{}",
        "safety_class": "safe",
        "success_rate": 1.0,
    },
]


def _epoch(timestamp: str) -> int:
    return int(datetime.fromisoformat(timestamp.replace("Z", "+00:00")).timestamp())


def seed(*, reset: bool = False) -> None:
    if not ping():
        raise RuntimeError("FalkorDB health check failed")
    graph = get_graph()
    if reset:
        graph.query(CLEAR_GRAPH_QUERY)

    for service in SERVICES:
        graph.query(UPSERT_SERVICE_QUERY, params=service)
    for dependency in DEPENDENCIES:
        graph.query(UPSERT_DEPENDENCY_QUERY, params=dependency)
    for deploy in DEPLOYS:
        graph.query(
            UPSERT_DEPLOY_QUERY,
            params={**deploy, "deployed_at_epoch": _epoch(deploy["deployed_at"])},
        )
    for incident in PAST_INCIDENTS:
        graph.query(
            UPSERT_PAST_INCIDENT_QUERY,
            params={**incident, "timestamp_epoch": _epoch(incident["timestamp"])},
        )
    for pattern in PATTERNS:
        graph.query(UPSERT_PATTERN_QUERY, params=pattern)
    for runbook in RUNBOOKS:
        graph.query(UPSERT_RUNBOOK_QUERY, params=runbook)

    print(
        "Seeded TestOps graph: "
        f"{len(SERVICES)} services, {len(DEPENDENCIES)} dependencies, "
        f"{len(DEPLOYS)} deploys, {len(PAST_INCIDENTS)} incidents, "
        f"{len(RUNBOOKS)} runbooks."
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Delete the runbook graph before idempotent seeding.",
    )
    seed(reset=parser.parse_args().reset)
