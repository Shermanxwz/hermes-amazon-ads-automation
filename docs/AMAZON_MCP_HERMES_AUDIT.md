# Amazon official contracts, MCP and Hermes audit

Audit date: **2026-08-04**

This audit keeps four evidence classes separate:

1. Amazon public capability evidence;
2. repository implementation and contract evidence;
3. executed isolated sandbox evidence;
4. owner-credentialed production evidence.

A lower layer never proves a higher layer.

## Conclusion

The branch is architecturally **Hermes-native, live-MCP-driven, regionalized and fail-closed**. It is not yet production-perfect certified because real Amazon credentials, real Hermes surfaces, the target VPS and a functioning CI Runner are still external acceptance boundaries.

Current executed isolated evidence is recorded in `SANDBOX_EXECUTION_2026-08-04.md`:

- **104 PASS**;
- **0 FAIL**;
- **15 EXTERNAL**;
- overall `PASS_WITH_EXTERNAL_ACCEPTANCE`.

## Official Amazon surface reviewed

The public Advanced Tools Postman collection was treated as a capability inventory, including:

- OAuth, Profiles and Manager Accounts;
- Sponsored Products, Sponsored Brands and Sponsored Display;
- Sponsored TV;
- DSP reporting and related contracts;
- Reporting, Snapshots and Exports;
- Marketing Stream;
- Recommendations;
- Budget and Budget Rules;
- Test Accounts;
- Amazon Marketing Cloud administration, reporting and audiences;
- Product metadata and eligibility;
- Creative Asset Library;
- Stores, Locations and Partner opportunities.

Public API existence does not prove that a particular Amazon Ads MCP tenant exposes the same Tool or Schema. Live MCP discovery remains authoritative.

References:

- `https://github.com/amzn/ads-advanced-tools-docs/tree/main/postman`
- `https://advertising-ai.amazon.com/mcp`
- `https://advertising.amazon.com/library/news/amazon-ads-mcp-server-open-beta/`

## Explicit capability policy

`official/project-capability-policy.json` assigns every audited capability one treatment:

- routine autonomous;
- exact operator-approved atomic execution;
- bounded data job;
- read-only context;
- compile and decompose;
- production acceptance only;
- permanently blocked.

`scripts/check_project_capability_policy.py` fails when a newly discovered official capability has no policy.

Key decisions:

| Capability | Treatment |
|---|---|
| Mature SP bid/budget/placement/negative/harvest | Routine deterministic autonomy |
| Campaign/Ad Group/Target/Keyword/Ad/Portfolio creation | Exact approval, then atomic execution |
| SB/SD/STV structural work | Product-specific acceptance plus exact approval |
| Reports/Snapshots/Exports/Stream | Bounded data jobs or monitoring |
| Recommendations | Read/explain; never blind-apply |
| Product metadata/eligibility | Read-only evidence |
| AMC/DSP/creative/store/location administration | Read-only or outside current autonomous strategy |
| MCP end-to-end Campaign workflow | Compile to atomic decisions; direct black-box write blocked |
| MCP locale expansion | Compile to regionalized atomic plan; direct black-box write blocked |
| Billing/account administration/delete | Permanently blocked |

## Live MCP protocol contract

`scripts/check_amazon_mcp_contract.py` independently:

1. validates the endpoint shape and rejects embedded credentials;
2. sends MCP `initialize`;
3. records the server contract and Session identifier;
4. sends `notifications/initialized`;
5. follows every `tools/list` cursor page;
6. rejects duplicate names and malformed input Schemas;
7. hashes Tool descriptions and Schemas deterministically;
8. classifies read, job, write, composite, destructive, billing and account authority;
9. checks Profile, reporting, Campaign-read and Campaign-create workflow coverage;
10. emits a non-secret manifest for drift review.

An unauthenticated `401` proves only that the protected endpoint exists. Production acceptance requires authenticated initialize and complete live discovery.

## Regional NA/EU/FE isolation

Amazon Profiles are regional. The integration supports:

- canonical `mcp-amazon-ads`, explicitly tagged by `ADS_MCP_DEFAULT_REGION`;
- `mcp-amazon-ads-na`;
- `mcp-amazon-ads-eu`;
- `mcp-amazon-ads-fe`.

The Hermes plugin tags Catalog rows as `hermes-registry:na|eu|fe`. The controller enforces region matching for:

- Main Profile-scoped reads;
- report/export jobs;
- structural plans;
- Executor writes;
- Verifier reads;
- every Profile-bound task.

A call or task cannot span regions. Account/Profile discovery is the only tagged regional call permitted before a Profile is known locally.

Amazon's public MCP announcement does not enumerate separate EU/FE MCP hostnames. Deployment examples therefore require the exact endpoint supplied by Amazon/onboarding and never guess regional hostnames.

