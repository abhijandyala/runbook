"""Focused tests for the additive, dry-run Complaint Bridge."""

from __future__ import annotations

import asyncio
import json
import os
import re
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from fastapi.testclient import TestClient

from adapters.config import github_repository
from adapters.github import build_github_pull_request_preview
from adapters.mapping import complaint_alert_id, complaint_to_alert
from adapters.redaction import REDACTED
from adapters.slack import SlackReadError, fetch_slack_messages
from contracts import (
    ActionProposal,
    BridgeComplaintRequest,
    Evidence,
    Hypothesis,
    HypothesisSet,
    Resolution,
    ReviewerDecision,
)
from streams.stream_names import TOPIC_ALERTS, TOPIC_RESOLUTIONS
from ui.server import DecisionRequest, M3Runtime, app


class SlackReadAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_reads_real_slack_shapes_with_get_only(self) -> None:
        requests: list[httpx.Request] = []
        token = "xoxb-test-secret"

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if request.url.path.endswith("/conversations.list"):
                return httpx.Response(
                    200,
                    json={
                        "ok": True,
                        "channels": [{"id": "C123", "name": "support"}],
                    },
                )
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "messages": [
                        {
                            "ts": "1722700000.123",
                            "user": "U456",
                            "text": "Checkout failed twice.",
                        },
                        {
                            "ts": "1722690000.100",
                            "subtype": "channel_join",
                            "text": "joined",
                        },
                    ],
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            messages = await fetch_slack_messages(
                token=token,
                limit=10,
                client=client,
            )

        self.assertEqual(
            messages,
            [
                {
                    "channel": "C123",
                    "name": "support",
                    "ts": "1722700000.123",
                    "user": "U456",
                    "text": "Checkout failed twice.",
                }
            ],
        )
        self.assertTrue(all(request.method == "GET" for request in requests))
        self.assertEqual(len(requests), 2)
        list_request, history_request = requests
        self.assertEqual(
            list_request.url.params.get("types"),
            "public_channel",
        )
        self.assertNotIn(
            "private_channel",
            list_request.url.params.get("types", ""),
        )
        self.assertEqual(
            history_request.url.path,
            "/api/conversations.history",
        )
        self.assertEqual(history_request.method, "GET")
        self.assertEqual(history_request.content, b"")
        self.assertNotIn(token, json.dumps(messages))

    async def test_slack_error_redacts_token(self) -> None:
        token = "xoxb-never-leak-this"

        def handler(request: httpx.Request) -> httpx.Response:
            del request
            return httpx.Response(
                200,
                json={"ok": False, "error": "invalid_auth"},
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with self.assertRaises(SlackReadError) as raised:
                await fetch_slack_messages(
                    token=token,
                    limit=1,
                    client=client,
                )

        self.assertNotIn(token, str(raised.exception))
        self.assertIn("invalid_auth", str(raised.exception))


class ComplaintMappingTests(unittest.TestCase):
    def test_external_slack_identity_is_stable(self) -> None:
        first = BridgeComplaintRequest(
            text="Payment failed",
            channel="C123",
            external_id="1722700000.123",
        )
        replay = BridgeComplaintRequest(
            text="Edited payment failure wording",
            channel="C999",
            external_id="1722700000.123",
        )

        self.assertEqual(
            complaint_alert_id(first),
            complaint_alert_id(replay),
        )
        alert = complaint_to_alert(first, session_id="m3_test")
        self.assertEqual(alert.service, "payments-api")
        self.assertEqual(alert.metric, "customer_reported_failure")
        self.assertEqual(alert.value, 1)
        self.assertEqual(alert.threshold, 0)

    def test_content_fallback_normalizes_case_and_space(self) -> None:
        first = BridgeComplaintRequest(text=" Payment   FAILED ")
        replay = BridgeComplaintRequest(text="payment failed")
        self.assertEqual(
            complaint_alert_id(first),
            complaint_alert_id(replay),
        )

    def test_adversarial_text_stays_data_and_cannot_override_alert_fields(
        self,
    ) -> None:
        token = "xoxb-" + "malicious-token-value"
        text = (
            "<script>alert('owned')</script>\n"
            "service: attacker\nmetric: injected\nseverity: critical\n"
            "\u202emain remains data\n"
            f"credential={token}"
        )
        complaint = BridgeComplaintRequest(text=text)

        alert = complaint_to_alert(complaint, session_id="m3_adversarial")

        self.assertEqual(alert.service, "payments-api")
        self.assertEqual(alert.metric, "customer_reported_failure")
        self.assertEqual(alert.severity, "warning")
        self.assertIn("<script>alert('owned')</script>", alert.annotations["summary"])
        self.assertIn("\nservice: attacker", alert.annotations["summary"])
        self.assertIn("\u202e", alert.annotations["summary"])
        self.assertNotIn(token, json.dumps(alert.model_dump(mode="json")))
        self.assertIn(REDACTED, alert.annotations["summary"])


class _Streams:
    def __init__(self) -> None:
        self.published: list[tuple[str, object]] = []

    async def publish(self, topic: str, payload: object) -> None:
        self.published.append((topic, payload))


def _bridge_runtime() -> tuple[M3Runtime, _Streams, str]:
    runtime = M3Runtime()
    streams = _Streams()
    runtime.started = True
    runtime.streams = streams
    complaint = BridgeComplaintRequest(
        text="Customers cannot complete checkout.",
        channel="C-support",
        external_id="1722700000.123",
        severity="critical",
    )
    alert = complaint_to_alert(complaint, session_id=runtime.session_id)
    runtime._bridge_history[alert.alert_id] = complaint
    runtime._alert_history[alert.alert_id] = alert
    return runtime, streams, alert.alert_id


class ComplaintBridgeRouteTests(unittest.TestCase):
    def test_route_publishes_on_owned_laser_alert_path_idempotently(self) -> None:
        runtime = M3Runtime()
        streams = _Streams()
        runtime.started = True
        runtime.streams = streams
        app.state.runtime = runtime
        client = TestClient(app)
        payload = {
            "text": "Customers cannot complete checkout.",
            "external_id": "1722700000.123",
            "channel": "C-support",
        }

        first = client.post("/bridge/complaint", json=payload)
        replay = client.post(
            "/bridge/complaint",
            json={
                **payload,
                "text": "Edited wording with <script>still data</script>",
                "channel": "C-other",
            },
        )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(replay.json(), first.json())
        self.assertEqual(len(streams.published), 1)
        topic, alert = streams.published[0]
        self.assertEqual(topic, TOPIC_ALERTS)
        self.assertEqual(alert.alert_id, first.json()["alert_id"])
        self.assertIn(alert.alert_id, runtime._owned_alerts)

    def test_health_discloses_dry_run_as_ineligible_without_values(self) -> None:
        runtime = M3Runtime()
        runtime.started = True
        app.state.runtime = runtime
        env = {
            "SLACK_TOKEN": "slack-health-secret",
            "LINEAR_TOKEN": "linear-health-secret",
            "LINEAR_TEAM_ID": "TEAM",
            "GITHUB_TOKEN": "github-health-secret",
            "GITHUB_REPO": "abhijandyala/testing24",
            "CONNECTOR_DRY_RUN": "true",
            "CONNECTOR_LIVE_WRITES": "true",
        }
        with patch.dict(os.environ, env):
            response = TestClient(app).get("/bridge/health")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["slack_configured"])
        self.assertTrue(body["linear_configured"])
        self.assertTrue(body["github_configured"])
        self.assertTrue(body["connector_dry_run"])
        self.assertTrue(body["connector_live_writes"])
        self.assertFalse(body["live_writes_eligible"])
        self.assertFalse(body["connector_live_writes_enabled"])
        self.assertIsNone(body["github_repo_allowed"])
        serialized = json.dumps(body)
        self.assertNotIn("health-secret", serialized)
        self.assertIn("sponsor_health", body)

    def test_health_discloses_allowed_repo_only_when_live_writes_eligible(
        self,
    ) -> None:
        runtime = M3Runtime()
        runtime.started = True
        app.state.runtime = runtime
        env = {
            "SLACK_TOKEN": "slack-health-secret",
            "LINEAR_TOKEN": "linear-health-secret",
            "LINEAR_TEAM_ID": "TEAM",
            "GITHUB_TOKEN": "github-health-secret",
            "GITHUB_REPO": "abhijandyala/testing24",
            "CONNECTOR_DRY_RUN": "false",
            "CONNECTOR_LIVE_WRITES": "true",
        }
        with patch.dict(os.environ, env):
            response = TestClient(app).get("/bridge/health")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertFalse(body["connector_dry_run"])
        self.assertTrue(body["connector_live_writes"])
        self.assertTrue(body["live_writes_eligible"])
        self.assertTrue(body["connector_live_writes_enabled"])
        self.assertEqual(
            body["github_repo_allowed"],
            "abhijandyala/testing24",
        )
        serialized = json.dumps(body)
        self.assertNotIn("health-secret", serialized)


