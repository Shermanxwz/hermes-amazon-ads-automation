# Current state

Version 3.2 is an advertising-only, approval-gated Hermes full autopilot.

It combines:

- live Amazon Ads MCP tool and JSON Schema discovery through Hermes;
- deterministic sponsored-ads optimization for routine operations;
- payload-bound human approval for Campaign and other structural/high-risk plans;
- atomic multi-step execution with created Amazon ID binding;
- Executor-only writes and different-session Verifier read-back;
- report lineage, Marketing Stream ingestion, recovery, audit and bounded storage;
- an authenticated Web approval and operations surface.

Default mode is `observe` and execution is disabled. Routine writes become autonomous only in `autopilot`. Structural/high-risk writes additionally require an unexpired exact approval. Billing, account administration, unknown semantics, unacknowledged schema drift, irreversible deletes and black-box composite/bulk mutations remain permanently blocked.

Repository engineering cannot fabricate advertiser-specific production evidence. Production acceptance still requires the owner’s OAuth, authenticated MCP catalog, real report lifecycle, Test Account or bounded real-profile Campaign-create canary, independent Amazon read-back, attribution-window observation and VPS recovery drills.