## Hermes 0.18.2 integration

Pinned target: `hermes-agent==0.18.2`, tag `v2026.7.7.2`.

Registered surfaces:

- 15 `ads_control_*` Tools;
- 10 Plugin Hooks;
- 3 Slash Commands;
- one namespaced Skill.

Hook behavior:

- `pre_llm_call`: role, mode, task, decisions, reports, approvals, regions, resources and Outbox;
- `post_llm_call`: Session/model/fallback telemetry;
- `pre_tool_call`: final Amazon MCP authorization;
- `post_tool_call`: original result delivery without mutation replay;
- Session start/end/finalize/reset;
- Subagent start/stop.

Delegation contract:

- task and role markers are mandatory;
- Executor and Verifier use different Sessions;
- `max_spawn_depth: 1`;
- `orchestrator_enabled: false`;
- `inherit_mcp_toolsets: true`;
- `subagent_auto_approve: false`;
- `max_concurrent_children: 1` on 2C2G;
- model fallback forces OBSERVE and disables writes.

Hermes 0.18.2 has one dependable global delegation model setting. Different Session is a hard security boundary; different model is optional hardening when the installed runtime can prove per-child routing.

## Approval trust boundary

Normal Hermes receives only the Agent Token. Operator Token is absent/empty by default. Slash Commands are registered for consistent interaction but approval/rejection mutation is disabled unless a separately isolated Gateway explicitly enables it and has no terminal, file or environment-reading capability.

Default human authority is the authenticated Web:

- password Session;
- HttpOnly Cookie;
- Origin validation;
- CSRF;
- full canonical plan display;
- exact Hash;
- typed confirmation phrase;
- expiry;
- one-time decision consumption.

## Material defects fixed during the audit

1. `write_batch_hardening.py` existed but was not installed at package startup.
2. The Hermes example used the wrong OAuth scope and unsupported/ignored OAuth fields.
3. MCP Toolset inheritance, child auto-approval, timeouts and parallel-call behavior were implicit.
4. The plugin scanned only canonical `mcp-amazon-ads` and could miss regional Toolsets.
5. Region enforcement did not cover reports or Verifier reads.
6. No protocol-level live MCP manifest generator existed.
7. Public Postman capability existence was conflated with project write authority.
8. Real Hermes smoke depended on an unnecessary Operator Token.
9. Deployment validation did not exercise the safest Web-only approval default.

All nine were corrected in this branch.

## Executed sandbox

The current isolated execution is documented in `SANDBOX_EXECUTION_2026-08-04.md` and covers:

- MCP initialize, Session and paginated discovery;
- schema/authority/fingerprint checks;
- all public capability-policy declarations;
- NA/EU/FE Toolset discovery and read/job/write isolation;
- report lifecycle and lineage;
- attribution and data-quality gates;
- deterministic routine optimization;
- Main/Executor/Verifier permissions;
- Compare-And-Set, atomic reservation and result states;
- Outbox delivery without mutation replay;
- approval Hash, phrase, expiry and self-approval prevention;
- Campaign → Ad Group → Target → Ad ID binding;
- single-entity mutation with valid multi-condition Target expressions;
- tamper/missing-ID quarantine;
- different-Session verification and mismatch handling;
- Web login, Cookie, Origin and CSRF;
- SQLite integrity, backup and restart.

From an actual checkout, run:

```bash
bash scripts/run_full_sandbox.sh
```

Optional credentialed live MCP contract audit:

```bash
FULL_SANDBOX_LIVE_MCP=1 \
AMAZON_ADS_MCP_ACCESS_TOKEN='...' \
bash scripts/run_full_sandbox.sh
```

The token is never written to generated manifests.

## Release blockers

The PR must remain Draft until dated owner evidence exists for:

1. OAuth consent, refresh and expiry recovery;
2. authenticated live MCP initialize and every tools/list page;
3. live Tool descriptions, Schemas, hashes and regional source;
4. owner-supplied exact regional MCP endpoint mapping;
5. real NA/EU/FE Profiles, currencies and manager relationships;
6. real report submit, poll, GZIP download, parsing and attribution backfill;
7. real 429 and timeout behavior without duplicate jobs or writes;
8. Marketing Stream AWS delivery;
9. Test Account or bounded Campaign hierarchy canary;
10. real returned Campaign/Ad Group/Target/Keyword/Ad IDs;
11. different Hermes Verifier Session reading every object from Amazon;
12. real Chromium Web approval on the deployed HTTPS origin;
13. pinned Hermes package load plus CLI/Gateway/Cron/child checks;
14. 2C2G soak, reboot and systemd recovery;
15. backup restore and a mature attribution-window shadow review.

Accurate status: **production-candidate architecture with successful isolated simulation**, not perfect production certification.
