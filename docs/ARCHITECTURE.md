# Architecture v3.2

## Trust model

Amazon Ads MCP provides live capabilities, not policy. Hermes Main collects data, explains evidence and constructs plans. The local control plane decides whether an action is routine-autonomous, operator-approved or permanently blocked.

- Main never receives Amazon Ads write authority.
- A task-bound Executor may perform exactly one atomically reserved action at a time.
- A different task-bound Verifier Session must make a fresh Amazon read.
- High-risk structural operations require an unchanged, unexpired, exact operator-approved plan.
- The AI can request approval but cannot use the browser approval authority.

## Components

- **Live MCP Catalog**: exact Hermes registered name, native name, JSON Schema, semantic, family, risk, hash and drift state. Changes to any contract attribute mark the tool drifted.
- **Optimization engine**: trusted-source checks, attribution maturity, window consistency, freshness, finite/nonnegative metrics, duplicates, KPIs and routine target/search-term/budget/placement/recommendation rules.
- **Managed structural planner**: validates live MCP tools, action types, entity identities, full arguments, expected states, dependencies and budget exposure before an approval request exists.
- **Approval authority**: canonical plan hash, exact confirmation, expiry, actor, event history and per-decision one-time consumption. Browser approval uses Session, Origin and CSRF. Optional command approval uses a separate credential and is disabled by default.
- **Structural execution renderer**: binds the unique real Amazon ID returned by a successful create Action and fills only pre-approved `{{decision:<plan-key>.entity_id}}` templates in later actions and verification expectations.
- **Transaction boundary**: SQLite `BEGIN IMMEDIATE`, reservation token, TTL, cooldown, task/day limits, campaign-create limits and cumulative budget exposure.
- **Outcome parser**: explicit success/failure/partial/pending/unknown. Partial and unknown writes become `uncertain` and require reconciliation.
- **Recorded read evidence**: a current Verifier's structured, cataloged read Action must be newer than the write attempt, within the evidence TTL, match the decision family and include the exact real entity.
- **Hermes lifecycle integration**: LLM/tool/session/subagent hooks, live resource state, durable Outbox and model-fallback telemetry.
- **Recovery and operations**: integrity checks, atomic backup, readiness reconciliation, alerts, adaptive retention, disk-pressure write pause and audit history.
- **Web**: browser Session, CSRF, Origin checks, login rate limiting, exact approval details and an operations-oriented dashboard.

## Permission classes

```text
routine deterministic decision
    -> no interaction -> Executor -> Verifier

structural/high-risk plan
    -> awaiting_approval -> exact human approval -> Executor -> Verifier

billing/account-admin/unknown/drifted/delete/composite-bulk
    -> permanently blocked
```

A high/critical risk label is no longer a global permanent denial. It becomes approval-gated only when the exact effects can be represented as narrow live-Schema-compatible actions and independently verified.

## Routine decision state machine

```text
planned -> reserved -> executed|pending|uncertain|failed
executed|pending|uncertain -> verified|mismatch
```

An expired reservation becomes `uncertain`. A late result carrying the original reservation token may reconcile it. Failed writes cannot be presented as verified. A task cannot finalize while a decision remains planned, reserved, executed, pending or uncertain.

## Approval state machine

```text
pending -> approved -> completed
   |          |            |
   |          |            +-> completed_with_issues
   |          +-> expired_in_flight -> completed_after_expiry
   +-> rejected|expired|cancelled
```

Each approval has child decision states:

```text
pending -> reserved -> completed|issue
   +-> expired
```

Rules:

- approval is bound to a canonical plan and full Payload Hash;
- the task plan must still hash identically at approval time and authorization time;
- one approval decision can be reserved once;
- an approved plan cannot be silently replaced after any action starts;
- a partially executed plan cannot be rejected as though nothing happened;
- expiry prevents new actions but never causes an in-flight Amazon mutation to be retried;
- mismatch, failed or blocked actions complete the approval with issues.

## Structural hierarchy execution

Example:

```text
create Campaign (plan_key=campaign)
    -> Amazon Campaign ID C123
    -> bind C123 to logical Campaign
    -> render {{decision:campaign.entity_id}}
create Ad Group under C123 (plan_key=ad-group)
    -> Amazon Ad Group ID G456
    -> bind G456
    -> render later Target/Ad arguments
```

The approval stores templates, not guessed Amazon IDs. Runtime rendering can replace only a declared ID placeholder. All other names, budgets, bids, targeting expressions, products, states and tools remain byte-semantically represented by the approved canonical data.

A create Action that reports success without one unique created entity ID becomes `uncertain`; all dependencies stop. Black-box composite/bulk tools are not used to bypass this chain.

## Role authority

- **Main** may read, create bounded report jobs, plan, request approval, create tasks and delegate. It cannot execute Amazon Ads writes or approve its own plan.
- **Executor** is authoritative only when its exact Session is the task's current running Executor. It receives only current task decisions and rendered approved arguments.
- **Verifier** is authoritative only when its exact different Session is the task's current running Verifier.
- One running Executor and one running Verifier are allowed per task. A Session cannot be rebound across active tasks or roles.
- Different model families are preferred when the installed Hermes deployment supports reliable per-child routing, but different Session is the machine-enforced invariant.

## Hermes framework boundary

The plugin uses the pinned Hermes 0.18.2 public interfaces:

- 15 registered control tools;
- three Slash Commands;
- pre/post LLM hooks;
- pre/post tool hooks;
- Session start/end/finalize/reset hooks;
- subagent start/stop hooks;
- one namespaced Skill.

`pre_tool_call` is the last blocking boundary before Amazon MCP dispatch. `post_tool_call` stores the result or durable result envelope. A model fallback event changes the controller to `OBSERVE` and disables writes.

The 2C2G deployment uses one child at a time, one delegation level and no orchestrator child hierarchy.

## Data model

Base tables:

- `profiles`: marketplace/currency, enabled state and validated strategy overrides;
- `cycles`: source, window, data quality, KPIs and snapshot hash;
- `metric_rows`: normalized input rows;
- `decisions`: rule, evidence, exact payload, reservation, execution and verification state;
- `tasks`, `workers`: Main/Executor/Verifier lifecycle and authoritative Session IDs;
- `mcp_tools`: exact live contract and drift state;
- `actions`: before/after tool calls, redacted arguments and structured result evidence;
- `verifications`: expected/actual/differences and source read Action ID;
- `events`, `alerts`, `stream_events`: operations history and deduplicated Stream input.

Approval/Hermes tables:

- `approval_requests`: canonical plan, hash, risk, exposure, authority and lifecycle;
- `approval_decisions`: one-time per-decision consumption and outcome;
- `approval_events`: immutable approval history;
- `hermes_sessions`: model/provider/surface and Session lifecycle telemetry.

## Failure behavior

The following fail closed:

- control-plane outage or unavailable live Catalog;
- removed, unknown or unacknowledged-drift tool;
- malformed live-Schema arguments;
- wrong, stale or unbound role/Session;
- disabled Profile or controller mode;
- old/future decision, duplicate/cooldown or limit breach;
- multi-entity write;
- absent, changed, expired or already-consumed approval;
- undeclared/forward dependency;
- missing or ambiguous created Amazon ID;
- parameter or parent-ID tampering;
- unstructured/partial/unknown write result;
- missing, stale, wrong-family or sibling-object read evidence;
- verification mismatch;
- Outbox, storage or model-fallback safety trigger.

`PAUSED` blocks all Amazon Ads activity. `OBSERVE` permits collection and planning but no writes. `AUTOPILOT` permits routine writes and only those structural writes that also possess valid exact approval.
