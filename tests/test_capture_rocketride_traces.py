"""Focused tests for live RocketRide event sanitization and proof rendering."""

from __future__ import annotations

import unittest

from scripts.capture_rocketride_traces import (
    count_events,
    flow_chronology,
    render_html,
    sanitize,
    validate_persisted_content,
)


class RocketRideLiveCaptureTests(unittest.TestCase):
    def test_sanitize_recursively_drops_sensitive_keys_and_values(self) -> None:
        raw_token = "raw-task-token-fixture"
        event = {
            "event": "apaevt_flow",
            "body": {
                "taskToken": raw_token,
                "credentials": {"password": "bad"},
                "nested": [
                    {"api_key": "bad"},
                    {"server_host": "cloud.example"},
                    {"safe": f"prefix-{raw_token}-suffix"},
                    {"link": "https://example.invalid/private"},
                ],
            },
        }

        sanitized = sanitize(event, raw_token)

        self.assertEqual(
            sanitized,
            {
                "event": "apaevt_flow",
                "body": {
                    "nested": [
                        {},
                        {},
                        {"safe": "[REDACTED]"},
                        {"link": "[REDACTED]"},
                    ]
                },
            },
        )
        validate_persisted_content([sanitized], raw_token)

    def test_event_counts_and_chronology_use_actual_flow_event(self) -> None:
        events = [
            {"event": "apaevt_task", "body": {"action": "running"}},
            {"event": "apaevt_status_update", "body": {"state": 3}},
            {
                "event": "apaevt_flow",
                "seq": 17,
                "captured_at": "2026-08-03T18:00:00Z",
                "body": {
                    "id": 4,
                    "op": "enter",
                    "component": "agent_rocketride_1",
                    "pipes": ["chat_1", "agent_rocketride_1"],
                    "trace": {"lane": "questions"},
                },
            },
        ]

        self.assertEqual(
            count_events(events),
            {"task": 1, "summary": 1, "flow": 1, "output": 0, "sse": 0},
        )
        self.assertEqual(
            flow_chronology(events)[0]["component"],
            "agent_rocketride_1",
        )

    def test_html_is_self_contained_and_labels_live_capture(self) -> None:
        metadata = {
            "captured_at": "2026-08-03T18:00:00Z",
            "diagnosis_trace_id": "trace_diagnosis",
            "remediation_trace_id": "trace_remediation",
            "event_counts": {
                "task": 1,
                "summary": 1,
                "flow": 1,
                "output": 0,
                "sse": 0,
            },
            "flow_chronology": [],
            "pipeline_running": True,
        }

        rendered = render_html(metadata)

        self.assertIn("LIVE CLOUD CAPTURE", rendered)
        self.assertIn("trace_diagnosis", rendered)
        self.assertNotIn("<script", rendered)
        self.assertNotIn("http://", rendered)
        self.assertNotIn("https://", rendered)


if __name__ == "__main__":
    unittest.main()
