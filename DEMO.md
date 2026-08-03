# 3-minute demo

Start the API, then open the [React workspace](http://127.0.0.1:8000/demo/).

## 0:00–0:30 — Real Slack intake

1. Point out `DRY RUN`, Slack `CONNECTED`, and the live event-stream status.
2. Click **Refresh** to sync Slack, find the complaint already posted in
   `#team`, and click **Run**. No demo message is fabricated.

If Slack is empty or unavailable, paste the same text into **Manual complaint
fallback** and click **Run complaint**. If any connector fails, show the error
and continue with the data already streamed; never claim a successful connector.

## 0:30–1:30 — Evidence, proposal, and gate

1. Watch LaserData move the incident through intake, diagnosis, remediation, and
   the human gate.
2. Open **Inbox**: show FalkorDB paths, cited evidence, confidence, and the bridge
   evidence brief. RocketRide is the backend-only inference boundary.
3. Open **Approvals**: inspect the exact proposal, runbook source, safety class,
   and reasoning. Click **Approve dry run**. If prompted, type the exact action
   target first.

## 1:30–2:10 — Preview, not production

Return to **Dashboard**. Show the Linear issue, draft GitHub change, and Slack
reply previews, each labeled **NOT SENT**. State that approval executed only the
simulated action path. Open **Memory** and show the real final incident ID and
resolution persisted to FalkorDB.

## 2:10–3:00 — Killshot and proof

1. Open the [judge/operator UI](http://127.0.0.1:8000/). Show the **FalkorDB
   ON/OFF** toggle: ON grounds diagnosis in graph paths; OFF produces zero graph
   latency, alert-only evidence, low confidence, and a safe diagnostic/none
   action. This is the graph-memory control, not a mocked screenshot.
2. Show [RocketRide Cloud proof](docs/evidence/rocketride-cloud-proof.md) and
   [live traces](docs/evidence/rocketride-live-traces.md).
3. Show the [Guild comparison](docs/evidence/guild-comparison.csv) and
   [experiment proof](docs/evidence/guild-experiment-proof.html): Guild AI
   tracks grounded versus graph-off runs, but does **not** prove runtime handoff.
