# Architecture

## Trust model

Amazon Ads MCP provides capabilities, not policy. Hermes Main collects and explains data. Only the deterministic engine creates executable decisions. A bound Executor performs one reserved decision; a different Verifier independently reads Amazon state before the control plane commits it.

## Components

- **Live MCP catalog**: exact Hermes registered name, native name, JSON Schema, semantic, family, risk, hash and drift state.
- **Optimization engine**: attribution maturity, data freshness, KPI calculations, target/search-term/budget/placement/recommendation rules.
- **Transaction boundary**: SQLite `BEGIN IMMEDIATE`, unique decision IDs, reservation token, TTL, cooldown, task/day limits.
- **Outcome parser**: explicit success/failure/partial/pending/unknown; HTTP-like or arbitrary JSON is never assumed successful.
- **Independent verification**: only a task-bound verifier can submit fresh actual state. Mismatch opens a critical alert.
- **Web**: browser session, CSRF, Origin checks, login rate limiting, immutable safety settings, read-oriented operations dashboard.

## State machine

```text
planned -> reserved -> executed|pending|failed -> verified|mismatch
```

A task cannot finalize while decisions are planned, reserved, executed or pending. Only all-verified tasks become `completed`; failures or mismatches become `completed_with_issues`.

## Data model

- `profiles`: marketplace/currency and per-profile strategy overrides;
- `cycles`: source, window, maturity/data quality, KPI and snapshot hash;
- `metrics`: normalized account/campaign/target/search-term/placement rows;
- `decisions`: rule, evidence, exact planned payload, reservation and result;
- `tasks`, `workers`: Main/Executor/Verifier lifecycle;
- `mcp_tools`: exact live MCP contract and drift state;
- `actions`, `verifications`, `events`, `alerts`: immutable operational history;
- `stream_events`: deduplicated Marketing Stream events.

## Failure behavior

Control-plane outage, missing catalog, removed tool, unacknowledged drift, malformed args, wrong role, stale task, duplicate decision, batch write, unstructured result or failed verification all stop commitment. Reads remain available in `OBSERVE`; `PAUSED` blocks all Amazon MCP activity.
