# Credentialed production acceptance

Keep the PR and deployment in `OBSERVE` until every applicable item has dated evidence.

## Environment and credential isolation

- [ ] Dedicated Linux user, repository and virtual environment installed.
- [ ] `/etc/hermes-amazon-ads-control.env` is root-readable only.
- [ ] `ADS_CONTROL_AGENT_TOKEN` and `ADS_CONTROL_OPERATOR_TOKEN` are different random values.
- [ ] Normal Hermes processes receive only `ADS_CONTROL_AGENT_TOKEN`.
- [ ] `ADS_CONTROL_ENABLE_COMMAND_APPROVAL` is absent/false unless the Gateway has no terminal, file or environment-reading capability.
- [ ] SQLite directory is writable only by the service account.
- [ ] Nginx/Caddy HTTPS works; port 8790 is not public.
- [ ] Web login, Origin and CSRF rejection are tested from the deployed domain.
- [ ] `amazon-ads-control --check` reports a healthy full database check.
- [ ] Backup is created and restored into a temporary service.
- [ ] Service and Hermes gateway recover after a real host reboot.

## Hermes framework acceptance

- [ ] Deployed version is `hermes-agent==0.18.2` or an explicitly reviewed compatible version.
- [ ] `hermes plugins list` shows `amazon-ads-control` enabled without load errors.
- [ ] All 15 `ads_control_*` tools are registered with schemas.
- [ ] `pre_llm_call` injects mode, role, task, catalog, reports, resources and pending approvals.
- [ ] `pre_tool_call` blocks an unplanned Amazon write before MCP dispatch.
- [ ] `post_tool_call` persists the exact result or durable Outbox envelope.
- [ ] Session start/active/reset/end events are visible in the control audit.
- [ ] `subagent_start` binds Executor/Verifier from task and role markers.
- [ ] `subagent_stop` closes the correct worker session.
- [ ] Executor and Verifier are different Hermes Sessions.
- [ ] `max_concurrent_children: 1`, `max_spawn_depth: 1` and `orchestrator_enabled: false` are effective on the 2C2G host.
- [ ] A reported model fallback changes the controller to `OBSERVE` and disables writes.
- [ ] Slash commands list correctly on every required CLI/Gateway surface, or Web-only approval is documented for that surface.

## Amazon and Hermes read acceptance

- [ ] OAuth completes and a forced token refresh succeeds.
- [ ] Authenticated MCP `initialize` and `tools/list` succeed.
- [ ] Expected Profiles, marketplaces and currencies are visible.
- [ ] `hermes mcp test amazon-ads` succeeds.
- [ ] Live MCP catalog contains expected read/write/job tools with no unreviewed drift.
- [ ] Reports can be created, polled, downloaded, decompressed and parsed for every enabled ad product.
- [ ] Attribution maturity and data-freshness gates behave correctly on real reports.
- [ ] A real 429/retry hint is honored without duplicating a job or mutation.
- [ ] Marketing Stream events arrive, deduplicate and produce budget/eligibility alerts.

## Strategy acceptance

- [ ] Export historical snapshots into replay format and run `amazon-ads-backtest`.
- [ ] Review every rule family across product type, marketplace and lifecycle stage.
- [ ] Run at least one full attribution window in `OBSERVE` and inspect false positives.
- [ ] Define account-specific target ACOS, maximum ACOS and absolute operational boundaries.
- [ ] Confirm decisions do not use incomplete, future, duplicate or stale rows.

Replay and shadow evidence validate consistency and operator acceptability; they do not establish causal incremental lift.

## Routine canary acceptance

Prefer an Amazon Ads Test Account. Otherwise choose one low-spend entity with an easily reversible change.

- [ ] Main creates exactly one deterministic routine decision.
- [ ] A bound Executor reserves and executes exactly that entity and value.
- [ ] Mutable writes use a fresh read and Compare-And-Set.
- [ ] Amazon returns a structured success or pending result.
- [ ] A different, read-only Verifier performs a new Amazon query.
- [ ] Verification references the recorded read action and matches the expected state.
- [ ] Audit, Web, alerts and task state show the complete chain.
- [ ] An intentional mismatch produces a critical alert and prevents clean completion.
- [ ] A forced MCP timeout/429 does not duplicate the write.
- [ ] A process restart between reservation and result quarantines the decision as `uncertain`.

## Approval and Campaign-create acceptance

Use a Test Account or a tightly bounded low-budget Profile.

- [ ] Main creates an exact Campaign hierarchy plan from the live MCP Schema.
- [ ] The plan contains Campaign, Ad Group, Target/Keyword and Product Ad actions as applicable.
- [ ] Every action shows exact arguments, expected state, Profile, budget exposure and dependency plan keys.
- [ ] The task remains `awaiting_approval`; an Executor cannot bind before approval.
- [ ] The agent token cannot call either browser or operator approval endpoints.
- [ ] Wrong Payload Hash, wrong confirmation phrase, missing CSRF and expired approval are rejected.
- [ ] The owner approves the exact Hash in the authenticated Web.
- [ ] Changing any approved name, budget, bid, state, product, target or parent ID is blocked.
- [ ] Campaign creation returns one unique Campaign ID and the controller binds it.
- [ ] The approved Campaign ID placeholder renders into the Ad Group request without changing the approval Hash.
- [ ] Every later created ID is bound before dependent actions proceed.
- [ ] Missing or ambiguous created IDs change the action to `uncertain` and stop dependents.
- [ ] Executor performs only one entity/action per MCP call.
- [ ] Different-session Verifier reads every created object and validates the rendered expected state.
- [ ] Approval decisions are consumed once and cannot be replayed.
- [ ] Approval expiry prevents any new action; an in-flight action is reconciled but not repeated.
- [ ] A partially executed plan cannot be rejected as though nothing started; it requires pause and reconciliation.
- [ ] Billing/account/permission/delete and black-box composite operations remain blocked even with an approval attempt.

## Autopilot release

- [ ] No critical alerts or unacknowledged Schema drift.
- [ ] Backup and rollback drill completed.
- [ ] Routine canary, Campaign-create canary and shadow evidence approved by the owner.
- [ ] Start with narrow Profile/rule scope and low daily action limits.
- [ ] Review the first complete attribution window before widening scope.
- [ ] Keep the approval panel monitored until several complete structural plans have passed independent verification.
