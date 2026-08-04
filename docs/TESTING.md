# Testing and release gates

The repository separates evidence into four layers. A green lower layer must never be described as proof of a higher layer.

## 1. Deterministic unit and integration suite

```bash
bash scripts/validate.sh
bash scripts/coverage.sh
```

The suite runs with Python development mode and warnings promoted to errors. It covers strategy decisions, attribution/data-quality gates, JSON Schema validation, catalog drift/removal, role separation, atomic reservation, expired-write quarantine, structured outcomes, recorded read evidence, Web authentication/CSRF/Origin checks, malformed HTTP, migration, backup, Marketing Stream retries/deduplication, installed CLIs and the independent-process Main → Executor → Verifier path.

Branch coverage has a hard CI floor of 75%. Coverage is a regression signal, not a claim that unexecuted lines are safe or that advertising decisions are profitable.

## 2. Package, deployment and stress validation

```bash
bash scripts/validate_deploy.sh
PYTHONPATH="$PWD/control-plane:$PWD/hermes-plugin:$PWD/tests" python3 tests/stress_recovery.py
```

Deployment validation builds and installs the wheel into a fresh virtual environment, checks all installed CLIs, validates runtime configuration and database integrity, runs the source installer, checks the Hermes plugin link, parses Nginx configuration and verifies a rewritten systemd unit.

The stress suite races 100 reservations for one decision, sends 1,000 duplicate stream events, serves 200 concurrent readiness requests and performs a full SQLite integrity check afterwards.

## 3. Browser, quality and official-contract CI

GitHub Actions additionally:

- runs the suite and branch-coverage gate on Python 3.11, 3.12 and 3.13;
- runs Ruff fatal-error rules, high-severity Bandit checks, JavaScript syntax and secret scanning;
- installs Playwright Chromium and executes real login, tab navigation, mode changes, responsive layout, logout, CSP/security-header and console/page-error checks;
- installs `hermes-agent==0.18.2` and loads the real plugin contract;
- downloads Amazon's current public Advanced Tools Postman collection and checks the protected Amazon Ads MCP endpoint;
- repeats package/deployment and stress/recovery validation on Ubuntu.

## 4. Credentialed production acceptance

The following cannot be proven by a public CI runner and must remain release-blocking evidence for the owner's environment:

- Amazon OAuth authorization, refresh and expiry recovery;
- visible Profiles, manager-account relationships and permissions;
- live Hermes discovery of the owner's exact Amazon Ads MCP tools and Schemas;
- report creation, polling, download, parsing and attribution backfill;
- real 429/rate-limit behavior;
- Amazon Test Account or tightly bounded canary write;
- a different Verifier session reading the changed state from Amazon;
- real Marketing Stream delivery through the owner's AWS resources;
- historical-account replay and a sustained `OBSERVE` shadow period;
- VPS reboot, HTTPS, systemd and backup-restore drill on the actual host.

Mock or synthetic results must never be recorded as successful credentialed acceptance.
