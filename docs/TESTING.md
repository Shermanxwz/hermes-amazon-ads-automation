# Testing and release gates

The repository separates evidence into four layers. A green lower layer must never be described as proof of a higher layer.

## Complete gate

From a real checkout, run:

```bash
bash scripts/run_full_sandbox.sh
```

It composes the repository suite, MCP fixture audit, official Postman semantic compiler, capability-policy completeness, fingerprint drift, endpoint reachability, stress/recovery and every locally available quality, deployment, browser and pinned-Hermes check. It writes separate `PASS`, `FAIL` and `EXTERNAL` results to `artifacts/full-sandbox/`.

Set `FULL_SANDBOX_LIVE_MCP=1` and provide an owner access token only when running the credentialed MCP contract gate. The token is never written to generated manifests.

## 1. Deterministic unit and integration suite

```bash
bash scripts/validate.sh
bash scripts/coverage.sh
```

The suite runs with Python development mode and warnings promoted to errors. It covers:

- strategy decisions, attribution and data-quality gates;
- live Catalog drift/removal and JSON Schema validation;
- MCP initialize/tools-list/schema/authority fixture behavior;
- Main/Executor/Verifier authority and different-Session enforcement;
- atomic reservation, expired-write quarantine and structured outcomes;
- report lineage and recorded Amazon read evidence;
- payload-bound approval request, exact Hash confirmation, expiry and one-time consumption;
- proof that the machine agent token can request but cannot self-approve;
- browser login, Origin and CSRF approval authority;
- permanently blocked account/billing/delete/composite boundaries;
- multi-step Campaign hierarchy dependencies;
- returned Amazon ID extraction and deterministic placeholder rendering;
- approval-Hash stability after real ID binding;
- parent-ID and argument tamper rejection;
- missing/ambiguous created-ID quarantine;
- approval supersession, partial-plan rejection and after-expiry completion semantics;
- single-entity batch enforcement while allowing one Target's legitimate multi-condition expression;
- Hermes discovery of canonical and explicitly named NA/EU/FE MCP Toolsets;
- Profile-to-region isolation for reads, reports, Executor writes and Verifier reads;
- migration, backup, Marketing Stream retries/deduplication and storage pressure;
- independent-process Main → Executor → Verifier flows.

Branch coverage has a hard CI floor of **80%**. Coverage is a regression signal, not proof that advertising decisions are profitable or that every external Amazon response shape has been observed.

## 2. Package, deployment and stress validation

```bash
bash scripts/validate_deploy.sh
PYTHONPATH="$PWD/control-plane:$PWD/hermes-plugin:$PWD/tests" python3 tests/stress_recovery.py
```

Deployment validation:

- builds and installs the 3.2 wheel into a fresh virtual environment;
- checks all installed CLIs and version output;
- validates machine/operator credential separation;
- validates regional MCP environment declarations;
- validates runtime configuration and full database integrity;
- runs the source installer and checks the Hermes plugin link;
- parses Nginx configuration;
- verifies the rewritten systemd unit;
- confirms storage and Outbox deployment controls.

The stress suite races reservations, deduplicates large Stream bursts, serves concurrent readiness requests and performs a full SQLite integrity check afterwards.

## 3. Hermes, browser, quality and official-contract CI

GitHub Actions is configured to:

- run unit and branch-coverage gates on Python 3.11, 3.12 and 3.13;
- run Ruff fatal/error rules, high-severity Bandit checks, JavaScript syntax and secret scanning;
- install Playwright Chromium and execute real browser login, tab navigation, responsive layout, exact approval display, CSRF rejection, approval, logout, CSP/security-header and console/page-error checks;
- install the real `hermes-agent==0.18.2` package;
- load the plugin using Hermes' real `PluginManager`;
- assert all 15 tools, ten Hooks, three Slash Commands and the namespaced Skill;
- prove real Hermes loads with no Operator Token and Web-only approval by default;
- test command approval disabled-by-default behavior and fallback telemetry;
- download Amazon's current public Advanced Tools Postman collection;
- enforce the accepted semantic API fingerprint and capability set;
- require an explicit project policy for every discovered official capability;
- execute the MCP initialize/tools-list/schema/authority fixture;
- check the protected Amazon Ads MCP endpoint;
- repeat package/deployment and stress/recovery validation on Ubuntu.

A workflow conclusion without allocated Runner steps or downloadable logs is not passing evidence. Release notes must distinguish a real green run from an Actions scheduling/account/platform failure.

## 4. Credentialed production acceptance

The following cannot be proven by repository CI and remain release-blocking evidence for the owner's environment:

- Amazon OAuth authorization, refresh and expiry recovery;
- authenticated MCP `initialize` and every paginated `tools/list` page;
- visible regional Profiles, manager-account relationships and permissions;
- the owner's exact live Amazon Ads MCP names, descriptions, Schemas and hashes;
- proof that each regional tool source matches its NA/EU/FE Profile;
- report creation, polling, download, decompression, parsing and attribution backfill;
- real 429/rate-limit and timeout behavior;
- real Amazon response envelopes for Campaign, Ad Group, Target, Keyword and Product Ad creation;
- Test Account or tightly bounded Campaign-create canary;
- deterministic binding of every returned real Amazon entity ID;
- a different Verifier Session reading every created object from Amazon;
- real Marketing Stream delivery through the owner's AWS resources;
- historical-account replay and a sustained `OBSERVE` shadow period;
- Web approval on the deployed HTTPS origin;
- Hermes Gateway interaction checks on every surface actually used;
- VPS reboot, systemd, storage soak and backup/restore drill.

Mock, static or synthetic results must never be recorded as successful credentialed acceptance. See `PRODUCTION_ACCEPTANCE.md` and `AMAZON_MCP_HERMES_AUDIT.md` for the exact evidence model.
