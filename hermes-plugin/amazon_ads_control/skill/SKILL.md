# Amazon Ads Approval-Gated Full Autopilot v3.2

Operate Amazon Ads as an ads-only, approval-gated full-management system. Amazon's official MCP provides live capabilities; the local control plane is the final authority for report lineage, deterministic strategy, structural planning, authorization, execution, verification, recovery and audit.

Do not use Seller Central browser automation. Do not invent cost, profit, inventory, refund, organic-sales, pricing or creative data. Do not ask for routine per-action approval. High-risk structural work must use the payload-bound approval workflow below.

## Authority model

There are three permission classes:

1. **Routine autonomous** — mature, deterministic and bounded bid, budget, placement, negative, harvest and eligible state operations proceed without user interruption.
2. **Operator-approved autonomous** — Campaign, Ad Group, Target, Keyword, Ad, Portfolio and other structural/high-risk plans may execute only after the user approves the exact payload hash.
3. **Permanently blocked** — billing/payment, users/roles/permissions, account administration, unknown semantics, unacknowledged schema drift and irreversible delete operations cannot be approved by the AI or the user command channel.

Approval is not a natural-language promise. The AI can request and explain approval, but it cannot call the operator approval endpoint. Only the authenticated Web or the explicit Hermes `/ads-approve` command can approve the exact stored payload.

## Non-negotiable routine sequence

1. Call `ads_control_status` and `ads_control_sync_catalog`.
2. Respect the reported resource profile. A constrained 2C2G host runs one Profile/report/child at a time; safety and strategy never weaken.
3. Create or recover one stable report transaction with `ads_control_create_report_job`.
4. Call the exact Amazon MCP report tools required to submit, poll and download the report.
5. Call `ads_control_report_evidence` and cite the recorded `evidence_action_id` for Amazon-driven transitions: `SUBMITTED`, `IN_PROGRESS`, `SUCCEEDED`, `DOWNLOADED`.
6. Use `ads_control_transition_report` for every state change. The controller verifies same-session structured report evidence and the persistent report ID.
7. Normalize downloaded data without fabrication. Transition to `VALIDATED` with the complete normalized snapshot. The controller computes all hashes.
8. Transition to `INGESTED`. Only the exact controller-stored normalized snapshot may be sent to `ads_control_plan_cycle` with its report lineage.
9. Create a task from the lineage-backed cycle.
10. For routine mutable bid, budget, placement or state writes, delegate a bound Executor. Before every write the Executor performs a fresh exact Amazon read and calls `ads_control_prepare_write`; the current value must equal the planned `before` value.
11. The Executor calls exactly one narrow planned Amazon write. Never retry an uncertain mutation. Durable callback delivery may retry only the original result envelope.
12. Stop the Executor, then delegate a different bound read-only Verifier session.
13. The Verifier performs a fresh exact Amazon read, calls `ads_control_read_evidence`, then `ads_control_verify_decision` with the recorded action ID.
14. Finalize only after every decision is verified or explicitly recorded as an issue.

A cycle without report lineage cannot create a routine optimization task. A report state without required Amazon action evidence cannot advance. A callback without an exact event ID, reservation token and result hash cannot commit.

## High-risk structural plan sequence

Use this path for Campaign creation, Ad Group/Target/Keyword/Ad creation, important pauses/archives, Portfolio restructuring, large structural changes, approved recommendations, market expansion or an MCP workflow classified high/critical.

1. Synchronize the live MCP catalog immediately before planning.
2. Read all Amazon entities and constraints needed to construct the plan. Never infer existing IDs, states, budgets, eligibility or product associations.
3. Prefer narrow atomic MCP tools. Use a composite workflow only when its complete live-schema payload, effects and verification targets are explicit.
4. Call `ads_control_create_managed_plan` with:
   - exact `profile`;
   - exact registered live `tool_name` for every action;
   - complete live-schema-valid `arguments`;
   - an `expected_state` that a different Verifier can independently read;
   - maximum daily budget exposure where applicable;
   - evidence and a human-readable reason.
5. The controller stores a canonical plan, hashes every action argument and creates one `awaiting_approval` task.
6. Present the user with:
   - Profile and marketplace;
   - every action and tool;
   - Campaign/Ad Group/Target/Ad structure;
   - daily and aggregate budget exposure;
   - irreversible or disabling effects;
   - approval expiry;
   - full payload hash and the exact `/ads-approve <approval_id> <hash-prefix>` command.
7. Stop. Do not delegate an Executor and do not interpret ordinary replies such as “可以” or “执行” as authorization.
8. After `ads_control_status` shows the approval as `approved`, delegate the Executor with the task and role markers.
9. The Executor must submit arguments byte-semantically equivalent to the approved canonical payload. Any field, ID, budget, bid, target, name or tool change invalidates authorization.
10. Each approval decision is consumed once. Uncertain mutation results are never replayed.
11. Delegate a different read-only Verifier session and verify every expected entity/state independently.
12. For multi-step creation, execute dependency order and stop on an unverified prerequisite. Newly returned Amazon IDs must be read back and used only through a new exact dependent decision when the approved plan did not already contain them.

## Campaign creation contract

A complete Campaign launch plan should, where the available Amazon MCP tools require it, cover:

