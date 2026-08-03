"""Capture sanitized live Cloud observability events from the M5 pipeline."""

from __future__ import annotations

import asyncio
import hashlib
import html
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from rocketride import RocketRideClient

from contracts import TaskEnvelope
from orchestration.rocketride_client import RunbookInference

PROJECT_ID = "5621bc5a-2ff1-4021-8592-81383fa4890f"
SOURCE = "chat_1"
PIPELINE_PATH = PROJECT_ROOT / "runbook.pipe"
JSONL_PATH = PROJECT_ROOT / "docs" / "evidence" / "rocketride-live-traces.jsonl"
MARKDOWN_PATH = PROJECT_ROOT / "docs" / "evidence" / "rocketride-live-traces.md"
HTML_PATH = PROJECT_ROOT / "docs" / "evidence" / "rocketride-live-traces.html"
MONITOR_TYPES = ("task", "summary", "flow", "output", "sse")
REQUIRED_EVENT_TYPES = ("task", "summary", "flow")
MAX_EVENT_WAIT_SECONDS = 20.0
POST_EVIDENCE_SETTLE_SECONDS = 2.0

SENSITIVE_KEY_PARTS = (
    "token",
    "apikey",
    "api_key",
    "api-key",
    "auth",
    "credential",
    "password",
    "secret",
    "uri",
    "url",
    "host",
    "endpoint",
)
SENSITIVE_VALUE_PATTERNS = (
    re.compile(r"[a-z][a-z0-9+.-]*://", re.IGNORECASE),
    re.compile(r"\b(?:sk-ant-|sk_live_|rr_[a-z0-9_-]*key)", re.IGNORECASE),
)
EVENT_CATEGORY_NAMES = {
    "apaevt_task": "task",
    "apaevt_status_update": "summary",
    "apaevt_flow": "flow",
    "apaevt_sse": "sse",
    "output": "output",
    "apaevt_output": "output",
}


