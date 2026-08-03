"""Canonical LaserData stream and topic names."""

import os

STREAM_SENTRY = os.getenv("LASER_STREAM", "sentry")

TOPIC_ALERTS = "alerts"
TOPIC_HYPOTHESES = "hypotheses"
TOPIC_PROPOSALS = "proposals"
TOPIC_RESOLUTIONS = "resolutions"

ALL_TOPICS = (
    TOPIC_ALERTS,
    TOPIC_HYPOTHESES,
    TOPIC_PROPOSALS,
    TOPIC_RESOLUTIONS,
)