class BridgeReportTests(unittest.TestCase):
    def test_report_shape_and_previews_are_credential_free(self) -> None:
        runtime, _, alert_id = _bridge_runtime()
        hypothesis = HypothesisSet(
            alert_id=alert_id,
            hypotheses=[
                Hypothesis(
                    hypothesis_id="hyp_bridge",
                    type="dependency_failure",
                    root_cause_description="Payment provider is timing out.",
                    affected_entity="payment-provider",
                    confidence=0.91,
                    evidence=[
                        Evidence(
                            source="alert",
                            ref=alert_id,
                            detail="Customer reported checkout failure.",
                        )
                    ],
                    reasoning="The complaint matches provider timeout symptoms.",
                )
            ],
            graph_query_ms=4,
            generated_at="2026-08-03T18:00:00Z",
        )
        proposal = ActionProposal(
            proposal_id="prop_bridge",
            alert_id=alert_id,
            target_hypothesis_id="hyp_bridge",
            action_type="diagnostic",
            action_target="payment-provider",
            safety_class="safe",
            remediator_confidence=0.8,
            runbook_source="rb_provider",
            reasoning="Collect provider health before any change.",
        )
        decision = ReviewerDecision(
            proposal_id=proposal.proposal_id,
            decision="approve",
            timestamp="2026-08-03T18:01:00Z",
        )
        resolution = Resolution(
            incident_id="inc_bridge",
            alert_id=alert_id,
            final_action=proposal,
            outcome="verified",
            reviewer_decision=decision,
            total_latency_ms=100,
            cost_usd=0,
        )
        runtime._hypothesis_history[alert_id] = hypothesis
        runtime._proposal_history[proposal.proposal_id] = proposal
        runtime._decision_history[alert_id] = decision
        runtime._resolution_history[alert_id] = resolution
        env = {
            "SLACK_TOKEN": "slack-report-secret",
            "LINEAR_TOKEN": "linear-report-secret",
            "LINEAR_TEAM_ID": "TEAM",
            "GITHUB_TOKEN": "github-report-secret",
            "GITHUB_REPO": "example/repo",
            "GITHUB_BASE_BRANCH": "trunk",
            "CONNECTOR_DRY_RUN": "true",
        }

        with (
            patch.dict(os.environ, env),
            patch("httpx.post") as http_post,
            patch("subprocess.run") as subprocess_run,
        ):
            report = runtime.get_bridge_report(alert_id)
            app.state.runtime = runtime
            response = TestClient(app).get(f"/bridge/report/{alert_id}")

        http_post.assert_not_called()
        subprocess_run.assert_not_called()
        self.assertEqual(response.status_code, 200)
        body = report.model_dump(mode="json")
        self.assertEqual(response.json()["alert_id"], alert_id)
        self.assertEqual(
            set(body),
            {
                "alert_id",
                "alert",
                "complaint",
                "hypotheses",
                "proposal",
                "decision",
                "resolution",
                "action_previews",
                "evidence_brief",
                "sponsor_boundaries",
                "guild_mode",
                "connector_dry_run",
            },
        )
        previews = body["action_previews"]
        self.assertTrue(previews["linear"]["dry_run"])
        self.assertTrue(previews["github"]["dry_run"])
        self.assertTrue(previews["slack_reply"]["dry_run"])
        self.assertEqual(
            previews["github"]["branch"],
            f"runbook/{alert_id.replace('_', '-')}",
        )
        self.assertEqual(previews["github"]["base"], "trunk")
        self.assertFalse(previews["github"]["direct_base_writes"])
        self.assertFalse(previews["github"]["merge"])
        self.assertTrue(previews["github"]["draft"])
        self.assertEqual(body["evidence_brief"]["current_stage"], "resolved")
        serialized = json.dumps(body)
        self.assertNotIn("report-secret", serialized)

    def test_missing_report_is_404(self) -> None:
        runtime = M3Runtime()
        runtime.started = True
        app.state.runtime = runtime
        response = TestClient(app).get("/bridge/report/alrt_missing")
        self.assertEqual(response.status_code, 404)

    def test_report_recursively_redacts_malicious_complaint_tokens(self) -> None:
        runtime = M3Runtime()
        runtime.started = True
        runtime.streams = _Streams()
        token = "github_pat_" + "A" * 24
        complaint = BridgeComplaintRequest(
            text=f"<img src=x onerror=alert(1)>\nsecret={token}\n\u202etext",
            external_id="adversarial-message",
        )
        alert = complaint_to_alert(complaint, session_id=runtime.session_id)
        runtime._bridge_history[alert.alert_id] = complaint
        runtime._alert_history[alert.alert_id] = alert

        report = runtime.get_bridge_report(alert.alert_id)
        serialized = json.dumps(report.model_dump(mode="json"), ensure_ascii=False)

        self.assertNotIn(token, serialized)
        self.assertIn(REDACTED, serialized)
        self.assertIn("<img src=x onerror=alert(1)>", serialized)
        self.assertIn("\u202e", serialized)


