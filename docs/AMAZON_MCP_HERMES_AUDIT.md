# Amazon official contracts, MCP and Hermes audit

Audit date: **2026-08-04**

This document separates four different kinds of evidence. They must never be collapsed into one vague claim that the integration is “perfect.”

1. **Official capability evidence** — Amazon's public Advanced Tools Postman collection and MCP announcement.
2. **Repository contract evidence** — code, configuration, policy manifests and tests in this branch.
3. **Executed sandbox evidence** — isolated protocol/state-machine execution without owner credentials.
4. **Credentialed production evidence** — OAuth, live tools, real reports, real writes and real read-back in the owner environment.

## Executive conclusion

The branch is now architecturally **Hermes-native and Amazon-MCP-driven**, but it is not yet entitled to a production-perfect certification.

- The control plane no longer depends on a guessed static Amazon tool list. It imports the live Hermes MCP Registry contract and hashes each Schema.
- Hermes' Tool, Hook, Skill, Slash Command, Session and Delegation surfaces are explicitly integrated.
- Routine decisions remain deterministic and bounded.
- Structural work is exact-payload approval-gated and independently verified.
- Black-box composite workflows, account administration, billing and irreversible deletion remain blocked by design.
- NA/EU/FE Profile-to-tool routing is machine-enforced for reads, jobs and writes when the MCP Toolset is region-tagged.
- A live MCP contract auditor now performs `initialize`, `notifications/initialized`, paginated `tools/list`, schema hashing and authority classification.
- Every public Postman capability discovered by the semantic compiler must have an explicit project policy.

Release remains blocked until the credentialed acceptance items at the end of this document pass.

## Official source surface reviewed

The public Postman collection was reviewed as a contract inventory, including:

- OAuth and Profiles;
- Manager Accounts;
- Sponsored Products;
- Sponsored Brands;
- Sponsored Display;
- Sponsored TV;
- DSP reporting and related contracts;
- Reporting;
- Amazon Marketing Stream;
- Recommendations;
- Budget and Budget Rules;
- Test Accounts;
- Snapshots and Exports;
- Amazon Marketing Cloud administration, reporting and audiences;
- Product metadata and eligibility;
- Creative Asset Library;
- Stores;
- Locations;
- Partner opportunities.

The Postman collection proves that public API contracts exist. It does **not** prove that a particular Ads MCP tenant currently exposes every corresponding tool or Schema. The live MCP audit is therefore a separate gate.

Official references:

- `https://github.com/amzn/ads-advanced-tools-docs/tree/main/postman`
- `https://advertising-ai.amazon.com/mcp`
- `https://advertising.amazon.com/library/news/amazon-ads-mcp-server-open-beta/`

## Capability-policy matrix

The machine-readable source of truth is `official/project-capability-policy.json`.

| Capability group | Project treatment | Autonomous mutation authority |
|---|---|---|
| OAuth | Owner acceptance only; Hermes stores tokens | None |
| Profiles / Manager Accounts | Read-only identity, currency and region context | None |
| SP routine bid/budget/placement/negative/harvest | Deterministic bounded execution | Yes, inside immutable limits |
| Campaign / Ad Group / Target / Keyword / Ad / Portfolio creation | Exact atomic plan and operator approval | Yes, after exact approval |
| SB / SD / STV structural work | Live-schema plan and approval | Yes, only after product-specific acceptance |
| Reporting / Snapshots / Exports | Persistent bounded data jobs | No delivery mutation |
| Marketing Stream | Deduplicated monitoring and alerts | No direct mutation |
| Recommendations | Read and explain; exact approved apply only | Never blind-apply |
| Product metadata / eligibility | Read-only planning evidence | None |
| Creative assets / Stores / Locations | Read-only context by default | No autonomous administration |
| AMC / DSP | Read-only/out-of-scope for the current strategy engine | None |
| End-to-end campaign MCP workflow | Compile and decompose to atomic decisions | Direct black-box call blocked |
| Locale expansion MCP workflow | Compile and decompose with regional isolation | Direct black-box call blocked |
| Billing / finance | Permanently blocked | None |
| Users / roles / permissions / account links | Permanently blocked | None |
| Delete / archive / remove | Permanently blocked | None |