class LiveTraceCaptureFailed(RuntimeError):
    """Raised when live observability proof cannot be established."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def fingerprint(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def is_sensitive_key(key: object) -> bool:
    lowered = str(key).lower()
    return any(part in lowered for part in SENSITIVE_KEY_PARTS)


def sanitize(value: Any, raw_token: str) -> Any:
    """Recursively drop sensitive keys and redact risky string values."""

    if isinstance(value, dict):
        return {
            str(key): sanitize(child, raw_token)
            for key, child in value.items()
            if not is_sensitive_key(key)
        }
    if isinstance(value, (list, tuple)):
        return [sanitize(child, raw_token) for child in value]
    if isinstance(value, str):
        if raw_token and raw_token in value:
            return "[REDACTED]"
        if any(pattern.search(value) for pattern in SENSITIVE_VALUE_PATTERNS):
            return "[REDACTED]"
        return value
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return sanitize(str(value), raw_token)


def event_category(event: dict[str, Any]) -> str:
    name = str(event.get("event", "unknown")).lower()
    if name in EVENT_CATEGORY_NAMES:
        return EVENT_CATEGORY_NAMES[name]
    if "output" in name:
        return "output"
    return name


def count_events(events: list[dict[str, Any]]) -> dict[str, int]:
    counts = Counter(event_category(event) for event in events)
    return {event_type: counts.get(event_type, 0) for event_type in MONITOR_TYPES}


def flow_chronology(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    chronology: list[dict[str, Any]] = []
    for event in events:
        if event_category(event) != "flow":
            continue
        body = event.get("body", {})
        if not isinstance(body, dict):
            body = {}
        trace = body.get("trace", {})
        if not isinstance(trace, dict):
            trace = {}
        chronology.append(
            {
                "captured_at": event.get("captured_at", ""),
                "sequence": event.get("seq", ""),
                "pipe": body.get("id", body.get("pipe_id", "")),
                "operation": body.get("op", ""),
                "component": body.get("component", ""),
                "components": body.get("pipes", []),
                "lane": trace.get("lane", ""),
            }
        )
    return chronology


def diagnostic_envelope(trace_id: str) -> TaskEnvelope:
    return TaskEnvelope(
        role="diagnostician",
        trace_id=trace_id,
        payload={
            "mode": "grounded_candidates",
            "alert": {
                "alert_id": trace_id,
                "service": "checkout-api",
                "severity": "critical",
                "metric": "error_rate",
            },
            "candidates": [
                {
                    "hypothesis_id": "hyp_live_capture_deploy",
                    "type": "recent_deploy",
                    "affected_entity": "checkout-api",
                    "confidence": 0.92,
                    "evidence": [
                        {
                            "source": "graph",
                            "ref": "deploy-live-capture",
                            "detail": "Deployment preceded the alert.",
                        }
                    ],
                }
            ],
        },
    )


def remediation_envelope(trace_id: str) -> TaskEnvelope:
    return TaskEnvelope(
        role="remediator",
        trace_id=trace_id,
        payload={
            "hypothesis": {
                "hypothesis_id": "hyp_live_capture_deploy",
                "type": "recent_deploy",
                "affected_entity": "checkout-api",
                "confidence": 0.92,
                "evidence": [
                    {
                        "source": "graph",
                        "ref": "deploy-live-capture",
                        "detail": "Deployment preceded the alert.",
                    }
                ],
            },
            "action": {
                "action_type": "rollback",
                "action_target": "checkout-api",
                "action_params": {"deployment": "deploy-live-capture"},
                "safety_class": "standard",
                "runbook_source": "runbook-live-capture",
                "confidence": 0.91,
            },
        },
    )


async def terminate_existing_pipeline() -> bool:
    """Resolve and terminate only the requested project/source pipeline."""

    client = RocketRideClient()
    await client.connect()
    try:
        token = await client.get_task_token(PROJECT_ID, SOURCE)
        if token is None:
            return False
        status = await client.get_task_status(token)
        if status.get("state") in {0, 5, 6}:
            return False
        await client.terminate(token)
        for _ in range(60):
            await asyncio.sleep(0.5)
            status = await client.get_task_status(token)
            if status.get("state") in {0, 5, 6}:
                return True
        raise LiveTraceCaptureFailed("Target pipeline did not stop before capture")
    finally:
        await client.disconnect()


async def wait_for_required_events(
    events: list[dict[str, Any]],
    notification: asyncio.Event,
) -> None:
    deadline = asyncio.get_running_loop().time() + MAX_EVENT_WAIT_SECONDS
    while asyncio.get_running_loop().time() < deadline:
        counts = count_events(events)
        if all(counts[event_type] > 0 for event_type in REQUIRED_EVENT_TYPES):
            await asyncio.sleep(POST_EVIDENCE_SETTLE_SECONDS)
            return
        notification.clear()
        remaining = deadline - asyncio.get_running_loop().time()
        try:
            await asyncio.wait_for(notification.wait(), timeout=min(1.0, remaining))
        except TimeoutError:
            pass
    counts = count_events(events)
    missing = [
        event_type
        for event_type in REQUIRED_EVENT_TYPES
        if counts[event_type] == 0
    ]
    raise LiveTraceCaptureFailed(
        "No live Cloud evidence captured for: " + ", ".join(missing)
    )


def validate_persisted_content(
    events: list[dict[str, Any]],
    raw_token: str,
) -> None:
    serialized = json.dumps(events, sort_keys=True)
    if raw_token in serialized:
        raise LiveTraceCaptureFailed("Raw task token reached sanitized evidence")

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if is_sensitive_key(key):
                    raise LiveTraceCaptureFailed(
                        f"Sensitive key survived sanitization: {key}"
                    )
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
        elif isinstance(value, str) and any(
            pattern.search(value) for pattern in SENSITIVE_VALUE_PATTERNS
        ):
            raise LiveTraceCaptureFailed(
                "Sensitive value survived event sanitization"
            )

    visit(events)


def render_markdown(metadata: dict[str, Any]) -> str:
    counts = metadata["event_counts"]
    chronology = metadata["flow_chronology"]
    rows = [
        (
            f"- `{item['captured_at']}` seq `{item['sequence']}`: "
            f"pipe `{item['pipe']}`, `{item['operation']}` "
            f"`{item['component'] or 'pipeline'}`, lane "
            f"`{item['lane'] or 'n/a'}`, stack "
            f"`{' → '.join(map(str, item['components'])) or 'n/a'}`"
        )
        for item in chronology
    ]
    return "\n".join(
        [
            "# RocketRide live Cloud traces",
            "",
            "**LIVE CLOUD CAPTURE**",
            "",
            f"- Captured: `{metadata['captured_at']}`",
            f"- Project ID: `{PROJECT_ID}`",
            f"- Source: `{SOURCE}`",
            f"- Diagnosis trace: `{metadata['diagnosis_trace_id']}`",
            f"- Remediation trace: `{metadata['remediation_trace_id']}`",
            (
                "- Event counts: "
                + ", ".join(f"`{name}` {counts[name]}" for name in MONITOR_TYPES)
            ),
            f"- Existing target pipeline terminated: `{metadata['terminated_existing']}`",
            f"- Pipeline remained running after capture: `{metadata['pipeline_running']}`",
            "",
            "## Actual component/flow chronology",
            "",
            *(rows or ["- No flow events captured."]),
            "",
            (
                "The JSONL companion contains the sanitized actual SDK events. "
                "Sensitive fields and endpoint-shaped values were removed or redacted."
            ),
            "",
        ]
    )


def render_html(metadata: dict[str, Any]) -> str:
    counts = metadata["event_counts"]
    chronology = metadata["flow_chronology"]
    cards = "".join(
        f"<div class='count'><strong>{counts[name]}</strong><span>{html.escape(name)}</span></div>"
        for name in MONITOR_TYPES
    )
    rows = "".join(
        "<tr>"
        f"<td>{html.escape(str(item['captured_at']))}</td>"
        f"<td>{html.escape(str(item['sequence']))}</td>"
        f"<td>{html.escape(str(item['pipe']))}</td>"
        f"<td>{html.escape(str(item['operation']))}</td>"
        f"<td>{html.escape(str(item['component'] or 'pipeline'))}</td>"
        f"<td>{html.escape(str(item['lane'] or 'n/a'))}</td>"
        f"<td>{html.escape(' → '.join(map(str, item['components'])) or 'n/a')}</td>"
        "</tr>"
        for item in chronology
    )
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>RocketRide Live Cloud Capture</title>
<style>
  :root {{ color-scheme: dark; font-family: Inter, ui-sans-serif, system-ui, sans-serif; }}
  body {{ margin: 0; background: #07111f; color: #e6edf7; }}
  main {{ max-width: 1180px; margin: 0 auto; padding: 42px; }}
  .label {{ display: inline-block; padding: 9px 15px; border-radius: 999px;
    background: #15d19a; color: #03231a; font-weight: 900; letter-spacing: .08em; }}
  h1 {{ margin: 20px 0 8px; font-size: 38px; }}
  .sub {{ color: #91a4bd; margin-bottom: 28px; }}
  .meta, .counts {{ display: grid; gap: 12px; }}
  .meta {{ grid-template-columns: repeat(2, minmax(0, 1fr)); margin-bottom: 22px; }}
  .meta div, .count {{ background: #0d1b2d; border: 1px solid #223553; border-radius: 12px; padding: 15px; }}
  .meta span, .count span {{ display: block; color: #91a4bd; font-size: 12px; text-transform: uppercase; }}
  .meta code {{ color: #c8d8ec; overflow-wrap: anywhere; }}
  .counts {{ grid-template-columns: repeat(5, 1fr); margin: 22px 0 30px; }}
  .count strong {{ display: block; font-size: 32px; color: #64e6bc; }}
  table {{ width: 100%; border-collapse: collapse; background: #0d1b2d; border-radius: 12px; overflow: hidden; }}
  th, td {{ text-align: left; padding: 11px 12px; border-bottom: 1px solid #223553; font-size: 13px; }}
  th {{ color: #91a4bd; text-transform: uppercase; font-size: 11px; }}
  .ok {{ color: #64e6bc; font-weight: 800; }}
</style>
</head>
<body><main>
<div class="label">LIVE CLOUD CAPTURE</div>
<h1>RocketRide observability proof</h1>
<div class="sub">Sanitized events captured through a token-scoped SDK monitor.</div>
<section class="meta">
  <div><span>Captured</span><code>{html.escape(metadata['captured_at'])}</code></div>
  <div><span>Project / source</span><code>{PROJECT_ID} / {SOURCE}</code></div>
  <div><span>Diagnosis trace</span><code>{html.escape(metadata['diagnosis_trace_id'])}</code></div>
  <div><span>Remediation trace</span><code>{html.escape(metadata['remediation_trace_id'])}</code></div>
  <div><span>Pipeline state</span><span class="ok">{'RUNNING' if metadata['pipeline_running'] else 'NOT RUNNING'}</span></div>
  <div><span>Trace level</span><span class="ok">FULL</span></div>
</section>
<section class="counts">{cards}</section>
<h2>Actual component / flow chronology</h2>
<table><thead><tr><th>Captured</th><th>Seq</th><th>Pipe</th><th>Op</th>
<th>Component</th><th>Lane</th><th>Stack</th></tr></thead><tbody>{rows}</tbody></table>
</main></body></html>
"""


