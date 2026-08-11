# Hermes Amazon Ads Control v4.2.1 — Sealed Full-Managed Release

v4.2.1 is the repository-sealed repair and autonomy release for the Hermes Amazon Ads control plane.
It replaces the incomplete v4.2.0 release line while preserving the historical v4.1.0 release.

## What is sealed in this release

- Sponsored Products full-managed **budget-bounded autonomy**: evidence controls action size and risk, rather than forbidding bounded experiments without historical proof.
- Account-level daily advertising exposure ceiling with exploration share and per-Campaign limit, enforced at the controller/execution boundary rather than only in the UI or planner.
- Atomic exposure reservations, CAS-style execution, reversible lifecycle sequencing, independent read-only verification and reconciliation.
- Autonomous SP creation path constructed PAUSED first, structurally verified, then activated leaf-to-parent with Campaign enabled last.
- Autonomous bid, budget, placement, harvest, exact-negative and bounded phrase-negative actions within the sealed policy envelope.
- Billing, account administration, permissions, irreversible delete/archive and unknown/drifted mutation surfaces remain permanently blocked.
- Immutable source report evidence and derived/live-enriched state are kept separate; the orchestrator no longer mutates an already-ingested source snapshot or self-authorizes writes.
- Privacy hardening removes advertiser/Profile identifiers from public UI/source/log paths and expands repository secret/account-data scanning.
- Dedicated non-root orchestration deployment, SQLite-safe backup behavior, deployment validation and >=80% branch-coverage gate.

## Hermes Studio integration

Hermes Studio is treated as the owner's primary conversation/Web surface, not as a separate control plane.
Studio chat, direct Hermes CLI sessions and scheduled orchestration must use the same Hermes base home/Profile and the same `amazon-ads-control` plugin trust boundary.

This release adds:

- correct plugin installation and validation for both the default Profile and named Profiles stored under `<base>/profiles/<name>`;
- a live Hermes Studio HTTP acceptance path through `/api/chat-run/runs` that must actually execute `ads_control_status`;
- a CI source-contract gate against the current `EKKOLearnAI/hermes-studio` Profile, PluginManager and Agent Bridge/chat-run semantics;
- fail-closed detection when upstream Hermes Studio changes those integration contracts.

## Amazon official contract sources

The sealed build continuously checks three independent Amazon surfaces:

1. Amazon Ads API official Postman collection;
2. Amazon Ads Unified API Postman collection, with GA and Beta separated;
3. Amazon Advertising AI MCP at `https://advertising-ai.amazon.com/mcp`, including MCP initialize/tools-list/schema and authority classification.

Runtime MCP schemas remain the execution source of truth. Postman/Unified API are independent official semantic and capability drift references.

## Release integrity

The `v4.2.1` Tag/Release is created only after the repository's `CI` workflow succeeds on the current `main` HEAD. The release workflow re-verifies:

- `pyproject.toml == 4.2.1`;
- `package-manifest.json == 4.2.1`;
- Hermes plugin version `== 4.2.1`;
- control-plane `__version__ == 4.2.1`;
- repository privacy/secret scan;
- current Hermes Studio integration contract.

If a `v4.2.1` tag already exists at a different commit, the workflow fails closed instead of moving the tag.

## Production acceptance boundary

Repository, sandbox and CI evidence cannot fabricate external owner/Amazon/VPS proof. A live production deployment still requires the explicit acceptance items in `docs/PRODUCTION_ACCEPTANCE.md`, including real OAuth refresh recovery, authenticated MCP discovery, Profile capability attestation, real reporting lifecycle, bounded write canary with independent Amazon read-back, attribution maturation, deployed Hermes Studio chat-run, and 2C2G VPS reboot/backup/HTTPS drills.

Accordingly, the repository can be **code-sealed** while live production acceptance remains evidence-driven and environment-specific.
