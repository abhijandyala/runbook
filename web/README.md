# Runbook Incident Command demo

GhostThread-inspired React/Vite shell adapted to the Runbook incident pipeline. The build base is `/demo/`; static output is written to `web/dist`.

## Commands

```sh
npm install
npm run typecheck
npm run lint
npm run safety:check
npm run build
```

Vite proxies `/bridge`, `/events`, `/decisions`, and `/health` to `http://127.0.0.1:8000` in development. Set `VITE_API_BASE` only when the API is hosted on a different origin.

## API assumptions

- `GET /bridge/health` exposes connector configuration, `connector_live_writes_enabled`, `github_repo_allowed`, and nested `sponsor_health`.
- `GET /bridge/slack/messages` returns either an array or `{ "messages": [...] }`.
- `POST /bridge/complaint` accepts the existing `BridgeComplaintRequest` (`text` plus optional `service`, `channel`, `external_id`, `graph_enabled`, and `severity`) and returns an `alert_id`.
- `/events` uses named SSE events including `connector_action`; connector completion is rendered only from that event or report data.
- `POST /decisions` retains the existing contract in dry-run mode. A live approval additionally sends `execute_live_writes: true` and `live_writes_confirmation` equal to the exact `alert_id`.
- `GET /bridge/report/{alert_id}` may return `evidence_brief`, provider preview fields, `action_previews`, or `connector_writes` rollup/actions.

No successful integration data is seeded. The TestTeam identity is presentation-only demo chrome.
