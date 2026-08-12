# Current State

Package release 4.2.3 implements **sealed-operation/control-policy generation v6.2**.

Repository status: **SEALED / PASS_WITH_EXTERNAL_ACCEPTANCE**.

## Runtime architecture

The active runtime is one composed control plane, not a collection of independent authorization paths:

1. Hermes / Hermes Studio is the owner-facing conversation and agent runtime.
2. `amazon-ads-control` is the only controller boundary for policy, task/session binding, spend authorization, execution and verification.
3. Amazon Ads MCP live schemas are the runtime capability source of truth; Direct Ads API fallback remains deterministic and Profile/region bound.
4. Main plans but cannot write. Exactly one bound Executor may consume atomic reservations. A different Verifier session performs Amazon read-back.
5. Daily systemd orchestration only starts Hermes and has no direct MCP/API/SQLite mutation authority.
6. Marketing Stream relay only normalizes/dedupes Profile-bound events into the control plane and has no write authority.

## Owner daily spend semantics

`max_daily_ad_spend` is the **single Owner commercial budget authorization**. It is not a nominal Campaign-budget value that the UI silently halves via a blanket Amazon overdelivery multiplier.

Before a spend-increasing write:

- the controller requires fresh same-day Sponsored Products spend evidence for the exact Profile;
- Marketing Stream SP traffic is preferred; an exact-day lineaged report is a fallback;
- stale or absent spend evidence blocks increases but never blocks otherwise-safe risk reductions;
- Campaign create/budget/enable also requires a fresh complete unpaginated Campaign observation;
- active/future nominal Campaign daily budgets cannot be pushed over the Owner cap;
- outstanding writes use small, short-lived atomic reserves to prevent concurrency oversubscription;
- platform overdelivery behavior only bounds a temporary internal latency cushion and is not exposed as a second Owner budget.

Default thresholds remain 80% exploration stop, 90% conservative no-increase mode and 100% controller ceiling.

Because Amazon delivery and telemetry are asynchronous, this is a controller execution ceiling rather than a mathematical guarantee that Amazon billing can never transiently cross the value by a cent. Live acceptance must measure real Stream/report latency.

## Budget reservation ownership

The duplicated compatibility reservation state machine has been removed from extension composition. `closed_loop` remains the canonical approval/CAS/entity-cooldown reservation owner. `budget_reservation` is only the final financial wrapper: it serializes the same-day spend precheck, then delegates to the canonical owner. There is no second reservation state machine.

## Hermes Studio

Studio, CLI and scheduled orchestration share the selected Hermes base/Profile and the same `amazon-ads-control` plugin boundary. CI validates default/named Profile layout plus the Studio Profile/PluginManager/Agent Bridge contract. Deployed `scripts/validate_hermes_studio.sh --live` remains an external production acceptance item.

## Amazon official contracts

Release gates continue to use:

- Amazon Ads official Postman collection;
- Amazon Ads Unified API collection with GA/Beta separation;
- authenticated/live Amazon Advertising AI MCP schema discovery at production acceptance.

Marketing Stream deployment now requires explicit `ADS_STREAM_PROFILE_ID` fallback for events that do not carry Profile ID. Advertiser/account IDs are never guessed into a Profile boundary.

## Repository gates

GitHub CI requires:

- Python 3.11 / 3.12 / 3.13 unit + branch coverage with minimum 80%;
- Ruff/Bandit/privacy checks;
- Amazon official contract checks;
- real Hermes compatibility;
- Hermes Studio contract/Profile checks;
- Chromium/Firefox/WebKit;
- deployment/systemd validation;
- stress/recovery;
- full-managed sealed sandbox.

The generic release workflow derives the current package version and refuses to move an existing tag. A release is eligible only when successful CI belongs to the current `main` HEAD and all release identities match.

## External production acceptance still required

Repository evidence cannot fabricate owner/Amazon/VPS proof. LIVE FULL-MANAGED PRODUCTION remains **NOT ACCEPTED** until dated evidence exists for OAuth refresh recovery, authenticated live MCP, real report lifecycle, real 429 behavior, Marketing Stream delivery/Profile binding/spend continuity, controlled SP write canary and independent Amazon read-back, mature attribution review, deployed Hermes Studio chat-run, HTTPS, and target 2C2G VPS reboot/backup/restore behavior.
