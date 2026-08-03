# 3-minute demo

Start the API, then open the [React workspace](http://127.0.0.1:8000/demo/).

## Before judging — choose the write mode

Dry-run is the safe default: keep `CONNECTOR_DRY_RUN=true` and
`CONNECTOR_LIVE_WRITES=false`. The demo will show previews and **Approve dry
run**, and no external connector writes are authorized.

For the controlled live demo, the operator must deliberately set both gates to
`CONNECTOR_DRY_RUN=false` and `CONNECTOR_LIVE_WRITES=true` before starting the
API. Confirm the red **LIVE WRITES ENABLED** banner and the allowlisted
`abhijandyala/testing24` target in **Settings**. Do not display `.env` or any
credentials. Even in this mode, each alert still requires explicit live-write
intent and its exact alert ID typed at approval time.

In a prepared local copy of
[`abhijandyala/testing24`](https://github.com/abhijandyala/testing24), open
`index.html` and switch the pricing toggle from **Monthly** to **Yearly**. Point
out that the intentionally broken `app.js` displays the monthly dataset for the
yearly selection. This is the visible symptom the runbook will fix.

## 0:00–0:35 — Real Slack intake and report

1. Point out the current `DRY RUN` or `LIVE WRITES ENABLED` banner, Slack
   `CONNECTED`, and the live event-stream status.
2. Click **Refresh** to sync Slack, find the complaint already posted in
   Slack, and show that its report describes the broken pricing toggle.
3. Click **Run** on that message. No demo message is fabricated. Watch the
   complaint become the selected alert and the bridge report populate.

If Slack is empty or unavailable, paste the same text into **Manual complaint
fallback** and click **Run complaint**. This preserves the diagnosis demo, but
does not provide an originating Slack thread for a completion reply.

## 0:35–1:30 — Evidence, proposal, and gate

1. Watch LaserData move the incident through intake, diagnosis, remediation, and
   the human gate.
2. Open **Inbox**: show FalkorDB paths, cited evidence, confidence, and the bridge
   evidence brief. RocketRide is the backend-only inference boundary.
3. Open **Approvals**: inspect the exact proposal, runbook source, safety class,
   connector targets, and reasoning.
4. In dry-run, click **Approve dry run**. In controlled live mode, check the
   explicit intent box, type the displayed alert ID exactly, then click
   **Approve live writes**. Never approve a target you have not verified. If the
   proposal is destructive, its separate exact action-target confirmation also
   remains required.

## 1:30–2:25 — Ordered result and artifact proof

1. Return to **Dashboard** and show **Connector write activity**. Live execution
   is fixed to **Linear → GitHub → Slack**; completion is proven by connector
   events and the authoritative report, not by the approval response alone.
2. Click the resulting Linear and GitHub links. For the verified run, open
   [Linear AGE-29](https://linear.app/agentsloveyou2hackathon/issue/AGE-29/warning-customer-reported-failure-in-payments-api)
   and [`testing24` draft PR #1](https://github.com/abhijandyala/testing24/pull/1).
3. In the PR, verify it is still **Open** and **Draft**, targets `main` from a
   `runbook/*` feature branch, and changes only `app.js` with one deletion and
   one insertion: the Yearly path now reads `dataset.yearly` and the Monthly
   path reads `dataset.monthly`.
4. Do **not** click **Ready for review** or **Merge**. The product never merges
   or writes directly to `main`, and the judge must never merge the draft.
5. Return to Slack and show the completion reply in the original complaint
   thread. Do not expose or read out channel or message IDs.
6. Open **Memory** and show the real final incident ID and resolution persisted
   to FalkorDB.

If a connector fails, stop and explain the displayed result accurately. Earlier
successful artifacts remain, the failed step and all later unattempted steps are
reported, and the rollup becomes `partial` or `failed`. Never rerun by changing
the decision: identical resubmission is idempotent and replays the recorded
result without duplicate external artifacts, while a different second decision
is rejected.

## 2:25–3:00 — Killshot and proof

1. Open the [judge/operator UI](http://127.0.0.1:8000/). Show the **FalkorDB
   ON/OFF** toggle: ON grounds diagnosis in graph paths; OFF produces zero graph
   latency, alert-only evidence, low confidence, and a safe diagnostic/none
   action. This is the graph-memory control, not a mocked screenshot.
2. Show [RocketRide Cloud proof](docs/evidence/rocketride-cloud-proof.md) and
   [live traces](docs/evidence/rocketride-live-traces.md).
3. Show the [Guild comparison](docs/evidence/guild-comparison.csv) and
   [experiment proof](docs/evidence/guild-experiment-proof.html): Guild AI
   tracks grounded versus graph-off runs, but does **not** prove runtime handoff.

## Fallback dry-run

If live-write configuration, connectivity, or artifact access is uncertain,
restart with `CONNECTOR_DRY_RUN=true` and `CONNECTOR_LIVE_WRITES=false`. Run the
same complaint and evidence walkthrough, click **Approve dry run**, and show the
Linear, GitHub, and Slack previews labeled **NOT SENT**. State plainly that this
proves the gated workflow and generated plan, not external writes; use the
verified links below as prior live-run evidence.

## Verified live demo artifacts

- [Linear AGE-29](https://linear.app/agentsloveyou2hackathon/issue/AGE-29/warning-customer-reported-failure-in-payments-api)
- [GitHub `abhijandyala/testing24` draft PR #1](https://github.com/abhijandyala/testing24/pull/1)
- The originating Slack thread contains the completion reply with both links;
  channel and message IDs are intentionally omitted.
