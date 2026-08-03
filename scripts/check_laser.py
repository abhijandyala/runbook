"""Idempotent LaserData topology and typed-message health check."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from uuid import uuid4

from contracts import Alert
from streams.iggy_client import IggyClient
from streams.stream_names import TOPIC_ALERTS


async def main() -> None:
    alert = Alert(
        alert_id=f"alrt_health_{uuid4().hex[:10]}",
        fired_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        severity="info",
        service="runbook-health",
        metric="laser_round_trip",
        value=1,
        threshold=1,
        labels={"env": "healthcheck"},
        annotations={"summary": "LaserData typed-message health check"},
    )
    async with IggyClient() as client:
        await client.ensure_topology()
        await client.ensure_topology()
        await client.publish(TOPIC_ALERTS, alert)
        async with asyncio.timeout(10):
            async for received in client.subscribe(
                TOPIC_ALERTS,
                f"health-{uuid4().hex[:8]}",
            ):
                if received.alert_id == alert.alert_id:
                    print(
                        "LaserData healthy: sentry stream, four topics, "
                        f"typed round trip {alert.alert_id}."
                    )
                    return
    raise RuntimeError("LaserData health check ended without receiving its alert")


if __name__ == "__main__":
    asyncio.run(main())
