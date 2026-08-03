# Current Hermes Amazon Ads chain (sanitized)

This is a sanitized snapshot prepared for private review. It contains configuration shape and operational policy, not credentials or customer data.

## Architecture

```text
Amazon Ads OAuth/PKCE
        -> Hermes native remote MCP (amazon-ads)
        -> MiniMax-M3 daily read-only audit cron
        -> Chinese audit report / candidate recommendations
        -> current GPT main controller reviews and decides
        -> any ad change requires a separate explicit user-authorized action
```

## Live connection facts observed during packaging

- MCP endpoint: `https://advertising-ai.amazon.com/mcp`
- Hermes transport: native HTTP MCP with OAuth 2.1 PKCE
- Callback: `http://127.0.0.1:41515/callback/ciYSpNVlbv9P`
- MCP discovery: 110 tools were reported by `hermes mcp test amazon-ads`
- The live MCP registration exposes both read and write tools. The current daily job is intended to be read-only by policy; a tool-level `tools.include` allowlist is provided separately for stronger enforcement.
- Credentials are intentionally absent from this repository. Hermes OAuth tokens belong in `$HERMES_HOME/mcp-tokens/amazon-ads.json` with restrictive permissions.

## Model routing

- Daily audit: `MiniMax-M3` via `custom:new-api-230385`
- Main controller: `gpt-5.6-luna` via `custom:new-api-230385-codex`
- New-API base: `https://api.230385.xyz/v1`
- GPT channel: `codex_responses`; the upstream gateway requires streaming Responses requests.

## Daily job

- Job id: `1e1e65c91c40`
- Schedule: `30 17 * * *`
- Current name: `Amazon Ads 每日只读审计`
- The job only collects, checks, and analyzes data. It must not create reports, mutate campaigns, or invoke write endpoints.
- MiniMax does not make final decisions and must not call a delegation worker.
- The main controller decides separately, only after explicit user authorization.

## Current known limitations

1. The read-only policy is currently expressed in the cron prompt. The MCP server still advertises write-capable tools unless a `tools.include` allowlist is enabled and validated.
2. Reporting schema/API failures should be recorded as `metrics_unavailable`; the audit must not create a report to compensate.
3. A cron row being enabled or scheduled is not proof that an execution completed. Unknown/terminated executions require recovery and read-back.
4. Context compression must be monitored for long Hermes sessions. The local config enables compression, but already-running agent objects may retain stale runtime flags; a fresh session or explicit `/compress` may be required after a context overflow.
5. New-API model metadata may overstate the effective upstream context window. Probe the real endpoint and keep operational turns small.

## Sensitive material deliberately excluded

- GitHub token and other API keys
- Amazon client secret, access token, refresh token, OAuth state/code
- `$HOME/.config/amazon-ads/credentials.env`
- `$HERMES_HOME/mcp-tokens/*`
- Hermes state databases, Web UI database, logs, request dumps, and full session transcripts
- Backups containing any of the above
- Raw advertising metrics, order data, and customer/account payloads
