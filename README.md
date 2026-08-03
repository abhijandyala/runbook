# runbook

`runbook` turns a real Slack complaint into an evidence-backed incident plan. Its
GhostThread-inspired React workspace is at [http://127.0.0.1:8000/demo/](http://127.0.0.1:8000/demo/);
the judge/operator UI is at [http://127.0.0.1:8000/](http://127.0.0.1:8000/).
There is no Sentry.io integration.

## How it works

1. Read-only Slack intake publishes an alert to LaserData.
2. FalkorDB supplies service, deploy, runbook, and past-incident paths.
3. RocketRide performs diagnosis and remediation inference.
4. A human must approve, reject, or modify the proposal.
5. Connector actions remain dry-run previews by default. An explicitly enabled
   live approval can write to Linear, GitHub, then the originating Slack thread.
6. The evidence brief and final resolution become durable FalkorDB incident memory.

Guild AI tracks grounded versus graph-off experiments and metrics. It is
**experiment evidence, not a Guild-mediated runtime handoff**.

## Setup

```bash
cp .env.example .env
# Fill the required FalkorDB, LaserData, RocketRide, and Slack values.
# Linear/GitHub are optional preview metadata. Keep the safe defaults:
# CONNECTOR_DRY_RUN=true and CONNECTOR_LIVE_WRITES=false.
./scripts/start.sh
cd web && npm ci && npm run build && cd ..
.venv/bin/uvicorn ui.server:app --port 8000
```

`scripts/start.sh` creates the Python 3.12 environment, installs requirements,
checks LaserData, and seeds FalkorDB. See [DEMO.md](DEMO.md) for the walkthrough.

## Test and build

```bash
.venv/bin/python -m unittest discover -s tests
cd web
npm run typecheck
npm run lint
npm run build
```

## Safety and evidence

- Dry-run is the safe default. With `CONNECTOR_DRY_RUN=true` or
  `CONNECTOR_LIVE_WRITES=false`, Linear, GitHub, and Slack remain previews
  labeled `NOT SENT`.
- Controlled live writes require both environment gates
  (`CONNECTOR_DRY_RUN=false` and `CONNECTOR_LIVE_WRITES=true`), configured
  credentials, an **Approve live writes** decision, explicit live-write intent,
  and the alert ID typed exactly for that alert. Missing any gate leaves the
  connector workflow unauthorized. Destructive remediation still has its
  separate exact-target confirmation.
- GitHub writes are hard-allowlisted to
  [`abhijandyala/testing24`](https://github.com/abhijandyala/testing24). The only
  supported patch deterministically changes exactly one line in `app.js`:
  `yearly ? el.dataset.monthly : el.dataset.yearly` becomes
  `yearly ? el.dataset.yearly : el.dataset.monthly`. If the known buggy line
  does not occur exactly once, the write fails closed.
- GitHub creates a `runbook/*` feature branch and opens a draft PR. It never
  writes directly to `main`, merges a PR, or changes any other file.
- Live actions execute strictly in the order **Linear → GitHub → Slack**. A
  failure stops the sequence: completed earlier artifacts remain, the failed
  and unattempted actions are reported, and the result is `partial` or `failed`.
- Decision submission is idempotent per proposal. Repeating the identical
  request returns and replays the recorded result without creating duplicate
  issues, PRs, or replies; a different second decision is rejected.
- Keep credentials only in `.env`; reports and captured evidence are sanitized.
- TestTeam branding is demo chrome. Linkup is disabled, and Guild runtime
  handoff is not claimed.

## Verified live demo artifacts

- Linear: [AGE-29 — customer-reported payments failure](https://linear.app/agentsloveyou2hackathon/issue/AGE-29/warning-customer-reported-failure-in-payments-api)
- GitHub: [`abhijandyala/testing24` draft PR #1](https://github.com/abhijandyala/testing24/pull/1)
- Slack: the originating complaint thread contains the completion reply with
  both artifact links. Channel and message identifiers are intentionally not
  published.

Evidence: [RocketRide proof](docs/evidence/rocketride-cloud-proof.md),
[live traces](docs/evidence/rocketride-live-traces.md),
[Guild experiment notes](docs/guild-experiment-tracking.md),
[Guild runs](docs/evidence/guild-experiment-runs.json), and
[Guild comparison](docs/evidence/guild-comparison.csv).
