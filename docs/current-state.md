# Current state

Version 4.0 is an advertising-only, Sponsored Products sealed ACOS autopilot.

It combines:

- live Amazon Ads MCP tool and JSON Schema discovery through Hermes;
- deterministic Direct Amazon Ads API fallbacks for every required SP operation;
- delay-aware Bayesian CVR/AOV/ACOS posteriors instead of heuristic certainty;
- global budget reallocation, bounded hourly pacing and lifecycle quarantine/recovery;
- a Profile-bound Sponsored Products standing-authorization envelope for atomic Campaign, Ad Group, Product Ad, Target and reversible PAUSED/ENABLED maintenance;
- payload-bound human approval for every operation outside that standing envelope;
- atomic multi-step execution with created Amazon ID binding;
- Executor-only writes and different-session Verifier read-back;
- report lineage, Marketing Stream ingestion, recovery, audit and bounded storage;
- legacy Ads API contract drift checks plus Unified API GA/Beta separation checks.

Default deployment mode remains `observe` with execution disabled. After the owner explicitly enables `autopilot`, routine SP optimization writes and exact structural plans inside the sealed envelope can run without per-plan approval. Campaigns must be created PAUSED, within the `HERMES-SP-` namespace and conservative budget limits, then independently verified before ENABLED. Sponsored Brands, Sponsored Display, Sponsored TV, DSP, locale expansion and black-box MCP workflows remain outside sealed autonomy.

Billing, account administration, users, roles, permissions, unknown semantics, unacknowledged schema drift, irreversible delete/archive/remove operations and cross-region writes remain permanently blocked.

Repository sandbox evidence cannot fabricate advertiser-specific Amazon evidence. Final production activation still requires the owner OAuth token, authenticated MCP `tools/list`, per-Profile capability attestation, a Test Account or bounded live SP canary, independent Amazon read-back, a mature attribution-window shadow/canary review and the target VPS reboot/restore drill.
