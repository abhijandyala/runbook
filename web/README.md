# Runbook Incident Command demo

GhostThread-inspired React/Vite shell adapted to the Runbook incident pipeline. The build base is `/demo/`; static output is written to `web/dist`.

## Commands

```sh
npm install
npm run typecheck
npm run lint
npm run build
```

Vite proxies `/bridge`, `/events`, `/decisions`, and `/health` to `http://127.0.0.1:8000` in development. Set `VITE_API_BASE` only when the API is hosted on a different origin.

## API assumptions

- `GET /bridge/health` exposes connector configuration booleans and nested `sponsor_health`; the UI also tolerates additive/legacy health fields.
- `GET /bridge/slack/messages` returns either an array or `{ "messages": [...] }`.
- `POST /bridge/complaint` accepts the existing `BridgeComplaintRequest` (`text` plus optional `service`, `channel`, `external_id`, `graph_enabled`, and `severity`) and returns an `alert_id`.
- `/events` uses named SSE events: `alert`, `hypotheses`, `proposal`, `decision`, `action`, `outcome`, `resolution`, and `bridge_report`.
- `POST /decisions` uses the repository's existing proposal decision contract. Destructive approval sends `typed_confirmation` equal to `action_target`.
- `GET /bridge/report/{alert_id}` may return `evidence_brief`, provider preview fields, or `action_previews`.

No successful integration data is seeded. The TestTeam identity is presentation-only demo chrome.
