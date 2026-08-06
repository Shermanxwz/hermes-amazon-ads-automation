# Current state

Version 5.0 is an advertising-only, Sponsored Products full-managed ACOS autopilot.

The normal user path has no per-plan approval. Any exact SP plan inside the profile-bound sealed envelope is automatically released; the caller no longer supplies `standing_authorization`. An explicitly Sponsored Products plan that violates namespace, PAUSED-create, budget, ASIN, state, family, region or schema boundaries is rejected rather than downgraded into an approval request.

The Web is now a single visualization dashboard with four KPIs, ACOS trend, AI activity, exception feed, target ACOS, per-Campaign budget cap and three emergency modes. Approval queues, Payload Hashes, worker sessions, MCP catalogs, task state machines and raw system logs are no longer part of the daily interface.

Hermes remains the operating surface. Internally the system still uses deterministic planning, atomic writes, Compare-And-Set, durable outcomes, different-session verification, audit, recovery and fail-closed readiness gates.

Billing, account administration, users, roles, permissions, unknown semantics, schema drift, irreversible operations, cross-region writes and black-box composite workflows remain permanently blocked.
