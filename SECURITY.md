# Security and secret handling

This repository is intended to be private, but privacy is not a substitute for secret hygiene.

## Never commit

- GitHub PATs or OAuth tokens
- Amazon Ads client secrets, access tokens, refresh tokens, authorization codes, or OAuth state
- New-API keys
- `.env` files containing real values
- Hermes `auth.json`, `mcp-tokens/`, `state.db`, Web UI databases, logs, request dumps, backups, or full session transcripts
- Raw advertising metrics or customer/order payloads unless separately approved

## Local secret locations

Use a local secret manager or protected files with `0600` permissions. The example files in this repo contain placeholders only.

## If a token appears in a chat, log, terminal, or repository

1. Revoke/rotate it at the issuing service.
2. Remove it from Git history if it was committed.
3. Re-run the repository secret scan.
4. Reconfigure the local service with the replacement without pasting the new secret into chat.

The GitHub PAT used for this upload was pasted into the conversation and should be revoked immediately after the upload is verified.
