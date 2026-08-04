# Security

## Secrets

Never commit Amazon client secrets, OAuth tokens, Hermes auth/session databases, control passwords, agent tokens, browser cookies or raw customer/order payloads. Use a root-readable environment file with mode `0600`. OAuth tokens remain managed by Hermes. Audit payloads are recursively bounded and redacted.

## Network

The service defaults to loopback and refuses remote bind unless explicitly overridden. Publish only through HTTPS Nginx/Caddy. Do not expose SQLite, port 8790, browser debugging or Seller Central cookies. Responses use CSP, frame denial, no-store, MIME protection, restrictive browser permissions and same-origin opener/resource policies.

## Hard policy

Main and Verifier cannot write. Executor writes require the task's current bound Executor session, an enabled Profile, a recent deterministic decision, an exact live non-drifted MCP contract, valid JSON Schema arguments, non-high-risk single-entity operation, atomic reservation, cooldown and action limits. Delete/archive, account/billing administration, composite/bulk workflows and unconfirmed Schema changes are permanently blocked.

## Verification and recovery

A Verifier cannot submit arbitrary `actual` JSON. Verification must reference a structured read action recorded from the task's current, different Verifier session after the write attempt. Expired, partial or unknown writes are quarantined as `uncertain`; they are never automatically replayed. Atomic backup and full SQLite integrity checks are available through the operator CLI.

## Web

PBKDF2 password hashing, constant-time token comparison, bounded thread-safe sessions, login rate limiting, HttpOnly SameSite cookies, CSRF and Origin checks are enabled. Mutating controls disable while requests are in flight and present failures in the UI rather than silently failing.

## Scope

This project constrains model-originated Amazon Ads MCP calls. It cannot defend against root compromise, a compromised Hermes runtime, a stolen agent token, malicious code installed on the VPS, Amazon-side defects or incorrect business targets. Advertising-effectiveness claims require the owner's historical replay, shadow period and canary evidence.

## Incident response

Switch to `PAUSED`, disable the plugin, revoke affected Amazon/Hermes credentials, preserve and back up SQLite, inspect alerts/actions/verifications, reconcile every `uncertain` decision through independent reads, rotate tokens, re-sync the MCP catalog in `OBSERVE`, and resume only after the credentialed canary checklist passes.
