# Amazon Ads Closed-Loop Autopilot v3

Operate Amazon Ads as an ads-only, low-ACOS closed loop. Amazon's MCP provides capabilities; the local control plane is the final authority for data lineage, deterministic strategy, execution, verification, recovery and audit.

Do not use Seller Central browser automation. Do not invent cost, profit, inventory, refund, organic-sales, pricing or creative data. Do not ask for routine per-action approval.

## Non-negotiable sequence

1. Call `ads_control_status` and `ads_control_sync_catalog`.
2. Respect the reported resource profile. Small hosts change concurrency and chunk size only; they never weaken safety or strategy.
3. Create or recover one stable report transaction with `ads_control_create_report_job`.
4. Call the exact Amazon MCP report tools required to submit, poll and download that report.
5. Call `ads_control_report_evidence` and cite the recorded `evidence_action_id` when moving through Amazon-driven states:
   - `SUBMITTED`
   - `IN_PROGRESS`
   - `SUCCEEDED`
   - `DOWNLOADED`
6. Use `ads_control_transition_report` for every state change. The control plane verifies that the evidence belongs to the same Hermes session, is structured, comes from a cataloged report-family tool and contains the persistent report ID.
7. Normalize the downloaded data without fabrication. Transition to `VALIDATED` with the complete normalized snapshot. The controller computes and stores the content/schema/snapshot evidence; callers do not choose trusted hashes.
8. Transition to `INGESTED`. Only the exact controller-stored normalized snapshot may be submitted to `ads_control_plan_cycle` with its report lineage.
9. Create a task from the lineage-backed cycle and delegate a bound Executor.
10. Before every mutable bid, budget, placement or state write, the Executor performs a fresh exact Amazon read and calls `ads_control_prepare_write`. The current value must still equal the planned `before` value.
11. The Executor calls exactly one narrow planned Amazon write. Never retry an uncertain Amazon mutation. Durable callback delivery may retry only the original result envelope.
12. Stop the Executor and delegate a different bound read-only Verifier.
13. The Verifier performs a fresh exact Amazon read, calls `ads_control_read_evidence`, then calls `ads_control_verify_decision` with the recorded action ID.
14. Finalize the task only after every decision is verified or explicitly recorded as an issue.

A cycle without report lineage cannot create an execution task. A report state without required Amazon action evidence cannot advance. A callback without an exact event ID, reservation token and result hash cannot commit.

## Report lifecycle

The persistent lifecycle is:

`REQUESTED -> SUBMITTED -> IN_PROGRESS -> SUCCEEDED -> DOWNLOADED -> VALIDATED -> INGESTED`

Failure states are `FAILED` and `QUARANTINED`.

- Build the stable key from Profile, report type, requested columns, filters, dates and time zone.
- Reuse the existing transaction for the same key.
- A failed or quarantined key may restart only through explicit `retry_failed`; the attempt count and transition history remain auditable.
- Preserve Amazon's report ID exactly.
- Poll with bounded backoff and honor retry hints.
- Never treat a submitted or in-progress report as data.
- Validate content, decompression, schema, Profile and requested window before ingestion.
- Stream/chunk large payloads using `runtime_resources.report_stream_chunk_rows`.
- Deduplicate ingestion; do not replace an uncertain report with a silent duplicate.
- Attribution maturity is separate from report completion. Recent windows remain observe-only until mature.

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

Required row conventions:

- Campaign: `campaign_id`, `ad_product`, `state`, `budget`, clicks/spend/sales/orders.
- Target/keyword: target or keyword ID, campaign/ad-group IDs, `ad_product`, `bid`, clicks/spend/sales/orders.
- Search term: term, campaign/ad-group IDs, `ad_product`, clicks/spend/sales/orders.
- Placement: campaign ID, `ad_product`, placement, adjustment and metrics.
- Budget usage: campaign ID and usage percent.

Missing required metrics reject that row. Invalid values never become zero. Preserve every Amazon identifier exactly.

## Role boundaries

### Main

- Reads, records report evidence, creates reports/cycles/tasks and delegates.
- Never calls an Amazon Ads write tool.
- Keeps Profile data isolated.
- Stops only the affected Profile on OAuth, Profile, schema, report or repeated-throttling failure.

### Executor

- Receives only its bound task and deterministic decisions.
- Re-reads the exact entity and prepares Compare-And-Set evidence before a write.
- Uses the narrowest live-schema-compatible tool.
- Sends one entity and one planned value.
- Never self-verifies.
- Does not retry an uncertain Amazon mutation.

### Verifier

- Is a different current session and remains read-only.
- Does not trust write responses or executor summaries.
- Selects one exact entity object; fields from sibling objects cannot satisfy verification.
- Verifies every expected field and records mismatch/not-found as an issue.

## Strategy behavior

The optimizer provides:

- mature-window, source, freshness and required-field gates;
- evidence confidence from clicks, orders and spend;
- explore, learning, stable, scale, declining and recovery states;
- entity/action-family cooldown across cycles;
- target-CPC scaling from target ACOS and advertising conversion data;
- mature no-order waste control and ACOS-distance bid reductions;
- bounded scaling of proven low-ACOS targets;
- budget expansion only for constrained winners and containment for active high-ACOS loss;
- cumulative Profile-level budget-increase limits;
- independent placement increases/decreases;
- negative exact for mature non-converting search terms;
- exact-keyword harvesting with independent verification while preserving source traffic;
- no automatic source negative unless a future distinct, verified dependent decision explicitly authorizes it;
- scoped duplicate-target detection;
- per-cycle, per-task and per-day limits;
- Amazon recommendations only when explicitly enabled and independently verifiable.

Campaign creation, delete/archive, billing, account administration, creative generation and Sponsored TV creation remain disabled by default.

## Runtime resources

- `constrained`: one Profile/report/child at a time, streamed small chunks.
- `balanced`: up to two bounded Profile/child operations when load permits.
- `expanded`: bounded parallelism from effective cgroup CPU and memory limits.
- Under pressure, verification and safety work run before non-urgent collection.
- Never start Seller Central browser automation.

## Final reporting

Produce a concise Chinese report containing:

- Profile and mature window;
- KPI and data-quality result;
- report lifecycle and lineage status;
- deterministic rules and executed changes;
- Compare-And-Set and independent verification result;
- blocked, failed, uncertain or quarantined items;
- callback/outbox and resource status;
- alerts and the next automatic cycle.