This is deliberate least privilege, not incomplete error handling. A capability can be connected and discoverable while still having no model-originated write authority.

## MCP protocol integration

`scripts/check_amazon_mcp_contract.py` audits the live server independently of model behavior:

1. validates HTTPS endpoint shape and rejects embedded credentials;
2. sends MCP `initialize` with a declared protocol version and client identity;
3. captures the server contract and session identifier;
4. sends `notifications/initialized`;
5. retrieves every `tools/list` page with cursor-loop protection;
6. requires unique names and object-root input Schemas;
7. hashes descriptions and Schemas deterministically;
8. classifies read, job, write, composite, destructive, billing and account-administration authority;
9. checks required account/Profile, reporting and Campaign workflow coverage;
10. emits a non-secret manifest suitable for drift comparison.

Unauthenticated HTTP `401` is treated as proof that the protected endpoint exists, not as proof of a successful integration. Production requires authenticated `initialize` and a complete live manifest.

## Regional isolation

Amazon's public API documentation requires separate regional Profile discovery for NA, EU and FE. A Profiles call only returns the Profile for the regional endpoint being used.

The integration now supports Hermes Toolsets named:

- `mcp-amazon-ads` with an explicit `ADS_MCP_DEFAULT_REGION`;
- `mcp-amazon-ads-na`;
- `mcp-amazon-ads-eu`;
- `mcp-amazon-ads-fe`.

The plugin tags every discovered tool as `hermes-registry:na|eu|fe`. The controller then checks:

- task Profile region versus Executor/Verifier tool region;
- report and export Profile IDs versus job tool region;
- Main read arguments versus known Profile region;
- structural plan Profile versus every action tool region;
- that one task/call does not span multiple regions.

Account/Profile discovery may run before a Profile exists locally. Other tagged regional calls require a known Profile or Profile-bound task.

The public MCP announcement confirms global availability, but it does not publicly enumerate separate EU/FE MCP hostnames. Deployment must use the exact endpoint supplied by Amazon/onboarding; examples intentionally do not guess regional hostnames.

## Hermes 0.18.2 integration

The pinned compatibility target is `hermes-agent==0.18.2` / `v2026.7.7.2`.

### Registered surfaces

- 15 `ads_control_*` Tools;
- 10 Plugin Hooks;
- 3 Slash Commands;
- 1 namespaced Skill.

### Hook coverage

- `pre_llm_call`: injects role, mode, task, decisions, reports, approval state, regional MCP state, resources and Outbox state;
- `post_llm_call`: records active session/model/fallback telemetry;
- `pre_tool_call`: final fail-closed authorization before any Amazon MCP call;
- `post_tool_call`: persists the original result envelope without replaying the Amazon mutation;
- Session start/end/finalize/reset lifecycle;
- Subagent start/stop binding and closure.

### Delegation contract

- `[ads-task:<id>]` and `[ads-role:executor|verifier]` markers are mandatory;
- Executor and Verifier must use different Hermes Sessions;
- `max_spawn_depth: 1` and `orchestrator_enabled: false` prevent uncontrolled trees;
- `inherit_mcp_toolsets: true` preserves the Amazon MCP Toolset in narrowed children;
- `subagent_auto_approve: false` prevents dangerous terminal approval in child threads;
- `max_concurrent_children: 1` runs Executor and Verifier sequentially on the 2C2G target;
- unexpected model fallback forces `OBSERVE` and disables execution.

### Approval trust boundary

Slash Commands are registered for interaction consistency, but mutation commands are disabled by default. Normal Hermes receives only the machine Agent Token. The Operator Token is absent/empty in the default deployment. Human approval is performed through the authenticated Web with Session, Origin, CSRF, exact Hash and typed confirmation.

## Findings fixed during this audit

