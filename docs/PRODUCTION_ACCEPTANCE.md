# Credentialed production acceptance

Keep the deployment in `OBSERVE` until every applicable item has dated evidence. CI/sandbox evidence may prove code properties, but it must not be used to mark a live-account item complete.

## Host and runtime

- [ ] Dedicated `amazonbot` Linux user, protected server-side environment, HTTPS and private control port.
- [ ] Database integrity check, transactional/online backup restore and real host reboot recovery pass.
- [ ] Hermes loads the plugin and all control tools/hooks in the exact production Profile.
- [ ] Hermes Studio chat, interactive Hermes and the scheduled orchestrator select the same enabled Hermes Profile/Home.
- [ ] `scripts/validate_hermes_studio.sh --live` passes against the deployed Studio/Hermes runtime and local control plane.
- [ ] Scheduled orchestration has no direct MCP/API token or SQLite mutation path and runs non-root with the declared resource limits.
- [ ] Main cannot write; one bound Executor writes and a different Session verifies.
- [ ] Model fallback, heartbeat loss, Outbox overflow or resource failure fails closed.
- [ ] The owner controls target ACOS, daily hard budget, exploration share, pause and resume without an approval workflow for routine sealed SP operations.

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
- [ ] A full fresh Campaign query for the exact Profile yields complete parseable Campaign budget state for the daily exposure guard.

## Daily budget envelope

- [ ] Owner-configured daily total hard cap persists across restart and is visible in the authenticated Web.
- [ ] Owner-configured exploration share persists across restart and is visible in the authenticated Web.
- [ ] A positive exposure action is rejected when the Campaign budget read is absent, stale, wrong-Profile, ambiguous or incomplete.
- [ ] New exploration stops at the configured exploration-stop utilization threshold.
- [ ] Normal positive exposure increases stop at the configured conservative threshold.
- [ ] The absolute daily hard cap rejects every additional positive Campaign-budget exposure action.
- [ ] Exposure-neutral and risk-reducing actions remain available at high utilization when all other safety gates pass.
- [ ] A newer complete Campaign read absorbs earlier executed/verified budget deltas without double counting; unresolved reserved/pending/uncertain deltas still reserve room.

## Full-managed canaries

- [ ] A routine SP decision executes without requesting owner approval.
- [ ] A fresh entity read and Compare-And-Set precede mutable existing-entity writes.
- [ ] A different Session independently reads Amazon and verifies the result.
- [ ] Timeout, mismatch and restart scenarios do not duplicate mutations.
- [ ] A small `HERMES-SP-EXP-*` experiment can be created autonomously when mature performance history is weak but budget/safety state permits exploration.
- [ ] The same experiment is blocked when its projected loss/exposure exceeds the exploration pool or daily hard cap.
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

- [ ] Four KPIs, ACOS trend, AI activity, exception feed and budget safety state render.
- [ ] Full-managed/observe/pause, target ACOS, daily total hard cap, exploration share and per-Campaign budget limit work.
- [ ] No raw Profile/advertiser ID, approval queue, Payload Hash, confirmation phrase, worker table, MCP catalog or task workflow is visible.
- [ ] Chromium, Firefox and WebKit pass desktop/mobile behavior without unexpected console/page errors.

## Release

- [ ] Package, control-plane `__version__`, plugin metadata, manifest, docs, Git tag and GitHub Release all identify the same release.
- [ ] Final release commit passes all CI jobs including runtime branch coverage >=80%, privacy/security, browser matrix, real Hermes PluginManager versions, deployment/systemd, official contracts, stress/recovery and full-managed sandbox.
- [ ] No critical alerts or live Schema drift.
- [ ] Routine, exploration and structural canaries are independently verified in the live Amazon account/Test Account.
- [ ] The first complete attribution window is reviewed before widening autonomous exposure.

Only after every applicable live item above has dated evidence may the deployment be labeled **LIVE FULL-MANAGED PRODUCTION ACCEPTED**.
