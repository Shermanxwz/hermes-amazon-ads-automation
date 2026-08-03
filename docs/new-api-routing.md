# New-API routing

The deployment uses two logical Hermes providers over one upstream base URL:

| Hermes provider | Models | API mode | Purpose |
|---|---|---|---|
| `custom:new-api-230385` | non-GPT models including MiniMax-M3 | `chat_completions` | read-only audit / ordinary chat |
| `custom:new-api-230385-codex` | `gpt-*` | `codex_responses` | main controller |

`/root/.hermes/scripts/sync-newapi-hermes.py` synchronizes `/v1/models`, assigns `gpt-*` models to the Responses provider, and keeps the two provider model catalogs in sync. It reads the local Hermes config and must only be run on a machine where credentials are already configured securely.

Do not commit a populated config file. Use placeholders from `config/hermes-amazon-chain.example.yaml` and configure secrets locally.
