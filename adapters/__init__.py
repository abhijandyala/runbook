"""Safe connector boundaries for the additive Complaint Bridge."""

from adapters.config import connector_dry_run, connector_status
from adapters.evidence import assemble_bridge_report
from adapters.mapping import complaint_alert_id, complaint_to_alert, redact_complaint
from adapters.slack import SlackReadError, fetch_slack_messages

__all__ = [
    "SlackReadError",
    "assemble_bridge_report",
    "complaint_alert_id",
    "complaint_to_alert",
    "connector_dry_run",
    "connector_status",
    "fetch_slack_messages",
    "redact_complaint",
]
