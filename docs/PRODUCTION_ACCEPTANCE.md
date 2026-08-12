# Credentialed production acceptance

Keep the deployment in `OBSERVE` until every applicable item has dated evidence. CI/sandbox evidence may prove code properties, but it must not be used to mark a live-account item complete.

## Host and runtime

- [ ] Dedicated `amazonbot` Linux user, protected server-side environment, HTTPS and private control port.
- [ ] Database integrity check, transactional/online backup restore and real host reboot recovery pass.
- [ ] Hermes loads the plugin and all control tools/hooks in the exact production Profile.
- [ ] Hermes Studio chat, interactive Hermes and the scheduled orchestrator select the same enabled Hermes base Home/Profile.
- [ ] `scripts/validate_hermes_studio.sh --live` passes against the production Hermes runtime and local control plane.
- [ ] `scripts/validate_hermes_studio.sh --studio-live` passes through the deployed Studio `/api/chat-run/runs` -> Agent Bridge -> `ads_control_status` tool path and records tool execution evidence.
- [ ] A named Profile, if used, resolves to `<base>/profiles/<name>` and the plugin is discovered from that exact Profile rather than silently falling back to default.
- [ ] Scheduled orchestration has no direct MCP/API token or SQLite mutation path and runs non-root with the declared resource limits.
- [ ] Main cannot write; one bound Executor writes and a different Session verifies.
- [ ] Model fallback, heartbeat loss during active work, Outbox overflow or resource failure fails closed.
- [ ] A genuinely idle Hermes Gateway remains ready after the initial plugin heartbeat and immediately re-arms heartbeat enforcement when work appears.
- [ ] The owner controls target ACOS, one daily maximum-spend cap, exploration share, pause and resume without an approval workflow for routine sealed SP operations.

## Privacy and repository

- [ ] Current production commit passes privacy/secret scanning with no real Profile ID, advertiser/account ID, credential, personal email or public host IP in source/generated text artifacts.
- [ ] Any previously exposed account identifier has been removed from active branches and affected public Git refs/history or the residual history risk is explicitly accepted and documented.
- [ ] No Amazon/Hermes/Studio credential or token fingerprint is printed by normal services.
- [ ] Public Web pages and screenshots do not display raw Profile/advertiser identifiers or internal credentials.

## Amazon read acceptance

- [ ] OAuth and forced token refresh succeed.
- [ ] Authenticated MCP initialize and complete paginated tools/list succeed.
- [ ] Profiles, regions, marketplaces, currencies and live schemas are correct.
- [ ] Reports submit, poll, download, decompress, validate and ingest without rewriting an already `INGESTED` source snapshot.
- [ ] Attribution maturity, freshness and real 429/Retry-After behavior are verified.
- [ ] Marketing Stream events arrive and deduplicate.
- [ ] Marketing Stream deployment has an explicit `ADS_STREAM_PROFILE_ID` fallback for messages that omit Profile ID; advertiser/account identifiers are not guessed into a Profile.
- [ ] Same-day Sponsored Products traffic `cost/spend` is continuously available and agrees materially with the exact-day report path after normal reporting delay.
- [ ] A full fresh Campaign query for the exact Profile yields complete parseable Campaign budget/state when a monetary create/budget/enable action needs it.

## Owner daily spend ceiling

