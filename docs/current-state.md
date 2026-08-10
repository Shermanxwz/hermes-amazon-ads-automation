# Current state

Package release 4.2.1 implements sealed-operation/control-policy generation v6.1: an advertising-only, Sponsored Products full-managed ACOS autopilot with an owner-controlled account daily budget exposure hard cap.

The normal user path has no per-plan approval. Any exact SP plan inside the profile-bound sealed envelope is automatically released; the caller no longer supplies `standing_authorization`. An explicitly Sponsored Products plan that violates namespace, PAUSED-create, per-Campaign budget, ASIN, state, family, region or schema boundaries is rejected rather than downgraded into an approval request.

Commercial autonomy is budget-bounded rather than evidence-prohibited. Mature report evidence controls confidence, action size and scale-up. Weak history may justify a small reversible `HERMES-SP-EXP-*` experiment inside the exploration pool instead of forbidding learning. The owner configures target ACOS, account daily budget exposure hard cap, exploration share and per-Campaign daily budget limit from the authenticated Web surface.

The daily budget boundary is enforced by the controller, not by model instructions. Before an exposure-increasing write, a fresh full Amazon Campaign read for the exact Profile must provide a complete budget observation. At the exploration-stop threshold new experiments stop; at the conservative threshold positive exposure increases stop; at the hard cap only exposure-neutral or risk-reducing actions may continue.

Autonomous structural creation is closed-loop. Campaigns, Ad Groups, Product Ads, Targets and Keywords must all be created PAUSED. Before a create task is accepted, the controller must find one enabled, non-drifted, atomic activation tool whose live JSON Schema can express the exact Amazon entity ID and ENABLED state. After all created entities are independently read back, activation is released in stages: Product Ads/Targets/Keywords first, Ad Groups second and Campaigns last. Each stage is independently verified, and any mismatch keeps the remaining graph PAUSED. The task completes only after the final Campaign ENABLED state is read back successfully.

A definite create or activation failure immediately aborts every unreleased activation stage. A pending, partial, timeout or otherwise uncertain write enters `write_uncertain`, fails all remaining activation decisions and requires independent Amazon reconciliation; the controller never blindly retries the mutation or continues to Campaign activation.

Trust is controller-derived rather than caller-declared. Hermes or another JSON client cannot self-assert `verified_create`, `verified_recovery`, `observed_in_ads`, fresh budget evidence or an authorized ASIN list. Product Ad ASIN authority is derived from ingested Amazon Ads evidence for the same Profile, while staged activation authority is minted only by an in-process controller marker and removed before persistence.

The scheduled US orchestrator is only a Hermes one-shot trigger. It does not import the Amazon MCP client, handle Amazon access tokens, mutate SQLite evidence or impersonate the Hermes plugin. Interactive Hermes, Hermes Studio and scheduled runs must use the same enabled `amazon-ads-control` plugin/Profile so all paths cross the same pre/post-tool authorization and audit boundary.

The Web is a single visualization dashboard with four KPIs, ACOS trend, AI activity, exception feed, target ACOS, account daily budget, exploration share, per-Campaign budget cap and three emergency modes. Raw Profile/advertiser IDs, approval queues, Payload Hashes, worker sessions, MCP catalogs, task state machines and raw system logs are not part of the daily interface.

Internally the system still uses deterministic planning, atomic writes, Compare-And-Set, durable outcomes, different-session verification, audit, recovery and fail-closed readiness gates. Billing, account administration, users, roles, permissions, unknown semantics, schema drift, irreversible operations, cross-region writes and black-box composite workflows remain permanently blocked.

CI/sandbox acceptance is not a substitute for live production acceptance. Owner OAuth/live MCP, real report lifecycle/429 behavior, Marketing Stream, a bounded real/Test Account SP canary with different-session Amazon readback, attribution maturity, the target VPS and deployed Hermes Studio shared-profile path require dated external evidence before production is declared accepted.
