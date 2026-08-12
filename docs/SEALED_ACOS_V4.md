# Hermes Amazon Ads Sealed ACOS Autopilot v4

## Scope

The sealed system optimizes **Amazon advertising-attributed ACOS only**. It does not ingest Seller Central, COGS, inventory, organic sales, refunds, Buy Box, pricing or promotion data. Sponsored Products is the only product with standing autonomous structural authority; SB, SD, STV and DSP remain Observe or exact operator-approved.

## Decision core

For every target, query, Campaign and placement, v4 estimates a delay-adjusted posterior for CVR, AOV, final attributed sales and ACOS. A reduction requires a configured posterior probability that final ACOS exceeds the maximum, while scaling requires a configured probability that final ACOS remains under target. Confidence never becomes 100% merely because a threshold number of clicks was reached.

The default attribution-completion curve is cumulative by click age and can be replaced with advertiser-specific Ads report backfill data. The controller blends maturity-corrected observed sales with a hierarchical prior instead of treating the most recent report as final.

## Autonomous controls

1. Target/keyword/product-target bid control.
2. Search-term negative and verified exact harvest.
3. Placement control with bounded changes.
4. Global Campaign budget transfer using posterior marginal value.
5. Hourly pacing from Marketing Stream/hourly rows with a separate intraday bid cap.
6. Reversible lifecycle quarantine and recovery.
7. Atomic SP Campaign graph creation under a standing authorization.

## Standing authorization envelope

The envelope is Profile-bound and Sponsored Products-only. Default limits are intentionally conservative and may be tightened per Profile:

- Campaign namespace: `HERMES-SP-`;
- create Campaign only in `PAUSED` state;
- maximum new Campaign daily budget: 50 account-currency units per Campaign;
- maximum aggregate new-Campaign budget: 100 per day;
- maximum two new Campaigns per day;
- Product Ads must reference an ASIN observed in trusted Amazon Ads evidence;
- state transitions are limited to `PAUSED` and `ENABLED`;
- ENABLED requires verified creation or verified recovery;
- every write remains one entity per call, exact-argument-bound and independently read back.

The envelope hash is included in every released decision. Any Profile policy change invalidates pending decisions and fails closed.

## Permanent denials

No setting or standing authorization can permit billing, invoices, payments, users, roles, permissions, invitations, account links, delete, archive, remove, purge, cross-region writes, unknown tools, drifted schemas or direct invocation of black-box composite MCP workflows.

## Transport

MCP is the preferred atomic interface after a live authenticated manifest attestation. Direct Ads API endpoints provide deterministic fallback for required SP operations. Unified API GA resources may be adopted after schema attestation; Unified Reports, Events, Rules, RuleLinks and Labels Beta remain Observe-only and cannot become a sole sealed dependency.

## Release gates

The repository release gate runs Python compilation, the complete unit/integration suite and branch coverage, focused v4 tests, MCP protocol/schema/authority checks, legacy and Unified Postman checks, policy/fingerprint checks, concurrency and recovery stress, Ruff, Bandit, JavaScript and secret checks, wheel/fresh-install/systemd/Nginx validation, Chromium/Firefox/WebKit E2E, real Hermes PluginManager compatibility and a full sandbox report artifact.

Live OAuth, real reports, Marketing Stream AWS delivery, real write/read-back, mature attribution-window canary and VPS recovery are explicitly reported as external acceptance evidence rather than falsely marked as repository tests.
