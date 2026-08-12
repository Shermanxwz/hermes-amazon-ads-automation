# Full-Managed Sealed Operation v6

## Contract

1. The optimization objective is Amazon advertising-attributed ACOS only.
2. Seller Central data is not ingested.
3. Hermes is the owner-facing control surface.
4. The Web is visualization plus emergency control only.
5. Sponsored Products operations inside the sealed envelope execute without per-plan approval.
6. Operations outside the envelope fail closed; no conversation can override permanent denials.
7. Every newly created delivery entity starts PAUSED and must complete independent read-back before activation.

## Normal cycle

Hermes synchronizes the live Amazon Ads catalog, advances persistent report jobs, validates lineage and maturity, runs the probabilistic ACOS controller, delegates one Executor, records the exact Amazon result, delegates a different read-only Verifier and finalizes only after independent read-back.

The owner is not involved in the normal cycle. Notifications are reserved for authentication loss, stale data, schema drift, throttling, budget conflicts, uncertain writes, verification mismatches, storage pressure or runtime failure.

## Closed-loop structural activation

Before accepting a full-managed create plan, the controller proves that every Campaign, Ad Group, Product Ad, Target or Keyword has one enabled, non-drifted, atomic update/enable tool whose live JSON Schema can express the exact entity ID and `ENABLED` state. If that activation path is missing or ambiguous, creation is rejected before a task exists.

The controller compiles paired activation decisions but holds them in a blocked state. All delivery entities are created `PAUSED`, unique Amazon IDs are bound from create responses, and a different read-only Verifier confirms the complete graph. Activation is then released in stages:

1. Product Ads, Targets and Keywords;
2. Ad Groups;
3. Campaigns last.

Every stage receives another independent read-back. A failed, missing, ambiguous, pending or uncertain verification prevents the next stage. Campaigns therefore remain PAUSED whenever the graph is not fully known. The final verified Campaign activation automatically finalizes the task.

## Web surface

The Web contains only spend, attributed sales, current and target ACOS, recent trend, AI actions, actionable exceptions, three runtime modes and two operational limits. Everything else remains an internal reliability mechanism or is available through Hermes conversation and audit storage.
