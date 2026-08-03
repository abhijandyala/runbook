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
5. Linear, GitHub, Slack, and remediation actions remain dry-run previews.
6. The evidence brief and final resolution become durable FalkorDB incident memory.

Guild AI tracks grounded versus graph-off experiments and metrics. It is
**experiment evidence, not a Guild-mediated runtime handoff**.

## Setup

```bash
cp .env.example .env
# Fill the required FalkorDB, LaserData, RocketRide, and Slack values.
# Linear/GitHub are optional preview metadata; keep CONNECTOR_DRY_RUN=true.
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

- Slack is a real read-only connector. The UI labels all Linear/GitHub/Slack
  outputs `NOT SENT`; no production service is changed.
- Approval permits only simulated execution. Destructive proposals also require
  exact typed confirmation.
- Keep credentials only in `.env`; reports and captured evidence are sanitized.
- TestTeam branding is demo chrome. Linkup is disabled, and Guild runtime
  handoff is not claimed.

Evidence: [RocketRide proof](docs/evidence/rocketride-cloud-proof.md),
[live traces](docs/evidence/rocketride-live-traces.md),
[Guild experiment notes](docs/guild-experiment-tracking.md),
[Guild runs](docs/evidence/guild-experiment-runs.json), and
[Guild comparison](docs/evidence/guild-comparison.csv).