- Campaign with exact name, ad product, targeting type, bidding strategy, state and daily budget;
- Ad Group with exact default bid and state;
- Keywords or Targets with match expressions and individual bids;
- Product Ads or creative associations with explicit advertised product identifiers;
- optional negative targets and placement settings;
- independent read-back fields for every created object.

Do not hide multiple unrelated campaigns in one action. The controller's autonomous Amazon write batch remains one entity. A user may approve a multi-action plan once, but the Executor executes and the Verifier checks the actions separately.

## Approval commands

- `/ads-approvals` lists pending plans and hashes.
- `/ads-approve <approval_id> <payload_hash-prefix>` approves only the exact stored plan.
- `/ads-reject <approval_id> <reason>` rejects the plan.

These commands use a separate operator credential. They are not model-callable tools. When commands are unavailable on a Hermes surface, direct the user to the authenticated local control Web approval panel.

## Role boundaries

### Main

- Reads, records report evidence, creates cycles/tasks and delegates.
- Builds exact structural plans from live MCP schemas.
- May request approval but can never approve.
- Never calls an Amazon Ads write tool.
- Keeps Profile data isolated.
- Stops only the affected Profile on OAuth, Profile, schema, report or repeated-throttling failure.

### Executor

- Receives only its bound task and deterministic decisions.
- Uses the narrowest live-schema-compatible tool.
- For routine mutable fields, re-reads the exact entity and prepares Compare-And-Set evidence.
- For structural actions, sends only the exact approved argument payload.
- Sends one entity/action at a time.
- Never self-verifies.
- Does not retry an uncertain mutation.

### Verifier

- Uses a different current Hermes session and remains read-only.
- A different model is preferred when the installed Hermes delegation implementation supports reliable per-child routing, but different-session isolation is always mandatory.
- Does not trust write responses or Executor summaries.
- Selects one exact entity object; fields from sibling objects cannot satisfy verification.
- Verifies every expected field and records mismatch/not-found as an issue.

## Report lifecycle

The persistent lifecycle is:

`REQUESTED -> SUBMITTED -> IN_PROGRESS -> SUCCEEDED -> DOWNLOADED -> VALIDATED -> INGESTED`

Failure states are `FAILED` and `QUARANTINED`.

- Build a stable key from Profile, report type, columns, filters, dates and time zone.
- Reuse the existing transaction for the same key.
- A failed or quarantined key may restart only through explicit retry; history remains auditable.
- Preserve Amazon's report ID exactly.
- Poll with bounded backoff and honor retry hints.
- Never treat submitted/in-progress reports as data.
- Validate content, decompression, schema, Profile and requested window.
- Stream/chunk large payloads using `runtime_resources.report_stream_chunk_rows`.
- Attribution maturity remains separate from report completion.

## Normalized snapshot contract

Submit only values actually returned by Amazon. Required top-level structure:

```json
{
  "source": "amazon-ads-mcp",
  "profile": {
    "profile_id": "...",
    "name": "...",
    "marketplace": "US",
    "country_code": "US",
    "currency": "USD"
  },
  "window": {
    "start": "YYYY-MM-DD",
    "end": "YYYY-MM-DD",
    "days": 14,
    "grain": "daily"
  },
  "account": {
    "impressions": 0,
    "clicks": 0,
    "spend": 0,
    "sales": 0,
    "orders": 0
  },
  "campaigns": [],
  "targets": [],
  "search_terms": [],
  "placements": [],
  "budget_usage": [],
  "recommendations": [],
  "hourly": []
}
```

Missing required metrics reject the row. Invalid values never become zero. Preserve every Amazon identifier exactly.

## Runtime and interaction behavior

- The plugin feature-detects Hermes command registration. CLI/gateway surfaces use commands when supported; Web approval always remains available.
- Session start/active/reset/end hooks are recorded when emitted by Hermes.
- Tool pre-hooks fail closed when the controller, live catalog or durable result outbox is unavailable.
- Tool post-hooks persist the exact result envelope; an Amazon mutation is never replayed to repair callback delivery.
- Delegation requires both `[ads-task:<id>]` and `[ads-role:executor|verifier]` markers.
- `max_spawn_depth` remains one; subagents cannot create an uncontrolled hierarchy.
- On a 2C2G VPS, set `max_concurrent_children: 1`; Executor and Verifier run sequentially.
- An unexpected/untrusted model fallback should force observe-only behavior until the operator reviews it.

## Permanently blocked boundary

Never request approval for:

- billing, payment or invoices;
- users, roles, permissions, invitations or account links;
- account deletion or Profile deletion;
- any tool with unknown semantics;
- any tool with unacknowledged live-schema drift;
- irreversible delete operations;
- a black-box operation whose exact effects and independent verification targets are unavailable.

## Final reporting

Produce a concise Chinese report containing:

- Profile, marketplace and mature window;
- KPI and data-quality result;
- report lifecycle and lineage status;
- deterministic rules and routine changes;
- pending/approved/rejected/expired structural plans and payload hashes;
- executed structural actions and budget exposure;
- Compare-And-Set and independent verification results;
- blocked, failed, uncertain or quarantined items;
- callback/outbox, Hermes lifecycle and resource status;
- alerts and the next automatic cycle.
