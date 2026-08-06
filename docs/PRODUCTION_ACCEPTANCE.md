# Credentialed production acceptance

Keep the deployment in `OBSERVE` until every applicable item has dated evidence.

## Host and runtime

- [ ] Dedicated Linux user, protected environment, HTTPS and private control port.
- [ ] Database check, backup restore and real host reboot recovery pass.
- [ ] Hermes loads the plugin and all control tools/hooks.
- [ ] Main cannot write; one bound Executor writes and a different Session verifies.
- [ ] Model fallback, heartbeat loss, Outbox overflow or resource failure fails closed.
- [ ] The owner controls goals, pause and resume through Hermes without an approval UI.

## Amazon read acceptance

- [ ] OAuth and forced token refresh succeed.
- [ ] Authenticated MCP initialize and complete tools/list succeed.
- [ ] Profiles, regions, marketplaces, currencies and live schemas are correct.
- [ ] Reports submit, poll, download, decompress, validate and ingest.
- [ ] Attribution maturity, freshness and real 429 behavior are verified.
- [ ] Marketing Stream events arrive and deduplicate.

## Full-managed canaries

- [ ] A routine SP decision executes without requesting approval.
- [ ] A fresh read and Compare-And-Set precede the one-entity write.
- [ ] A different Session independently reads and verifies the result.
- [ ] Timeout, mismatch and restart scenarios do not duplicate mutations.
- [ ] A Campaign graph plan is released without a standing-authorization flag only when every action is inside the sealed envelope.
- [ ] Campaigns, Ad Groups, Product Ads, Targets and Keywords are all created PAUSED; Campaign names use `HERMES-SP-` and budget/create limits hold.
- [ ] Before creation, every delivery entity has one unambiguous enabled atomic activation tool that validates against the authenticated live JSON Schema.
- [ ] Missing IDs stop dependents without replay.
- [ ] The complete PAUSED graph is independently read back before any activation action is released.
- [ ] Product Ads, Targets and Keywords activate and verify before Ad Groups; Ad Groups activate and verify before Campaigns.
- [ ] A failed or ambiguous activation read keeps the Campaign PAUSED and emits one actionable exception.
- [ ] Final task completion occurs only after the Campaign ENABLED state is independently read back.
- [ ] Explicitly SP plans outside the envelope are rejected rather than routed to an approval queue.
- [ ] Billing, account, permission, irreversible, cross-region and composite writes remain blocked.

## Web acceptance

- [ ] Four KPIs, ACOS trend, AI activity and exception feed render.
- [ ] Only full-managed/observe/pause, target ACOS and per-Campaign budget limit are exposed.
- [ ] No approval queue, Payload Hash, confirmation phrase, worker table, MCP catalog or task workflow is visible.
- [ ] Chromium, Firefox and WebKit pass desktop and mobile tests without console errors.

## Release

- [ ] No critical alerts or schema drift.
- [ ] Routine and structural canaries are independently verified.
- [ ] The first complete attribution window is reviewed before widening scope.
