# Security

## Secrets

Never commit Amazon client secrets, OAuth tokens, Hermes auth/session databases, control passwords, agent tokens, browser cookies or raw customer/order payloads. Use a root-readable environment file with mode `0600`. OAuth tokens remain managed by Hermes.

## Network

The service defaults to loopback and refuses remote bind unless explicitly overridden. Publish through HTTPS Nginx/Caddy. Do not expose SQLite, 8790, browser debugging or Seller Central cookies.

## Hard policy

Main and Verifier cannot write. Executor writes require a bound task, deterministic decision, exact live MCP catalog entry, non-drifted Schema, successful independent JSON Schema validation, atomic reservation, one-entity batch, limits and independent verification. Delete/archive and account/billing administration are permanently blocked.

## Web

PBKDF2 password hashing, constant-time token comparison, bounded thread-safe sessions, login rate limiting, HttpOnly SameSite cookies, CSRF, Origin checking, CSP, no-store and frame denial are enabled.

## Scope

This project controls model-originated Amazon Ads MCP calls. It does not defend against root compromise, a compromised Hermes process, a stolen agent token or malicious code installed on the VPS. Seller Central login remains separate and manual.

## Incident response

Pause the service, disable the plugin, revoke affected Amazon/Hermes credentials, preserve the SQLite audit database, inspect alerts/actions/verifications, rotate tokens, re-sync the MCP catalog in `OBSERVE`, then resume only after Test Account verification.
