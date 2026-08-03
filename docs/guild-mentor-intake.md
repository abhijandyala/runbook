# Guild mentor intake

Use this checklist before implementing the Guild adapter. Record only values and
contracts supplied by Guild or confirmed by the event mentor.

## Public findings (verified 2026-08-03)

- [ ] CLI: Node.js 18+ and `npm i @guildai/cli -g`; authenticate with
  `guild auth login`, then verify with `guild auth status` and `guild doctor`.
- [ ] Agent runtime: agent code imports `@guildai/agents-sdk`; Guild's runtime
  provides the SDK and `zod`, so the agent project must not invent package
  versions or direct service calls.
- [ ] API-trigger auth: create the trigger/key in Guild's web UI. The documented
  combined `api_key_id:api_key_secret` is sent with HTTP Basic Authentication;
  there is no documented CLI command for creating API keys.
- [ ] Session start: the public request is
  `POST /api/workspaces/{owner_name}/{workspace_name}/sessions` with
  `session_type: "api_trigger"` and an `agent_input` object matching the
  agent's input schema.
- [ ] Session follow-up/status: public docs describe
  `POST /api/sessions/{session_id}/events`, `GET /api/sessions/{session_id}`,
  `GET /api/sessions/{session_id}/events`, and
  `GET /api/sessions/{session_id}/tasks`.
- [ ] Human prompt: adding `userInterfaceTools` enables blocking
  `task.ui.prompt({type: "text", text: "..."})`; the documented reply exposes
  `reply.text`, and tool responses may also include attachments.
- [ ] Runtime identity: `task.sessionId` is documented as an opaque session ID.
  Public docs do not define this repository's `guild_handoff_id` or
  `guild_task_id`; their source and mapping require mentor confirmation.

Sources:

- <https://docs.guild.ai/cli/getting-started>
- <https://docs.guild.ai/platform/triggers>
- <https://docs.guild.ai/sdk/task-object>
- <https://docs.guild.ai/sdk/tools>

## Contracts requested from the mentor

- [ ] **Agent schema:** exact `inputSchema` for alert, ranked hypotheses,
  proposal, reviewer context, and correlation IDs; exact output/error schema.
- [ ] **Session schema:** exact successful session-create response, status/event
  fields, terminal states, retry/idempotency rules, and the field containing the
  opaque `session_id`.
- [ ] **Handoff schema:** approved Guild primitive and sequence for assigning a
  human review. Confirm whether the handoff is the session, a session task, or
  another documented object; provide its create/read/complete contract.
- [ ] **`ui_prompt` schema:** approved prompt text/options, reviewer response
  shape, cancellation/timeout behavior, and how reviewer identity is recorded.
- [ ] **Decision return:** exact path for approve/reject/modify, including
  modified action fields and the authoritative Guild task identifier.

## IDs required

- [ ] Workspace owner name, workspace name, and workspace ID.
- [ ] Agent ID or full agent name, published version ID, and API trigger ID.
- [ ] Credential identifier/secret delivery process (store secrets only in
  local `.env`; never paste them into this document, logs, or screenshots).
- [ ] Returned session ID and the mentor-approved value mapped to
  `HypothesisSet.guild_handoff_id`.
- [ ] Returned task/handoff ID mapped unchanged to
  `ActionProposal.guild_task_id` and `ReviewerDecision.guild_task_id`.

## Acceptance evidence

- [ ] `guild auth status` and `guild doctor` pass, with tokens redacted.
- [ ] Published agent/version and API trigger are visible in the intended
  workspace.
- [ ] A novel alert creates a real Guild session/handoff and blocks at
  `ui_prompt` until a human responds.
- [ ] Guild session events/tasks show the human prompt and response; captured
  IDs exactly match the UI's hypothesis, proposal, and decision views.
- [ ] Approve, reject, and modify each return through Guild and preserve the
  existing simulated-execution safety boundary.
- [ ] Restart/retry evidence shows correlation is idempotent and does not create
  duplicate review tasks.
- [ ] `GET /health` reports the confirmed `guild_mode` and
  `guild_qualifying: true` with local fallback disabled for submission.
- [ ] Screenshot/log bundle redacts credentials while showing session/task IDs,
  prompt, human response, final decision, and sponsor boundary labels.
