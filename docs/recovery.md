# Sunday freeze and recovery

Run every command from the repository root.

## 1. Start and prepare recovery dependencies

```bash
./scripts/start.sh
```

The P/M0 recovery flow is Cloud-first. The script creates the Python 3.12
environment and installs its dependencies, then reads `LASER_CONNECTION_STRING`
from `.env` without loading it into Bash. A configured remote deployment uses
LaserData Cloud directly:

```text
Using configured LaserData Cloud deployment.
LaserData healthy: sentry stream, four topics, typed round trip alrt_health_...
Seeded TestOps graph: 6 services, 5 dependencies, 2 deploys, 2 incidents, 4 runbooks.
runbook dependencies are ready.
```

Docker is needed only when the configured LaserData endpoint is local. In that
fallback mode, the script checks Docker, clones the official Laser Stack into
ignored `.local/laser-stack` on its first run, and starts it before running the
same LaserData and FalkorDB checks. Its output also includes:

```text
Laser Stack is ready.
```

## 2. Recheck only the LaserData topology

```bash
.venv/bin/python -m scripts.check_laser
```

Expected:

```text
LaserData healthy: sentry stream, four topics, typed round trip alrt_health_...
```

The check calls topology initialization twice before its message round trip, so
success proves initialization is idempotent.

## 3. Reseed FalkorDB

```bash
.venv/bin/python graph_seed.py
```

Expected:

```text
Seeded TestOps graph: 6 services, 5 dependencies, 2 deploys, 2 incidents, 4 runbooks.
```

This is idempotent. Do not use `--reset` during the demo because accumulated
incident memory is part of the product.

## 4. Check RocketRide Cloud

```bash
.venv/bin/python -m scripts.check_rocketride
```

Expected:

```text
RocketRide Cloud healthy: traced inference alrt_rr_health_...
```

## 5. Check the Guild qualifying gate

With the API running, inspect its health contract:

```bash
curl -fsS http://127.0.0.1:8000/health
```

Before submission, the response must contain both a real `guild_mode` and:

```json
{"guild_qualifying": true}
```

`guild_qualifying: false` is an explicit submission blocker. The local human
review fallback may keep development moving, but it does not satisfy the Guild
sponsor integration. Stop and complete `docs/guild-mentor-intake.md`; do not
rename the fallback or treat a local decision as Guild evidence.

## 6. Trigger Pack 1

```bash
.venv/bin/python pipeline.py scenarios/s01_bad_deploy.json --approve
```

Expected final result:

```text
"hypothesis": "recent_deploy"
"action": "rollback"
"decision": "approve"
"outcome": "verified"
```

The preceding boundary logs must show LaserData, FalkorDB, and RocketRide.

## 7. Judge-entered novel alert

```bash
.venv/bin/python pipeline.py \
  --service judge-novel-service \
  --metric queue_depth \
  --value 91 \
  --threshold 20
```

An unknown service must finish with low confidence and a safe diagnostic action;
it must not crash.

## M7 and Pack 3 status

M7 (Linkup enrichment) is intentionally cut until all four required sponsors
— LaserData, FalkorDB, RocketRide, and Guild.ai — are green. Pack 3 therefore
reports zero Linkup hits, keeps the diagnosis below 0.5 confidence without
asserting an external event, and honestly escalates with a safe `diagnostic` or
`none` action instead of fabricating external evidence.

## Exact unresolved items

- Guild publishes a CLI, agent SDK, API-triggered sessions, and blocking
  `ui_prompt`, but the repository still lacks a mentor-verified mapping from
  those public primitives to its handoff/task contracts. Until that mapping is
  implemented and `/health` reports `guild_qualifying: true`, Guild remains a
  submission blocker. Do not invent an endpoint or ID.
- Linkup remains conditional and is not part of the four mandated sponsors.
- Git initialization is intentionally deferred until the hackathon at the
  builder's request. `.gitignore` is ready, but `git status` cannot yet prove the
  ignore rule.
- Capture the RocketRide Cloud trace screenshot at the venue/dashboard.
- The official Laser `./scripts/smoke` currently prints
  `line 71: network_args[@]: unbound variable` and then reports both the managed
  SDK smoke and overall smoke as passed. The application-level typed round trip
  in `scripts/check_laser.py` passes independently.
- Do not use Colima for Laser Stack on this Mac. Iggy versions `0.8.101-ld` and
  `0.8.102-ld` both panic there with
  `the thread pool is needed but no worker thread is running`. Docker Desktop
  resolves the incompatibility.
