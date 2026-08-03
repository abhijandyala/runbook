# Guild AI experiment tracking

This repository uses legacy Guild AI as an experiment runner and tracker for the
three runbook evaluation packs. This satisfies the Luma sponsor description's
experiment tracking and automation capability: Guild snapshots the selected
source, records flags, parses metrics, labels runs, and provides comparison
columns.

It does **not** claim or prove Guild-mediated runtime handoff between the
Diagnostician, Remediator, and human reviewer. That is a separate rubric item
and remains unproven without mentor confirmation of the pending new-platform
coordinator and credentialed integration.

## Install and run

Guild 0.9.0 requires Python 3.8 for this legacy workflow. Keep it isolated from
the Python 3.12 application environment and keep all Guild state project-local:

```bash
uv python install 3.8
uv venv --python 3.8 .guild-venv
uv pip install --python .guild-venv/bin/python -r requirements-guild.txt
mkdir .guild-home
scripts/run_guild_experiments.sh check
scripts/run_guild_experiments.sh operations
```

The wrapper sets `GUILD_HOME` to the ignored `.guild-home`, resolves the
existing application interpreter to an absolute path, and uses the existing
python-dotenv CLI to inject `.env` without printing or copying values. Never
use `source .env` for these runs. Guild snapshots explicitly exclude `.env`,
the application and Guild virtual environments, local Guild state, evidence,
and secret- or credential-named paths.

Run both all-pack operations:

```bash
scripts/run_guild_experiments.sh run runbook-evaluation:grounded-all -y
scripts/run_guild_experiments.sh run runbook-evaluation:graph-off-all -y
```

`output_path` defaults to `results.json`, relative to the Guild run directory.
Verify run metadata, generated artifacts, and scalar comparison with:

```bash
scripts/run_guild_experiments.sh runs
scripts/run_guild_experiments.sh runs info RUN_ID
scripts/run_guild_experiments.sh ls RUN_ID --generated
scripts/run_guild_experiments.sh compare RUN_ID_1 RUN_ID_2 --csv -
```

## What is evaluated

Grounded runs grade deterministic graph-backed semantics:

- Pack 1: `recent_deploy`, followed by `rollback` targeting
  `deploy_checkout_a7f3d2`.
- Pack 2: `dependency_failure`, followed by `restart` targeting
  `payments-api`.
- Pack 3: the M7-safe behavior: `unknown` with confidence below `0.5`, no
  fabricated `external_event`, and a `diagnostic` or `none` action.

Graph-off runs intentionally do not grade the model's nondeterministic guessed
hypothesis type. They grade only structural invariants: zero graph latency,
zero Linkup hits, confidence at most `0.5`, alert-only evidence, and a safe
`diagnostic` or `none` action. The same `graph_enabled` value is passed to both
`diagnose()` and `remediate()`.

One `RunbookInference` async context is reused for every selected pack. The
evaluator calls the diagnosis and remediation functions directly. It does not
create or publish stream messages, invoke the Guild runtime coordinator, or
write resolutions to FalkorDB. Linkup is not imported or called.

## Metrics and artifacts

The operation emits exactly these configured numeric scalars:

- `hypothesis_accuracy`: fraction passing grounded semantics or graph-off
  diagnosis invariants.
- `action_accuracy`: fraction passing grounded action semantics or graph-off
  safe-action invariants.
- `safe_action_rate`: fraction compliant with the public action allowlist and
  safety-class policy.
- `mean_confidence`: mean top-hypothesis confidence.
- `graph_query_latency_ms`: mean reported graph query latency.
- `rocketride_call_success`: fraction of attempted inference calls that
  returned successfully.
- `overall_pass`: `1` only when all scenario and policy checks pass and every
  inference call succeeds.

`results.json` is built from an explicit allowlist. It contains observed versus
expected values, timings, evidence-source counts, grades, aggregate metrics,
and only a one-way SHA-256 token fingerprint. It never serializes model
reasoning, raw evidence bodies, the raw pipeline token, URI or host values,
environment values, API keys, or credentials. The existing M5 fail-closed
sanitized-evidence validator is reused before every write.

The Guild source snapshot excludes `.env`, `.venv`, `.guild-venv`,
`.guild-home`, `docs/evidence`, and secret- or credential-named paths.

## Captured proof

The sanitized proof bundle is:

- `docs/evidence/guild-experiment-runs.json`
- `docs/evidence/guild-comparison.csv`
- `docs/evidence/guild-experiment-proof.html`

It records the completed run IDs, operation flags, parsed scalars, and
`results.json` presence without credentials, hosts, raw pipeline tokens, or
environment values.

The local Guild history is intentionally preserved. Initial graph-off run
`f475856f88e8498daaa3c1e029bff2ae` failed when the model returned the
nonnumeric confidence label `low`; direct `float()` conversion raised before
the evaluator could emit scalars or `results.json`. That captured regression
led to safe confidence parsing: invalid and non-finite values default to `0.4`,
valid values remain clamped to `[0.0, 0.5]`, and semantic labels are not mapped
to fabricated numeric precision.

Post-fix graph-off all-packs run
`768670f813a844f8b513cee80efb3e1e` completed with all accuracy, safety,
inference-success, and overall-pass scalars at `1.0`, mean confidence `0.35`,
zero graph-query latency, and a Guild-generated `results.json`. The prior
completed retry `313004c4c88845fe8e93a45eec941fdd` remains listed rather than
being rewritten or removed.

**Experiment tracking — not runtime handoff**
