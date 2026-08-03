# Read-only operation policy

The daily job is an auditor, not an operator.

## Allowed

Only semantically read-only operations such as:

- `list`
- `query`
- `get`
- `retrieve`
- `check`

The candidate allowlist is in `config/amazon-ads-readonly-tools.example.yaml`. Validate the names against the live MCP schema before enabling it.

## Forbidden

Never call or emulate:

- create, update, delete
- pause, resume, enable, disable
- budget or bid changes
- campaign, ad group, ad, target, keyword, or product-targeting writes
- report creation, update, or deletion
- POST/PUT/PATCH/DELETE advertising endpoints used to mutate state

If an operation's semantics or schema are unclear, skip it and report `metrics_unavailable`.

## Decision boundary

```text
MiniMax-M3 = read + analyze + recommend
GPT/main controller = review + decide
User authorization = required before any external write
```

Do not rely only on natural-language instructions when a tool-level allowlist is available. Prefer `tools.include` for the daily MCP session, and keep cron approvals fail-closed (`approvals.cron_mode: deny`) unless there is a narrowly scoped human-controlled exception.
