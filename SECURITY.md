# Security

## Secrets and private identifiers

Never commit Amazon client secrets, OAuth tokens, Hermes auth/session databases, control passwords, agent tokens, browser cookies, raw customer/order payloads, real Amazon Profile IDs, advertiser/account IDs, personal email addresses or public host IPs. Keep runtime credentials in a protected server-side environment file readable only by the service identity that needs them; do not solve permissions by running the autonomous orchestrator as root.

`scripts/verify-no-secrets.py` scans committed/current source and generated text artifacts for common credentials plus account/private identifiers. This is a current-tree gate, not a substitute for Git-history cleanup after an incident. Removing a value from HEAD does not erase old commits, tags, releases, forks or clones.

OAuth/access tokens must never be printed or fingerprinted in normal logs. Audit payloads are recursively bounded and secret-bearing keys/text are redacted while retaining enough nested Amazon response structure for verification and budget evidence.

## Network

The control service defaults to loopback and refuses remote bind unless explicitly overridden. Publish only through HTTPS Nginx/Caddy. Do not expose SQLite, port 8790, browser debugging, Amazon tokens, Hermes provider credentials, Hermes Studio JWT/session secrets or Seller Central cookies. Responses use CSP, frame denial, no-store, MIME protection, restrictive browser permissions and same-origin opener/resource policies.

Hermes Studio is an owner-facing chat/Web surface, not a privileged bypass. Studio, interactive Hermes and the scheduled orchestrator must use the same selected Hermes Profile with `amazon-ads-control` explicitly enabled. Browser JavaScript never receives `ADS_CONTROL_AGENT_TOKEN` or Amazon/Hermes credentials.

## Hard policy

Main and Verifier cannot write. Executor writes require the task's current bound Executor session, an enabled Profile, a recent deterministic/controller-authorized decision, an exact live non-drifted MCP contract, valid JSON Schema arguments, a single-entity atomic reservation, cooldown and action limits.

Every exposure-increasing write is additionally subject to the account daily Campaign-budget exposure hard cap. The hard cap cannot be disabled. A fresh, complete Amazon Campaign budget read for the exact Profile is required before increasing exposure. New exploration stops at its configured utilization threshold, normal positive exposure increases stop at the conservative threshold, and the absolute hard cap rejects any further increase. Exposure-neutral and risk-reducing actions may remain available when otherwise safe.

Delete/archive/remove, account/billing administration, users/roles/permissions, composite/bulk workflows, cross-region writes, unknown semantics and unconfirmed Schema changes are permanently blocked.

## Verification and recovery

A Verifier cannot submit arbitrary `actual` JSON. Verification must reference a structured read action recorded from the task's current, different Verifier session after the write attempt. Expired, partial or unknown writes are quarantined as `uncertain`; they are never automatically replayed. Later structural activation stages remain blocked until independent reconciliation establishes Amazon state.

Already `INGESTED` source snapshots are evidence and must not be rewritten by an orchestrator. Derived/live-enriched state must be stored as separate evidence. SQLite backup/recovery must use the project's transactional/online backup path and integrity checks; do not copy a live WAL database file as if that alone were a consistent backup.

## Scheduled autonomy

The systemd daily trigger has no direct Amazon MCP/Ads API or SQLite mutation authority. It only starts a Hermes one-shot through the normal enabled plugin/Profile. The service runs as the dedicated `amazonbot` identity with resource and filesystem restrictions. A scheduled path must never self-record `allowed=True`, impersonate another runtime component or create a second authorization path outside the controller.

## Web

PBKDF2 password hashing, constant-time token comparison, bounded thread-safe sessions, login rate limiting, HttpOnly SameSite cookies, CSRF and Origin checks are enabled. Mutating controls disable while requests are in flight and present failures in the UI rather than silently failing.

The owner Web exposes target ACOS, account daily budget hard cap, exploration share, per-Campaign budget and emergency modes. It must not render raw Profile/advertiser IDs, credentials, approval payload hashes, worker sessions, MCP catalogs or raw system logs.

## Scope

This project constrains model-originated Amazon Ads operations. It cannot defend against root compromise, a compromised Hermes runtime, a stolen agent token, malicious code installed on the VPS, Amazon-side defects or incorrect owner business targets. The daily hard budget bounds modeled Campaign-budget exposure; it does not make Amazon delivery behavior mathematically identical to a bank-account spending lock.

Advertising-effectiveness claims still require historical replay, shadow review, bounded exploration/canary evidence and matured attribution. Weak performance history may justify a smaller experiment, but never bypasses current-state reads, budget limits, atomic execution or independent verification.

## Incident response

Switch to `PAUSED`, disable the plugin, revoke affected Amazon/Hermes credentials, preserve SQLite/audit evidence, inspect alerts/actions/verifications, reconcile every `uncertain` decision through independent reads, rotate tokens, re-sync the MCP catalog in `OBSERVE`, and resume only after the credentialed canary checklist passes.

For a repository privacy incident, make the repository private if possible, remove the identifier from the current tree, rotate any actual credentials, rewrite affected Git refs/history, remove or move leaked tags/releases where possible, re-run the privacy scanner, and assume existing clones/forks may retain previously public data.
