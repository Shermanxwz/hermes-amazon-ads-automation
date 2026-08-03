# Hermes Amazon Ads automation (private review package)

Sanitized export of the current Hermes Amazon Ads automation chain for private review and optimization.

## What is included

- Amazon Ads native MCP/OAuth configuration shape
- MiniMax-M3 daily read-only audit cron prompt and schedule
- GPT main-controller routing shape
- New-API model synchronizer used by Hermes
- Candidate read-only MCP tool allowlist
- OAuth, failure recovery, context, and security documentation

## What is intentionally excluded

No real credentials, GitHub token, Amazon client secret, access/refresh token, OAuth state/code, Hermes token files, `.env`, databases, logs, backups, raw session transcripts, or raw advertising/order data are included.

## Operating model

```text
MiniMax-M3: read-only audit -> Chinese report + candidates
GPT/main controller: review -> decide
User authorization: required before any ad write
```

The daily job must remain read-only. The live MCP server advertises write-capable tools, so use the allowlist in `config/amazon-ads-readonly-tools.example.yaml` after validating its names against the live schema. Prompt instructions alone are not a hard security boundary.

## Local setup outline

1. Copy the example config and fill secrets locally through a protected secret manager or `hermes config set`; do not commit the populated file.
2. Ensure the exact Amazon OAuth callback is registered as an Allowed Return URL.
3. Run `hermes config check`.
4. Run `hermes mcp test amazon-ads`.
5. Validate read-only tool names and enable an explicit `tools.include` allowlist for the audit session.
6. Run the audit only after checking that no write tools are in the effective session toolset.

The repository is a configuration/documentation handoff, not a copy of Amazon's proprietary remote MCP implementation.