def write_evidence(events: list[dict[str, Any]], metadata: dict[str, Any]) -> None:
    JSONL_PATH.parent.mkdir(parents=True, exist_ok=True)
    JSONL_PATH.write_text(
        "".join(json.dumps(event, sort_keys=True) + "\n" for event in events),
        encoding="utf-8",
    )
    MARKDOWN_PATH.write_text(render_markdown(metadata), encoding="utf-8")
    HTML_PATH.write_text(render_html(metadata), encoding="utf-8")


async def capture() -> dict[str, Any]:
    terminated_existing = await terminate_existing_pipeline()
    captured_events: list[dict[str, Any]] = []
    event_notification = asyncio.Event()
    diagnosis_trace_id = f"trace_live_diagnosis_{uuid4().hex[:12]}"
    remediation_trace_id = f"trace_live_remediation_{uuid4().hex[:12]}"
    running_fingerprint = ""

    async with RunbookInference(str(PIPELINE_PATH)) as inference:
        if inference.token is None:
            raise LiveTraceCaptureFailed("RunbookInference returned no task token")
        raw_token = inference.token
        running_fingerprint = fingerprint(raw_token)

        async def on_event(event: dict[str, Any]) -> None:
            sanitized = sanitize(event, raw_token)
            if isinstance(sanitized, dict):
                sanitized["captured_at"] = utc_now()
                captured_events.append(sanitized)
                event_notification.set()

        monitor = RocketRideClient(on_event=on_event)
        await monitor.connect()
        monitor_key = {"token": raw_token}
        try:
            await monitor.add_monitor(monitor_key, list(MONITOR_TYPES))
            await asyncio.sleep(1.0)
            await inference.invoke(diagnostic_envelope(diagnosis_trace_id))
            await inference.invoke(remediation_envelope(remediation_trace_id))
            await wait_for_required_events(captured_events, event_notification)
        finally:
            await monitor.remove_monitor(monitor_key, list(MONITOR_TYPES))
            await monitor.disconnect()

        validate_persisted_content(captured_events, raw_token)

    status_client = RocketRideClient()
    await status_client.connect()
    try:
        active_token = await status_client.get_task_token(PROJECT_ID, SOURCE)
        pipeline_running = bool(
            active_token
            and fingerprint(active_token) == running_fingerprint
        )
    finally:
        await status_client.disconnect()

    counts = count_events(captured_events)
    if not all(counts[event_type] > 0 for event_type in REQUIRED_EVENT_TYPES):
        raise LiveTraceCaptureFailed("Required live event evidence was not captured")
    if not pipeline_running:
        raise LiveTraceCaptureFailed("Full-trace pipeline was not running after capture")

    metadata = {
        "captured_at": utc_now(),
        "project_id": PROJECT_ID,
        "source": SOURCE,
        "diagnosis_trace_id": diagnosis_trace_id,
        "remediation_trace_id": remediation_trace_id,
        "event_counts": counts,
        "flow_chronology": flow_chronology(captured_events),
        "terminated_existing": terminated_existing,
        "pipeline_running": pipeline_running,
    }
    write_evidence(captured_events, metadata)
    return metadata


async def async_main() -> None:
    metadata = await capture()
    print("RocketRide live Cloud capture passed.")
    print("Event counts: " + json.dumps(metadata["event_counts"], sort_keys=True))
    print(f"JSONL proof: {JSONL_PATH.relative_to(PROJECT_ROOT)}")
    print(f"Markdown proof: {MARKDOWN_PATH.relative_to(PROJECT_ROOT)}")
    print(f"HTML proof: {HTML_PATH.relative_to(PROJECT_ROOT)}")
    print(f"Pipeline remains running: {metadata['pipeline_running']}")


def main() -> int:
    try:
        asyncio.run(async_main())
    except LiveTraceCaptureFailed as exc:
        print(f"RocketRide live Cloud capture failed: {exc}")
        return 1
    except Exception as exc:  # noqa: BLE001 - avoid printing potentially sensitive data.
        print(f"RocketRide live Cloud capture failed ({type(exc).__name__}).")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
