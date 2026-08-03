# Security

## Secrets

Never commit or paste into conversations: Amazon client secrets, OAuth access/refresh tokens, authorization codes/state, New-API keys, GitHub tokens, control-plane password hashes tied to a real password, browser profiles/cookies, or Hermes token/session databases.

Store runtime values in `/etc/hermes-amazon-ads-control.env` with mode `0600`, owned by the service user or root. The shared agent token should be at least 32 random characters and is only sent over loopback.

## Network

- Bind the control plane to `127.0.0.1`.
- Publish only through an HTTPS reverse proxy.
- Do not expose SQLite, browser debugging, xrdp/VNC, or port 8790 directly.
- The browser API uses password login, HttpOnly SameSite=Strict session cookie, CSRF token, Origin check, bounded sessions, and no-store headers.
- Agent endpoints use a separate bearer token and should stay loopback-only.

## LLM/tool boundary

The plugin blocks model-originated Amazon Ads writes from Main and unbound sessions. It does not protect against root, direct database edits, a compromised Hermes/control-plane process, a stolen agent token, or side effects caused by other allowed tools such as shell commands. Keep Hermes terminal approvals/sandbox rules appropriate for the VPS.

## Seller Central

The project does not automate authentication challenges, CAPTCHA/2FA, banking, tax, account-health appeals, permissions, or identity verification. Use the fixed VPS browser profile manually for those flows.

## Incident response

1. Set mode to `paused` or disable `execution_enabled`.
2. Revoke/rotate the affected token or OAuth credential.
3. Preserve the SQLite DB and Hermes logs.
4. Review blocked/allowed actions and task bindings.
5. Read back Amazon Ads objects before reverting; do not blindly replay inverse actions.