- [ ] Owner-configured daily maximum ad spend persists across restart and is visible in the authenticated Web.
- [ ] The Web shows today spend / available amount / exploration available amount; it does not present a second “worst-case exposure” user budget.
- [ ] Owner-configured exploration share persists across restart and is visible in the authenticated Web.
- [ ] Spend-increasing actions are rejected when same-day SP spend evidence is absent, stale, wrong-Profile or unusable.
- [ ] Risk-reducing actions remain available when otherwise safe even if spend evidence is stale.
- [ ] Campaign create/budget/enable is rejected when the Campaign envelope read is absent, stale, wrong-Profile, paginated, ambiguous or incomplete.
- [ ] Active/future nominal Campaign daily budgets cannot be pushed above the Owner daily maximum.
- [ ] Concurrent in-flight reservations cannot oversubscribe the remaining Owner spend room.
- [ ] New exploration stops at the configured exploration-stop spend threshold.
- [ ] Normal spend-increasing actions stop at the configured conservative threshold.
- [ ] The absolute controller daily ceiling rejects every additional spend-increasing action.
- [ ] Amazon overdelivery math is confined to bounded temporary internal latency reserve and is not multiplied over every active Campaign as a second Owner-facing cap.
- [ ] Real account testing records any transient Amazon delivery overshoot so the controller ceiling is not misrepresented as a synchronous bank-account lock.

## Full-managed canaries

- [ ] A routine SP decision executes without requesting owner approval.
- [ ] A fresh entity read and Compare-And-Set precede mutable existing-entity writes.
- [ ] A different Session independently reads Amazon and verifies the result.
- [ ] Timeout, mismatch and restart scenarios do not duplicate mutations.
- [ ] A small `HERMES-SP-EXP-*` experiment can be created autonomously when mature performance history is weak but spend/safety state permits exploration.
- [ ] The same experiment is blocked when its projected spend/reservation exceeds the exploration share or daily maximum.
- [ ] A Campaign graph plan is released only when every action is inside the sealed envelope.
- [ ] Campaigns, Ad Groups, Product Ads, Targets and Keywords are all created PAUSED; Campaign names use the allowed `HERMES-SP-` namespace and budget/create limits hold.
- [ ] Before creation, every delivery entity has one unambiguous enabled atomic activation tool that validates against the authenticated live JSON Schema.
- [ ] Missing IDs stop dependents without replay.
- [ ] The complete PAUSED graph is independently read back before any activation action is released.
- [ ] Product Ads, Targets and Keywords activate and verify before Ad Groups; Ad Groups activate and verify before Campaigns.
- [ ] A failed or ambiguous activation read keeps the Campaign PAUSED and emits one actionable exception.
- [ ] Final task completion occurs only after the Campaign ENABLED state is independently read back.
- [ ] Explicitly SP plans outside the envelope are rejected rather than routed through a model-created bypass.
- [ ] Billing, account, permission, irreversible, cross-region and composite writes remain blocked.

## Web acceptance

- [ ] Four KPIs, ACOS trend, AI activity, exception feed and daily spend state render.
- [ ] Full-managed/observe/pause, target ACOS, daily maximum spend, exploration share and per-Campaign budget limit work.
- [ ] No raw Profile/advertiser ID, approval queue, Payload Hash, confirmation phrase, worker table, MCP catalog or task workflow is visible.
- [ ] Chromium, Firefox and WebKit pass desktop/mobile behavior without unexpected console/page errors.

## Release

- [ ] Package, control-plane `__version__`, plugin metadata, manifest, docs, Git tag and GitHub Release all identify `4.2.3`.
- [ ] Final release commit passes all CI jobs including runtime branch coverage >=80%, privacy/security, browser matrix, real Hermes PluginManager versions, Hermes Studio Profile/Agent Bridge contract, deployment/systemd, Amazon official contracts, stress/recovery and full-managed sandbox.
- [ ] The `v4.2.3` Tag points exactly at the CI-passing current `main` HEAD; an existing mismatched tag must fail closed and must never be force-moved by automation.
- [ ] The generic release workflow succeeds and superseded version-specific release workflows are absent from `main`.
- [ ] No critical alerts or live Schema drift.
- [ ] Routine, exploration and structural canaries are independently verified in the live Amazon account/Test Account.
- [ ] The first complete attribution window is reviewed before widening autonomous spend.

Only after every applicable live item above has dated evidence may the deployment be labeled **LIVE FULL-MANAGED PRODUCTION ACCEPTED**.
