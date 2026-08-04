# Credentialed production acceptance

Keep the PR and deployment in `OBSERVE` until every applicable item has dated evidence.

## Environment

- [ ] Dedicated Linux user, repository and virtual environment installed.
- [ ] `/etc/hermes-amazon-ads-control.env` is root-readable only.
- [ ] SQLite directory is writable only by the service account.
- [ ] Nginx/Caddy HTTPS works; port 8790 is not public.
- [ ] `amazon-ads-control --check` reports a healthy full database check.
- [ ] Backup is created with `control_cli.py backup` and restored into a temporary service.
- [ ] Service and Hermes gateway recover after a real host reboot.

## Amazon and Hermes read acceptance

- [ ] OAuth completes and a forced token refresh succeeds.
- [ ] Expected Profiles, marketplaces and currencies are visible.
- [ ] `hermes mcp test amazon-ads` succeeds.
- [ ] Live MCP catalog contains expected read/write/job tools with no unreviewed drift.
- [ ] Reports can be created, polled, downloaded and parsed for every enabled ad product.
- [ ] Attribution maturity and data-freshness gates behave correctly on real reports.
- [ ] Marketing Stream events arrive, deduplicate and produce budget/eligibility alerts.

## Strategy acceptance

- [ ] Export historical snapshots into the replay format and run `amazon-ads-backtest`.
- [ ] Review every rule family across product type, marketplace and lifecycle stage.
- [ ] Run at least one full attribution window in `OBSERVE` and inspect false positives.
- [ ] Define account-specific target ACOS, maximum ACOS and absolute operational boundaries.
- [ ] Confirm decisions do not use incomplete, future, duplicate or stale rows.

Replay and shadow evidence validate consistency and operator acceptability; they do not establish causal incremental lift.

## Canary write acceptance

Prefer an Amazon Ads Test Account. Otherwise choose one low-spend entity with an easily reversible change.

- [ ] Main creates exactly one deterministic decision.
- [ ] A bound Executor reserves and executes exactly that entity and value.
- [ ] Amazon returns a structured success or pending result.
- [ ] A different, read-only Verifier performs a new Amazon query.
- [ ] Verification references the recorded read action and matches the expected state.
- [ ] Audit, Web, alerts and task state show the complete chain.
- [ ] An intentional mismatch produces a critical alert and prevents task completion.
- [ ] A forced MCP timeout/429 does not duplicate the write.
- [ ] A process restart between reservation and result quarantines the decision as `uncertain`.

## Autopilot release

- [ ] No critical alerts or unacknowledged Schema drift.
- [ ] Backup and rollback drill completed.
- [ ] Canary and shadow evidence approved by the owner.
- [ ] Start with narrow Profile/rule scope and low daily action limits.
- [ ] Review the first complete attribution window before widening scope.
