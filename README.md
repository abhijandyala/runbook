# runbook

`runbook` is a graph-native incident-response copilot built for the Memory Meets
Motion Hackathon. Live alerts enter a durable LaserData stream, FalkorDB
supplies relationship-aware operational memory, RocketRide performs every LLM
reasoning step, and Guild.ai gates the handoff to a human reviewer.

## Sponsor boundaries

- **FalkorDB:** service dependencies, deploys, incidents, runbooks, and durable
  resolution memory.
- **LaserData:** one `sentry` stream with `alerts`, `hypotheses`, `proposals`,
  and `resolutions` topics.
- **RocketRide:** the only inference path. Application code never calls an LLM
  provider directly.
- **Guild.ai:** mandatory agent handoff and human-review primitive; its exact
  integration will be confirmed with the event mentor.
- **Linkup:** optional external-status enrichment after all mandatory sponsors
  are green.

## Current execution model

1. A typed alert is published to LaserData.
2. The Diagnostician traverses FalkorDB and deterministically ranks candidates.
3. `runbook.pipe` turns those candidates into cited reasoning through
   RocketRide Cloud.
4. The Remediator selects a graph-backed, whitelisted runbook action and asks
   RocketRide to justify it.
5. A human approves, rejects, or modifies the action.
6. The outcome is written to FalkorDB as memory for the next incident.

## Safety

Secrets belong only in `.env`; copy `.env.example` and fill values locally.
The RocketRide project UUID is intentionally committed as a literal resource
identifier inside `runbook.pipe`.

## Demo scenarios

- `s01_bad_deploy`: recent deploy plus a matching historical incident.
- `s02_upstream_failure`: a root cause two graph hops from the alert.
- `s03_cloud_outage`: optional Linkup enrichment when graph confidence is low.

Simulated alerts and remediation endpoints are disclosed demo fixtures. Graph
queries, candidate ranking, RocketRide inference, streaming, human decisions,
and memory writeback use the real integration paths.
