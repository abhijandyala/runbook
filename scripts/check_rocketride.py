"""Verify the deployed RocketRide pipeline reaches its Anthropic control node."""

from __future__ import annotations

import asyncio
from uuid import uuid4

from contracts import TaskEnvelope
from orchestration.rocketride_client import RunbookInference


async def main() -> None:
    trace_id = f"alrt_rr_health_{uuid4().hex[:10]}"
    envelope = TaskEnvelope(
        role="remediator",
        trace_id=trace_id,
        payload={
            "hypothesis": {
                "type": "unknown",
                "confidence": 0.1,
                "evidence": [],
            },
            "action": {
                "action_type": "diagnostic",
                "action_target": "runbook-health",
                "safety_class": "safe",
                "runbook_source": "",
            },
        },
    )
    async with RunbookInference() as inference:
        result = await inference.invoke(envelope)
    if result.get("trace_id") != trace_id or not result.get("reasoning"):
        raise RuntimeError("RocketRide health response failed contract validation")
    print(f"RocketRide Cloud healthy: traced inference {trace_id}.")


if __name__ == "__main__":
    asyncio.run(main())
