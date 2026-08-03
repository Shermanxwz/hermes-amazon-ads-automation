# Amazon Ads MCP OAuth setup

## Requirements

1. Register the exact callback in the Amazon developer console as an Allowed Return URL.
2. Use the exact code points in the URI. `l`, `I`, `i`, and `1` are different characters.
3. Keep `client_id` and `client_secret` outside Git; load them locally through Hermes configuration or a secret manager.
4. Let Hermes perform OAuth/PKCE. Never paste authorization codes, state, refresh tokens, or access tokens into chat or a repository.

## Local configuration shape

Use `config/hermes-amazon-chain.example.yaml` as a structural reference. The live token store is managed by Hermes, not by this repository.

```bash
hermes config check
hermes mcp test amazon-ads
```

A successful connection/tool discovery is not the same as an Ads read operation. Verify separately with bounded read-only calls.

## Read-only acceptance pass

- list accessible Ads accounts/profiles
- query campaigns, ad groups, ads, targets, and portfolios
- retrieve existing reports only; do not create reports
- record counts, states, missing permissions, and data-window limitations
- compare before/after only when a prior run may have had side effects

No write operation belongs in the acceptance pass.
