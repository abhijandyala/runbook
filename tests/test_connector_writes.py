"""Mock-only coverage for opt-in Complaint Bridge connector writes."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from adapters.evidence import assemble_bridge_report
from adapters.github import (
    BUGGY_PRICE_LINE,
    CORRECT_PRICE_LINE,
    GitHubWriteError,
    create_github_draft_pull_request,
    patch_app_js,
)
from adapters.mapping import complaint_to_alert
from adapters.writes import execute_connector_writes
from contracts import (
    ActionProposal,
    BridgeComplaintRequest,
    ConnectorWriteResult,
)
from ui.server import DecisionRequest, M3Runtime

LIVE_ENV = {
    "CONNECTOR_DRY_RUN": "false",
    "CONNECTOR_LIVE_WRITES": "true",
    "LINEAR_TOKEN": "lin_api_mocktoken123",
    "LINEAR_TEAM_ID": "team_mock",
    "GITHUB_TOKEN": "github_pat_mocktoken123456",
    "GITHUB_REPO": "abhijandyala/testing24",
    "GITHUB_BASE_BRANCH": "main",
    "SLACK_TOKEN": "xoxb-mock-token-123456",
}


class _Streams:
    def __init__(self) -> None:
        self.published: list[tuple[str, object]] = []

    async def publish(self, topic: str, payload: object) -> None:
        self.published.append((topic, payload))


def _fixture(
    *, correlation: bool = True
) -> tuple[
    M3Runtime,
    BridgeComplaintRequest,
    ActionProposal,
]:
    runtime = M3Runtime()
    runtime.started = True
    runtime.streams = _Streams()
    complaint = BridgeComplaintRequest(
        text="Yearly checkout displays the monthly price.",
        channel="C-support" if correlation else None,
        external_id="1722700000.123" if correlation else None,
    )
    alert = complaint_to_alert(complaint, session_id=runtime.session_id)
    proposal = ActionProposal(
        proposal_id="prop_connector_write",
        alert_id=alert.alert_id,
        target_hypothesis_id="hyp_pricing",
        action_type="diagnostic",
        action_target="pricing-ui",
        safety_class="safe",
        remediator_confidence=0.95,
        runbook_source="pricing-runbook",
        reasoning="Restore the intended yearly/monthly dataset selection.",
    )
    runtime._bridge_history[alert.alert_id] = complaint
    runtime._alert_history[alert.alert_id] = alert
    runtime._proposal_history[proposal.proposal_id] = proposal
    runtime._pending_proposals[proposal.proposal_id] = proposal
    return runtime, complaint, proposal


def _previews(
    runtime: M3Runtime,
    complaint: BridgeComplaintRequest,
    proposal: ActionProposal,
):
    return assemble_bridge_report(
        alert=runtime._alert_history[proposal.alert_id],
        complaint=complaint,
        hypotheses=None,
        proposal=proposal,
        decision=None,
        resolution=None,
        guild_mode="test",
    ).action_previews


def _success_handler(
    requests: list[httpx.Request],
    *,
    source: str | None = None,
):
    app_source = source or f"before\n{BUGGY_PRICE_LINE}\nafter\n"

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.host == "api.linear.app":
            return httpx.Response(
                200,
                json={
                    "data": {
                        "issueCreate": {
                            "success": True,
                            "issue": {
                                "id": "issue-id",
                                "identifier": "ENG-42",
                                "url": "https://linear.app/acme/issue/ENG-42",
                            },
                        }
                    }
                },
            )
        if request.url.path.endswith("/git/ref/heads/main"):
            return httpx.Response(200, json={"object": {"sha": "base-sha"}})
        if request.url.path.endswith("/git/refs"):
            return httpx.Response(201, json={"ref": "refs/heads/runbook/test"})
        if request.method == "GET" and request.url.path.endswith("/contents/app.js"):
            return httpx.Response(
                200,
                json={
                    "sha": "file-sha",
                    "content": base64.b64encode(app_source.encode()).decode(),
                },
            )
        if request.method == "PUT" and request.url.path.endswith("/contents/app.js"):
            return httpx.Response(200, json={"content": {"sha": "new-sha"}})
        if request.url.path.endswith("/pulls"):
            return httpx.Response(
                201,
                json={
                    "id": 101,
                    "html_url": "https://github.com/abhijandyala/testing24/pull/1",
                },
            )
        if request.url.path.endswith("/chat.postMessage"):
            return httpx.Response(200, json={"ok": True, "ts": "1722700001.456"})
        return httpx.Response(500, json={"error": "unexpected mock request"})

    return handler


class ConnectorGateTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_request_and_environment_gates_are_required(self) -> None:
        cases = (
            ({"CONNECTOR_DRY_RUN": "true"}, "approve", True, "match"),
            ({"CONNECTOR_LIVE_WRITES": "false"}, "approve", True, "match"),
            ({}, "reject", True, "match"),
            ({}, "approve", False, "match"),
            ({}, "approve", True, "wrong"),
        )
        for env_override, decision, execute, confirmation in cases:
            with self.subTest(
                env=env_override,
                decision=decision,
                execute=execute,
                confirmation=confirmation,
            ):
                runtime, _, proposal = _fixture()
                request = DecisionRequest(
                    proposal_id=proposal.proposal_id,
                    decision=decision,
                    execute_live_writes=execute,
                    live_writes_confirmation=(
                        proposal.alert_id if confirmation == "match" else "wrong"
                    ),
                )
                env = {**LIVE_ENV, **env_override}
                with (
                    patch.dict(os.environ, env, clear=False),
                    patch.object(
                        runtime.guild,
                        "record_decision",
                        new=AsyncMock(),
                    ),
                    patch(
                        "ui.server.execute_connector_writes",
                        new=AsyncMock(),
                    ) as execute_mock,
                ):
                    await runtime.submit_decision(request)
                execute_mock.assert_not_awaited()

    async def test_exact_confirmation_enables_approved_live_write(self) -> None:
        runtime, _, proposal = _fixture()
        result = ConnectorWriteResult(
            proposal_id=proposal.proposal_id,
            alert_id=proposal.alert_id,
            status="succeeded",
            started_at="2026-08-03T00:00:00Z",
            completed_at="2026-08-03T00:00:01Z",
        )
        request = DecisionRequest(
            proposal_id=proposal.proposal_id,
            decision="approve",
            execute_live_writes=True,
            live_writes_confirmation=proposal.alert_id,
        )
        with (
            patch.dict(os.environ, LIVE_ENV, clear=False),
            patch.object(runtime.guild, "record_decision", new=AsyncMock()),
            patch(
                "ui.server.execute_connector_writes",
                new=AsyncMock(return_value=result),
            ) as execute_mock,
        ):
            await runtime.submit_decision(request)
        execute_mock.assert_awaited_once()
        self.assertEqual(
            runtime.get_bridge_report(proposal.alert_id).connector_writes,
            result,
        )

    async def test_concurrent_duplicate_decisions_write_once(self) -> None:
        runtime, _, proposal = _fixture()
        result = ConnectorWriteResult(
            proposal_id=proposal.proposal_id,
            alert_id=proposal.alert_id,
            status="succeeded",
            started_at="2026-08-03T00:00:00Z",
            completed_at="2026-08-03T00:00:01Z",
        )
        request = DecisionRequest(
            proposal_id=proposal.proposal_id,
            decision="approve",
            execute_live_writes=True,
            live_writes_confirmation=proposal.alert_id,
        )
        execute_mock = AsyncMock(return_value=result)
        with (
            patch.dict(os.environ, LIVE_ENV, clear=False),
            patch.object(runtime.guild, "record_decision", new=AsyncMock()),
            patch("ui.server.execute_connector_writes", new=execute_mock),
        ):
            first, second = await asyncio.gather(
                runtime.submit_decision(request),
                runtime.submit_decision(request.model_copy(deep=True)),
            )
        self.assertEqual(first, second)
        self.assertEqual(execute_mock.await_count, 1)


class ConnectorAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_order_patch_threading_and_safe_draft_pr(self) -> None:
        runtime, complaint, proposal = _fixture()
        requests: list[httpx.Request] = []
        transitions = []

        async def record_transition(action):
            transitions.append(action)

        transport = httpx.MockTransport(_success_handler(requests))
        async with httpx.AsyncClient(transport=transport) as client:
            with patch.dict(os.environ, LIVE_ENV, clear=False):
                result = await execute_connector_writes(
                    alert=runtime._alert_history[proposal.alert_id],
                    complaint=complaint,
                    proposal=proposal,
                    previews=_previews(runtime, complaint, proposal),
                    on_action=record_transition,
                    client=client,
                )

        self.assertEqual(result.status, "succeeded")
        self.assertEqual(
            [request.url.host for request in requests],
            [
                "api.linear.app",
                "api.github.com",
                "api.github.com",
                "api.github.com",
                "api.github.com",
                "api.github.com",
                "slack.com",
            ],
        )
        put_request = next(request for request in requests if request.method == "PUT")
        put_body = json.loads(put_request.content)
        patched = base64.b64decode(put_body["content"]).decode()
        self.assertNotIn(BUGGY_PRICE_LINE, patched)
        self.assertEqual(patched.count(CORRECT_PRICE_LINE), 1)
        self.assertRegex(put_body["branch"], r"^runbook/[a-z0-9][a-z0-9-]*$")
        self.assertNotEqual(put_body["branch"], "main")

        pull_request = next(
            request for request in requests if request.url.path.endswith("/pulls")
        )
        pull_body = json.loads(pull_request.content)
        self.assertTrue(pull_body["draft"])
        self.assertEqual(pull_body["base"], "main")
        self.assertIn(result.linear_issue_url, pull_body["body"])
        self.assertFalse(
            any(
                request.method == "DELETE"
                or "/merges" in request.url.path
                or request.url.path.endswith("/merge")
                for request in requests
            )
        )

        slack_request = requests[-1]
        slack_body = json.loads(slack_request.content)
        self.assertEqual(slack_body["channel"], complaint.channel)
        self.assertEqual(slack_body["thread_ts"], complaint.external_id)
        self.assertIn("complete", slack_body["text"])
        self.assertIn(result.linear_issue_url, slack_body["text"])
        self.assertIn(result.github_pull_request_url, slack_body["text"])
        self.assertEqual(
            [item.status for item in transitions],
            [
                "pending",
                "pending",
                "pending",
                "running",
                "succeeded",
                "running",
                "succeeded",
                "running",
                "succeeded",
            ],
        )

    async def test_absent_exact_bug_line_prevents_commit_pr_and_slack(self) -> None:
        runtime, complaint, proposal = _fixture()
        requests: list[httpx.Request] = []
        transport = httpx.MockTransport(
            _success_handler(requests, source="const price = dataset.yearly;\n")
        )
        async with httpx.AsyncClient(transport=transport) as client:
            with patch.dict(os.environ, LIVE_ENV, clear=False):
                result = await execute_connector_writes(
                    alert=runtime._alert_history[proposal.alert_id],
                    complaint=complaint,
                    proposal=proposal,
                    previews=_previews(runtime, complaint, proposal),
                    on_action=AsyncMock(),
                    client=client,
                )
        self.assertEqual(result.status, "partial")
        self.assertIsNotNone(result.linear_issue_url)
        self.assertIsNone(result.github_pull_request_url)
        self.assertFalse(any(request.method == "PUT" for request in requests))
        self.assertFalse(
            any(
                request.url.path.endswith(("/pulls", "/chat.postMessage"))
                for request in requests
            )
        )

    async def test_missing_slack_correlation_keeps_linear_and_pr_links(self) -> None:
        runtime, complaint, proposal = _fixture(correlation=False)
        requests: list[httpx.Request] = []
        transport = httpx.MockTransport(_success_handler(requests))
        async with httpx.AsyncClient(transport=transport) as client:
            with patch.dict(os.environ, LIVE_ENV, clear=False):
                result = await execute_connector_writes(
                    alert=runtime._alert_history[proposal.alert_id],
                    complaint=complaint,
                    proposal=proposal,
                    previews=_previews(runtime, complaint, proposal),
                    on_action=AsyncMock(),
                    client=client,
                )
        self.assertEqual(result.status, "partial")
        self.assertIsNotNone(result.linear_issue_url)
        self.assertIsNotNone(result.github_pull_request_url)
        self.assertIsNone(result.slack_message_ts)
        self.assertFalse(
            any(request.url.path.endswith("/chat.postMessage") for request in requests)
        )

    async def test_errors_and_results_redact_tokens(self) -> None:
        runtime, complaint, proposal = _fixture()
        token = LIVE_ENV["GITHUB_TOKEN"]

        def handler(request: httpx.Request) -> httpx.Response:
            del request
            return httpx.Response(
                200,
                json={"errors": [{"message": f"authorization {token} rejected"}]},
            )

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as client:
            with patch.dict(os.environ, LIVE_ENV, clear=False):
                result = await execute_connector_writes(
                    alert=runtime._alert_history[proposal.alert_id],
                    complaint=complaint,
                    proposal=proposal,
                    previews=_previews(runtime, complaint, proposal),
                    on_action=AsyncMock(),
                    client=client,
                )
        self.assertNotIn(token, json.dumps(result.model_dump(mode="json")))


class GitHubPatchSafetyTests(unittest.IsolatedAsyncioTestCase):
    def test_patch_requires_exactly_one_known_line(self) -> None:
        self.assertEqual(
            patch_app_js(BUGGY_PRICE_LINE),
            CORRECT_PRICE_LINE,
        )
        for source in ("", f"{BUGGY_PRICE_LINE}\n{BUGGY_PRICE_LINE}"):
            with (
                self.subTest(source=source),
                self.assertRaises(GitHubWriteError),
            ):
                patch_app_js(source)

    async def test_rejects_non_allowlisted_repo_and_unsafe_branch_before_io(
        self,
    ) -> None:
        client = AsyncMock()
        for repository, branch in (
            ("other/repo", "runbook/safe"),
            ("abhijandyala/testing24", "main"),
            ("abhijandyala/testing24", "../main"),
        ):
            with (
                self.subTest(repository=repository, branch=branch),
                self.assertRaises(GitHubWriteError),
            ):
                await create_github_draft_pull_request(
                    token="mock",
                    repository=repository,
                    base="main",
                    branch=branch,
                    title="title",
                    body="body",
                    linear_url="https://linear.example/ENG-1",
                    client=client,
                )
        client.request.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