1. `write_batch_hardening.py` existed but was not installed at package startup. It is now active.
2. The full Hermes example used an incorrect OAuth scope and fields not honored by Hermes 0.18.2. It now uses `advertising::campaign_management`, `redirect_port`, and Hermes' supported pre-registered client fields.
3. MCP Toolset inheritance, child auto-approval, parallel-call behavior and timeouts were implicit. They are now explicit deployment contracts.
4. The canonical plugin only scanned `mcp-amazon-ads`. It now discovers optional NA/EU/FE Toolsets and tags their source region.
5. Regional enforcement previously covered neither reports nor verifier reads. It now applies at the complete Tool Check boundary.
6. The project had no protocol-level authenticated MCP manifest generator. The live contract auditor was added.
7. Public Postman capability existence and project authority policy were conflated. A complete capability-policy manifest and CI checker were added.
8. The real Hermes smoke previously depended on an Operator Token. It now verifies successful plugin loading with Web-only approval defaults.

## Executed isolated sandbox

An independent isolated simulation was executed during this audit with the following result:

- **92 PASS**;
- **0 unhandled FAIL**;
- **14 EXTERNAL acceptance items**;
- overall: `PASS_WITH_EXTERNAL_ACCEPTANCE`.

The executed paths included:

- protected MCP endpoint behavior;
- initialize/session/paginated discovery/schema/authority audit;
- persistent report lifecycle and invalid-transition rejection;
- attribution, stale/future and missing-data gates;
- deterministic routine strategy;
- Main/Executor/Verifier authority;
- Compare-And-Set;
- reservation races and outcome states;
- durable Outbox and no mutation replay;
- approval Hash/phrase/expiry/self-approval boundaries;
- Campaign → Ad Group → Target → Ad ID binding and placeholder rendering;
- single-entity writes with legitimate multi-condition Target expressions;
- tamper and missing-ID quarantine;
- different-Session verification and mismatch handling;
- Web login, Cookie, Origin and CSRF;
- fallback-to-OBSERVE;
- SQLite integrity, backup and restart.

This proves the modeled protocol and state-machine behavior. It does not prove real Amazon response envelopes or real Hermes package execution inside GitHub Actions.

## Repository full-sandbox gate

Run from an actual checkout:

```bash
bash scripts/run_full_sandbox.sh
```

The script records `PASS`, `FAIL`, and `EXTERNAL` separately and produces:

- `artifacts/full-sandbox/report.json`;
- `artifacts/full-sandbox/report.md`;
- Postman semantic manifest;
- project capability-policy result;
- MCP fixture/live manifests where available.

Optional live MCP contract audit:

```bash
FULL_SANDBOX_LIVE_MCP=1 \
AMAZON_ADS_MCP_ACCESS_TOKEN='...' \
bash scripts/run_full_sandbox.sh
```

The token is used only as an Authorization header and is never written to the manifest.

## Remaining release blockers

The following must pass with dated owner evidence before the PR leaves Draft:

1. Login with Amazon consent, token refresh and expiry recovery.
2. Authenticated live MCP `initialize` and complete paginated `tools/list`.
3. Live tool names, descriptions, Schemas, Schema hashes and regional source verification.
4. NA/EU/FE Profile IDs, currencies and manager relationships for every enabled region.
5. Real report submit, poll, GZIP download, parsing, attribution backfill and dedupe.
6. Real 429 and timeout behavior without duplicate jobs or writes.
7. Marketing Stream delivery through the owner's AWS resources.
8. Test Account or tightly bounded Campaign hierarchy canary.
9. Real Campaign, Ad Group, Target/Keyword and Ad response envelopes with unique ID binding.
10. Different Hermes Verifier Session reading every object back from Amazon.
11. Real Chromium approval flow on the deployed HTTPS origin.
12. Pinned Hermes package load and CLI/Gateway/Cron/child interaction checks on every surface actually used.
13. 2C2G memory/CPU/storage soak, reboot and systemd recovery.
14. Backup restore and rollback drill.
15. At least one mature attribution-window shadow evaluation before widening scope.

Until these pass, the accurate status is **production-candidate architecture with successful isolated simulation**, not “perfect production integration.”
