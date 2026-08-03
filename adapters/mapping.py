"""Map customer complaints onto the existing runbook Alert contract."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from adapters.redaction import redact_data
from contracts import Alert, BridgeComplaintRequest


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def complaint_alert_id(complaint: BridgeComplaintRequest) -> str:
    """Derive a retry-stable ID from Slack identity or normalized content."""

    external_id = (complaint.external_id or "").strip()
    if external_id:
        identity = f"slack\x1f{external_id}"
    else:
        identity = "\x1f".join(
            (
                "complaint-bridge",
                (complaint.channel or "").strip().lower(),
                complaint.service.strip().lower(),
                " ".join(complaint.text.split()).lower(),
            )
        )
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]
    return f"alrt_bridge_{digest}"


def complaint_to_alert(
    complaint: BridgeComplaintRequest,
    *,
    session_id: str,
    fired_at: str | None = None,
) -> Alert:
    safe_complaint = redact_complaint(complaint)
    annotations = {
        "summary": safe_complaint.text.strip(),
        "complaint_text": safe_complaint.text.strip(),
        "m3_session_id": session_id,
        "bridge_source": "slack",
    }
    if safe_complaint.external_id and safe_complaint.external_id.strip():
        annotations["external_id"] = safe_complaint.external_id.strip()
    if safe_complaint.channel and safe_complaint.channel.strip():
        annotations["channel"] = safe_complaint.channel.strip()

    return Alert(
        alert_id=complaint_alert_id(complaint),
        fired_at=fired_at or _iso_now(),
        severity=safe_complaint.severity,
        service=safe_complaint.service.strip() or "payments-api",
        metric="customer_reported_failure",
        value=1,
        threshold=0,
        labels={
            "source": "complaint-bridge",
            "channel": (safe_complaint.channel or "unknown").strip() or "unknown",
        },
        annotations=annotations,
    )


def redact_complaint(
    complaint: BridgeComplaintRequest,
) -> BridgeComplaintRequest:
    """Return a credential-free copy while preserving complaint structure."""

    return BridgeComplaintRequest.model_validate(
        redact_data(complaint.model_dump(mode="python"))
    )
