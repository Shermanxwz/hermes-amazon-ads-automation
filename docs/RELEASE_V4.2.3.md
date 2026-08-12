# Hermes Amazon Ads Control v4.2.3 — Owner Daily Spend Sealed Autonomy

v4.2.3 seals the current framework around one product-level budget meaning: **the Owner daily maximum ad spend is the only commercial spend authorization**.

## Budget semantics repaired

Previous v4.2.x code conservatively multiplied all active Campaign daily budgets by Amazon's high-traffic-day overdelivery factor and surfaced that value as “worst-case daily spend exposure”. That internal risk model was safe but product-semantically wrong: it effectively created a second budget and could make an Owner cap of 30 behave like only ~15 of nominal Campaign budget.

v4.2.3 removes that abstraction from the Owner surface and execution base:

- same-day SP spend is measured from Profile-bound Marketing Stream traffic, with an exact-day lineaged report fallback;
- stale/missing spend evidence blocks increases;
- Campaign monetary mutations still require a fresh complete Campaign envelope and cannot push active/future nominal daily budgets above the Owner cap;
- short-lived in-flight reservations protect concurrency;
- Amazon overdelivery remains only a bounded internal latency cushion, not a blanket multiplier over all Campaigns;
- 80/90/100 spend thresholds remain fail-closed;
- risk reductions remain available when otherwise safe.

The UI now shows **today's ad spend, today's available amount, AI exploration available amount and spend-data status**. It no longer presents a second “worst-case exposure” concept.

## Reservation architecture sealed

The temporary `budget_reservation_compat` composition layer is removed. The canonical `closed_loop` approval/CAS/cooldown reservation state machine remains the only owner. `budget_reservation` adds one serialized financial precheck and then delegates to that owner.

## Marketing Stream hardening

- explicit `ADS_STREAM_PROFILE_ID` fallback when Amazon stream records omit Profile ID;
- no guessing from advertiser/account IDs;
- Amazon idempotency identifiers preferred for dedupe;
- date/hour fallback for event time;
- only same-day Sponsored Products traffic cost/spend is eligible for the Owner spend meter.

## Existing sealed controls retained

- Sponsored Products budget-bounded full-managed autonomy;
- Main / Executor / different-session Verifier separation;
- atomic one-entity writes, prewrite CAS and independent Amazon readback;
- PAUSED-first structural creation and staged activation;
- billing/account/delete/archive/unknown/drift/composite blocks;
- Hermes Studio shared Profile and Agent Bridge integration;
- Daily Orchestrator through Hermes rather than a bypass;
- Amazon official Postman / Unified API / MCP contract gates;
- privacy scanning, 80% branch coverage, browser matrix, stress/recovery and full sandbox.

## Release integrity

v4.2.3 uses a generic post-CI release workflow. It derives the package version, proves the successful CI SHA is still current `main`, checks all package/plugin identities, privacy and Hermes Studio contracts, and refuses to move an existing tag.

## Production boundary

Code/repository sealing is not owner-credentialed live acceptance. Real OAuth, authenticated MCP, report lifecycle, Marketing Stream delivery and spend continuity, controlled SP canary, independent Amazon readback, attribution maturity, deployed Studio chat-run, HTTPS and 2C2G reboot/backup/restore evidence remain required before declaring LIVE FULL-MANAGED PRODUCTION ACCEPTED.
