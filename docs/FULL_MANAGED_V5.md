# Full-Managed Sealed Operation v5

## Contract

1. The optimization objective is Amazon advertising-attributed ACOS only.
2. Seller Central data is not ingested.
3. Hermes is the owner-facing control surface.
4. The Web is visualization plus emergency control only.
5. Sponsored Products operations inside the sealed envelope execute without per-plan approval.
6. Operations outside the envelope fail closed; no conversation can override permanent denials.

## Normal cycle

Hermes synchronizes the live Amazon Ads catalog, advances persistent report jobs, validates lineage and maturity, runs the probabilistic ACOS controller, delegates one Executor, records the exact Amazon result, delegates a different read-only Verifier and finalizes only after independent read-back.

The owner is not involved in the normal cycle. Notifications are reserved for authentication loss, stale data, schema drift, throttling, budget conflicts, uncertain writes, verification mismatches, storage pressure or runtime failure.

## Web surface

The Web contains only spend, attributed sales, current and target ACOS, recent trend, AI actions, actionable exceptions, three runtime modes and two operational limits. Everything else remains an internal reliability mechanism or is available through Hermes conversation and audit storage.
