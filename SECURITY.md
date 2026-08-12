# Security — Archived Repository

This repository is archived and is not an active deployment target.

## Privacy rule

Do not commit or publish any of the following:

- Amazon Ads client secrets, refresh/access tokens, or authorization headers
- Amazon Ads Profile IDs or advertiser/account identifiers
- Hermes/provider/session credentials, cookies, JWTs, database files, or control tokens
- personal email addresses, public host IPs, customer/order payloads, or browser data
- private keys, passwords, API keys, `.env` runtime files, logs, backups, or generated artifacts containing operational data

Examples must use unmistakable placeholders only.

## Historical exposure handling

Removing a value from the current branch does not remove it from Git history, tags, releases, caches, forks, or clones. Any confirmed real credential must be rotated/revoked first. Private account identifiers should be removed from reachable refs/releases where possible and treated as previously public.

## Archive rule

No automated release, deployment, scheduled operation, or credentialed production acceptance should be run from this archived repository.
