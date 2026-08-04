# Architecture

## Trust model

Amazon Ads MCP provides capabilities, not policy. Hermes Main collects and explains data. Only the deterministic engine creates executable decisions. A task-bound Executor performs one atomically reserved, single-entity decision. A different task-bound Verifier must make a fresh Amazon read; the control plane derives `actual` state from that recorded read action rather than trusting model-supplied JSON.

## Components

- **Live MCP catalog**: exact Hermes registered name, native name, JSON Schema, semantic, family, risk, hash and drift state. Changes to any contract attribute mark the tool drifted.
- **Optimization engine**: trusted-source checks, attribution maturity, window consistency, data freshness, finite/nonnegative metrics, duplicate detection, KPI calculations and target/search-term/budget/placement/recommendation rules.
- **Transaction boundary**: SQLite `BEGIN IMMEDIATE`, reservation token, TTL, cooldown and task/day limits. Expired reservations become `uncertain`; they are never silently returned to the queue.
- **Outcome parser**: explicit success/failure/partial/pending/unknown. Partial and unknown writes become `uncertain` and require reconciliation.
- **Recorded read evidence**: a current Verifier's structured, cataloged read action must be newer than the write attempt, within the evidence TTL, match the decision family and include the planned entity.
- **Recovery and operations**: full integrity checks, atomic SQLite backup, readiness reconciliation, alerts and immutable audit history.
- **Web**: browser session, CSRF, Origin checks, login rate limiting, immutable safety settings, explanations and an operations-oriented dashboard.

## State machine

```text
planned -> reserved -> executed|pending|uncertain|failed
executed|pending|uncertain -> verified|mismatch
```

`reserved` that expires becomes `uncertain`. A late tool result carrying the original reservation token can reconcile it. Failed writes cannot be presented as verified. A task cannot finalize while any decision remains planned, reserved, executed, pending or uncertain.

## Role authority

- Main may read, create bounded report jobs, plan, create tasks and delegate. It cannot execute ad writes.
- Executor is authoritative only when its exact session is the task's current running executor.
- Verifier is authoritative only when its exact, different session is the task's current running verifier.
- One running Executor and one running Verifier are allowed per task. A session cannot be rebound across active tasks or roles.

## Data model

- `profiles`: marketplace/currency, enabled state and validated strategy overrides;
- `cycles`: source, window, data quality, KPI and snapshot hash;
- `metric_rows`: immutable normalized input rows;
- `decisions`: rule, evidence, exact payload, reservation, execution and verification state;
- `tasks`, `workers`: Main/Executor/Verifier lifecycle and authoritative session IDs;
- `mcp_tools`: exact live contract and drift state;
- `actions`: before/after tool calls, redacted arguments and structured result evidence;
- `verifications`: expected/actual/difference plus the source read-action ID;
- `events`, `alerts`, `stream_events`: operations history and deduplicated Marketing Stream input.

## Failure behavior

Control-plane outage, unavailable catalog, removed/drifted/high-risk tool, malformed Schema arguments, wrong or stale role, disabled Profile, old/future decision, duplicate/cooldown, multi-entity write, expired reservation, unstructured outcome, missing/stale/wrong-family read evidence or verification mismatch all fail closed. `PAUSED` blocks Amazon Ads reads, jobs and writes; `OBSERVE` permits collection and planning but no writes.
