"""Run one alert through the complete durable-stream walking skeleton."""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from agents.diagnostician import diagnose
from agents.remediator import enforce_action_policy, remediate
from agents.reviewer import decide, resolve
from contracts import Alert, HypothesisSet
from falkor.writes import write_resolution
from orchestration.rocketride_client import RunbookInference
from streams.iggy_client import IggyClient
from streams.stream_names import (
    TOPIC_ALERTS,
    TOPIC_HYPOTHESES,
    TOPIC_PROPOSALS,
    TOPIC_RESOLUTIONS,
)


def _boundary(sponsor: str, message: str) -> None:
    print(f"[{sponsor}] {message}", flush=True)


async def _matching_message(
    streams: IggyClient,
    topic: str,
    alert_id: str,
):
    reader_name = f"runbook-{topic}-{uuid4().hex[:8]}"
    async for message in streams.subscribe(topic, reader_name):
        if getattr(message, "alert_id", None) == alert_id:
            return message
    raise RuntimeError(f"Subscription to {topic} ended unexpectedly")


async def run_alert(
    alert: Alert,
    *,
    graph_enabled: bool = True,
    approve: bool = False,
) -> dict[str, object]:
    async with IggyClient() as streams:
        await streams.ensure_topology()
        _boundary("LaserData", "sentry stream and four topics are ready")
        async with RunbookInference() as inference:
            await streams.publish(TOPIC_ALERTS, alert)
            _boundary("LaserData", f"published alerts/{alert.alert_id}")
            streamed_alert = await _matching_message(
                streams,
                TOPIC_ALERTS,
                alert.alert_id,
            )
            _boundary("LaserData", f"consumed alerts/{alert.alert_id}")

            hypotheses = await diagnose(
                streamed_alert,
                inference,
                graph_enabled=graph_enabled,
            )
            _boundary(
                "FalkorDB",
                f"queried operational memory in {hypotheses.graph_query_ms}ms",
            )
            _boundary(
                "RocketRide",
                f"generated diagnosis reasoning for {alert.alert_id}",
            )
            await streams.publish(TOPIC_HYPOTHESES, hypotheses)
            _boundary("LaserData", f"published hypotheses/{alert.alert_id}")
            streamed_hypotheses = await _matching_message(
                streams,
                TOPIC_HYPOTHESES,
                alert.alert_id,
            )
            _boundary("LaserData", f"consumed hypotheses/{alert.alert_id}")
            if not isinstance(streamed_hypotheses, HypothesisSet):
                streamed_hypotheses = HypothesisSet.model_validate(
                    streamed_hypotheses
                )

            proposal = await remediate(
                streamed_hypotheses,
                alert.service,
                inference,
                graph_enabled=graph_enabled,
            )
            _boundary(
                "RocketRide",
                f"generated remediation justification for {alert.alert_id}",
            )
            proposal = enforce_action_policy(proposal)
            await streams.publish(TOPIC_PROPOSALS, proposal)
            _boundary("LaserData", f"published proposals/{alert.alert_id}")
            streamed_proposal = await _matching_message(
                streams,
                TOPIC_PROPOSALS,
                alert.alert_id,
            )
            _boundary("LaserData", f"consumed proposals/{alert.alert_id}")

            reviewer_decision = decide(
                streamed_proposal,
                decision="approve" if approve else "reject",
                reviewer_note="Walking-skeleton CLI review",
            )
            resolution = resolve(
                streamed_proposal,
                reviewer_decision,
                outcome="verified",
                total_latency_ms=1200,
                cost_usd=0.001,
            )
            await streams.publish(TOPIC_RESOLUTIONS, resolution)
            _boundary("LaserData", f"published resolutions/{alert.alert_id}")

    created_incident = write_resolution(
        alert,
        resolution,
        root_cause=streamed_hypotheses.hypotheses[0].type,
    )
    _boundary("FalkorDB", f"persisted resolution as {created_incident}")
    return {
        "alert_id": alert.alert_id,
        "hypothesis": streamed_hypotheses.hypotheses[0].type,
        "confidence": streamed_hypotheses.hypotheses[0].confidence,
        "action": streamed_proposal.action_type,
        "decision": reviewer_decision.decision,
        "outcome": resolution.outcome,
        "incident_id": created_incident,
    }


async def run_scenario(
    scenario_path: Path,
    *,
    graph_enabled: bool = True,
    approve: bool = False,
) -> dict[str, object]:
    scenario = json.loads(scenario_path.read_text())
    return await run_alert(
        Alert.model_validate(scenario["trigger"]),
        graph_enabled=graph_enabled,
        approve=approve,
    )


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "scenario",
        nargs="?",
        default="scenarios/s01_bad_deploy.json",
    )
    parser.add_argument("--graph-off", action="store_true")
    parser.add_argument("--approve", action="store_true")
    parser.add_argument("--service")
    parser.add_argument("--metric", default="latency_p99_ms")
    parser.add_argument("--value", type=float, default=1000)
    parser.add_argument("--threshold", type=float, default=500)
    parser.add_argument(
        "--severity",
        choices=("critical", "warning", "info"),
        default="warning",
    )
    args = parser.parse_args()
    if args.service:
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        result = await run_alert(
            Alert(
                alert_id=f"alrt_judge_{uuid4().hex[:10]}",
                fired_at=now,
                severity=args.severity,
                service=args.service,
                metric=args.metric,
                value=args.value,
                threshold=args.threshold,
                labels={"env": "prod", "source": "judge-input"},
                annotations={"summary": "Judge-entered novel alert"},
            ),
            graph_enabled=not args.graph_off,
            approve=args.approve,
        )
    else:
        result = await run_scenario(
            Path(args.scenario),
            graph_enabled=not args.graph_off,
            approve=args.approve,
        )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
