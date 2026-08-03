"""Safe connector boundaries for the additive Complaint Bridge."""

from adapters.config import (
    connector_dry_run,
    connector_live_writes,
    connector_status,
    live_writes_eligible,
)
from adapters.evidence import assemble_bridge_report
from adapters.mapping import complaint_alert_id, complaint_to_alert, redact_complaint
from adapters.slack import SlackReadError, fetch_slack_messages
from adapters.writes import execute_connector_writes

__all__ = [
    "SlackReadError",
    "assemble_bridge_report",
    "complaint_alert_id",
    "complaint_to_alert",
    "connector_dry_run",
    "connector_live_writes",
    "connector_status",
    "execute_connector_writes",
    "fetch_slack_messages",
    "live_writes_eligible",
    "redact_complaint",
]