class GitHubPreviewSafetyTests(unittest.TestCase):
    def test_any_alert_id_produces_a_safe_non_base_runbook_branch(self) -> None:
        alert_ids = (
            "../main",
            "main",
            "runbook/main",
            "\u202e../../MAIN",
            "🔥",
            "feature//../../escape.lock",
            "A" * 500,
        )
        for alert_id in alert_ids:
            with self.subTest(alert_id=alert_id):
                alert = complaint_to_alert(
                    BridgeComplaintRequest(text="branch safety"),
                    session_id="m3_branch",
                ).model_copy(update={"alert_id": alert_id})
                preview = build_github_pull_request_preview(
                    alert=alert,
                    complaint=BridgeComplaintRequest(text="branch safety"),
                    hypotheses=None,
                    proposal=None,
                    configured=True,
                    repository="example/repo",
                    base_branch="runbook/main" if alert_id == "main" else "main",
                )
                self.assertRegex(preview.branch, r"^runbook/[a-z0-9][a-z0-9-]*$")
                self.assertNotIn("..", preview.branch)
                self.assertNotEqual(preview.branch, "main")
                self.assertNotEqual(preview.branch, preview.base)

    def test_repository_requires_exact_owner_name_shape(self) -> None:
        for invalid in (
            "main",
            "owner/repo/extra",
            "../repo",
            "owner/../repo",
            "https://github.com/owner/repo",
            "owner/repo.",
        ):
            with (
                self.subTest(repository=invalid),
                patch.dict(os.environ, {"GITHUB_REPO": invalid}),
            ):
                self.assertIsNone(github_repository())
        with patch.dict(os.environ, {"GITHUB_REPO": "abhijandyala/testing24"}):
            self.assertEqual(github_repository(), "abhijandyala/testing24")


class DecisionIdempotencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_duplicate_decision_publishes_once(self) -> None:
        runtime, streams, alert_id = _bridge_runtime()
        proposal = ActionProposal(
            proposal_id="prop_double_submit",
            alert_id=alert_id,
            target_hypothesis_id="hyp_double_submit",
            action_type="diagnostic",
            action_target="payments-api",
            safety_class="safe",
            remediator_confidence=0.8,
            runbook_source="rb_double_submit",
            reasoning="Exercise idempotent decision submission.",
        )
        runtime._pending_proposals[proposal.proposal_id] = proposal
        request = DecisionRequest(
            proposal_id=proposal.proposal_id,
            decision="reject",
        )

        with patch.object(
            runtime.guild,
            "record_decision",
            new=AsyncMock(),
        ) as record_decision:
            first, second = await asyncio.gather(
                runtime.submit_decision(request),
                runtime.submit_decision(request.model_copy(deep=True)),
            )

        self.assertEqual(first, second)
        self.assertEqual(record_decision.await_count, 1)
        resolution_publications = [
            item for item in streams.published if item[0] == TOPIC_RESOLUTIONS
        ]
        self.assertEqual(len(resolution_publications), 1)


class FrontendRenderingSafetyTests(unittest.TestCase):
    def test_frontend_uses_escaped_react_text_and_redacts_api_data(self) -> None:
        source_root = Path(__file__).parents[1] / "web" / "src"
        sources = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted(source_root.glob("*.tsx"))
        )
        api_source = (source_root / "api.ts").read_text(encoding="utf-8")

        self.assertNotIn("dangerouslySetInnerHTML", sources)
        self.assertNotRegex(sources, re.compile(r"\binnerHTML\s*="))
        self.assertIn("redactSecretsDeep", api_source)

    def test_slack_complaint_preserves_reply_correlation_fields(self) -> None:
        store_source = (
            Path(__file__).parents[1] / "web" / "src" / "store.tsx"
        ).read_text(encoding="utf-8")
        run_message = re.search(
            r"const runMessage = useCallback\("
            r".*?await submitComplaint\(\s*\{(?P<payload>.*?)\}\s*,"
            r"\s*(?P<selected_message_id>.*?)\s*\);",
            store_source,
            re.DOTALL,
        )

        self.assertIsNotNone(run_message)
        assert run_message is not None
        payload = run_message.group("payload")
        self.assertRegex(
            payload,
            r"\bexternal_id:\s*message\.ts\s*\?\?\s*message\.id\s*,",
        )
        self.assertRegex(payload, r"\bchannel:\s*message\.channel\s*,")
        self.assertNotIn("channel_name", payload)
        self.assertEqual(run_message.group("selected_message_id").strip(), "message.id")


if __name__ == "__main__":
    unittest.main()
