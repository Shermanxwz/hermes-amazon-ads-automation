# Amazon Ads Remote MCP: Sanitized Migration Reference

This reference captures the reusable Amazon Ads/Hermes pattern without retaining client IDs, secrets, tokens, OAuth state, or user account data.

## What the gates mean

| Gate | Evidence | What it does **not** prove |
|---|---|---|
| Endpoint reachability | Unauthenticated MCP request returns HTTP 401 and protected-resource metadata | OAuth is complete or Ads tools are usable |
| OAuth request generation | Hermes starts PKCE and produces an authorization URL | The provider accepted the redirect URI |
| Developer-console allowlist | The exact current callback is saved under Allowed Return URLs | User consent or token persistence |
| Callback/token completion | Browser returns to the listener and a token record is created with access/refresh material | Account has the expected Ads profiles or permissions |
| Tool discovery | `hermes mcp test <server>` connects and reports tools | A domain request succeeds |
| Ads read-only call | Profile/account/campaign/report response is read back | It is safe to perform a write without budget/idempotency controls |

Always report these as separate statuses.

## Reusable OAuth procedure

1. Configure the destination MCP with native OAuth/PKCE and a fixed callback port when the provider requires a static allowlist.
2. Read the exact callback URI from the active destination configuration **and** from the currently generated authorization request. Do not reconstruct it from memory. Compare the two as exact strings/code points; visually similar characters such as lowercase `l`, uppercase `I`, lowercase `i`, and digit `1` are distinct URI values.
3. Before asking the account owner to repeat a portal change, compare that effective URI with the last user-confirmed portal value or screenshot/history. If the user says the allowlist was already completed, investigate client-ID/Security-Profile mismatch or character-level drift and reproduce the live authorization error before requesting any edit. If a portal action is genuinely needed, ask the account owner to save the exact URI under **Allowed Return URLs**. Do not ask for credentials in chat.
4. Start the destination MCP test and open the generated authorization URL.
5. Let the account owner complete provider login/consent in the browser. Never copy the authorization code or state into the conversation.
6. Verify the callback process exits successfully, the token file exists with restrictive permissions, and the token record contains the expected fields without printing values.
7. Re-run MCP connection/tool discovery from a fresh process. A token file alone is not proof of tool usability.

Common exact-match failures are `127.0.0.1` versus `localhost`, a different port, an old callback suffix, a trailing slash, and saving the value under Allowed Origins instead of Allowed Return URLs.

## Read-only Ads acceptance pass

After tool discovery, perform only bounded read operations:

1. List accessible Ads profiles/accounts.
2. Identify the intended marketplace/profile and read its campaign summary.
3. Read the relevant ad groups, ads, keywords/targets, budgets, and current states.
4. Request a small report or recent status/metrics window.
5. Compare against historical notes only to detect drift; never treat historical counts or spend as current truth.
6. Persist a sanitized verification record containing timestamp, profile identifier if safe for local storage, request class, result status, and any missing permission.

Do not create, pause, resume, bid, or budget-change anything during this proving pass. If later writes are requested, require an explicit scope and budget cap, duplicate detection/idempotency, permission checks, and immediate read-back.

## Hermes-specific operational checks

- Use supported Hermes configuration commands and finish with `hermes config check` plus a read-back.
- Run `hermes mcp test` for the destination server rather than inferring health from `hermes mcp list`.
- Capture the discovered tool count and connection duration.
- Check the scheduled job’s last result and next run separately; an active cron row can still have a killed or unknown execution.
- Test the normal model provider independently of the Amazon MCP so provider-routing failures are not misdiagnosed as OAuth failures.
- If the old agent is being retired, disable its Web UI provider/agent first, repeat the read-only Ads pass, and only then remove its CLI and home directory.
- New-API / one-api gateways expose a single `https://<host>/v1/responses` for `codex_responses`-channel providers; the channel requires `stream=true` and rejects non-streamed POSTs. There is **no separate "GPT-only base URL"** on these gateways — candidate paths like `/responses`, `/codex/v1`, `/codex`, or `/v1` typically return 200 static HTML or 404 JSON. Before offering a `base_url` change as an option, probe the candidate URL with the user's bearer; if no working alternative exists, the only valid action is to delete the provider.

## Programmatic secret reuse when the user pastes credentials

If the user pastes a long opaque value (`client_secret`, `refresh_token`, `Bearer`, etc.) into chat, do **not** hand-transcribe it into a command argument. A single character drift — usually lowercase `l` vs uppercase `I`, lowercase `i` vs digit `1`, or `O` vs `0` — silently turns an LWA token exchange into `invalid_grant` with no obvious cause. Instead:

1. Read the value directly from the user's most recent message row in the local session database (`SELECT content FROM messages WHERE role='user' AND content LIKE '%…%' ORDER BY id DESC LIMIT 1`). Parse with a strict line-prefix regex (e.g. `^AMAZON_ADS_CLIENT_SECRET=(.*)$`) and reject if the value contains any whitespace or non-ASCII.
2. Compare length and SHA-256 hash against any local credential file before using; if the lengths differ, fall back to re-reading from the DB row rather than guessing.
3. Pipe the value into a Python script as a literal in a multi-line string inside `execute_code`, not as a shell argument that ends up in process listings or shell history.
4. After the exchange succeeds, overwrite the on-disk credential file from the same DB-derived value with `0600` permissions. Never re-emit the value into chat, handoff files, logs, or skill references.

## Removing a provider and cleaning cron snapshots

- After `hermes config unset providers.<name>`, the Web UI or its reconcile tick may re-write the provider block into `~/.hermes/config.yaml`. Re-read the file (and `hermes config get providers.<name>`) one or more minutes later to confirm the unset is durable; if it reappears, stop promising the user that the deletion worked.
- Cron jobs record `model_snapshot` and `provider_snapshot` at creation time; the scheduler uses these for a drift guard. After removing a provider, edit the affected job's `model`/`provider` with `hermes cron edit`, then directly null both snapshot fields in `~/.hermes/cron/jobs.json` (the CLI does not always clear them). Without that, the next fire fails closed with a drift error.
- When a daily task involves judgement or external side effects, prefer the LLM as primary controller and reserve sub-agent / worker models for bounded audit jobs. Reflect that routing decision in `model`/`provider` and the snapshot fields so the next tick does not silently drift back to the old path.

## Direct MCP fallback for bounded read-only verification

If a model-mediated `hermes chat` call hangs or times out while invoking a known read-only MCP operation, do not infer that the operation failed and do not retry the same natural-language loop indefinitely. Treat the attempt as `unknown`, inspect whether the caller process is still alive, then use the installed MCP SDK directly for a bounded probe:

1. Load the already-persisted Hermes access token from its restrictive token file; never print it, put it in a command argument, or paste it into a report.
2. Connect with `mcp.client.streamable_http.streamable_http_client` and `mcp.ClientSession` using an `Authorization: Bearer …` header.
3. Call `initialize()` and `list_tools()` first. Read the target tool's `inputSchema` instead of guessing argument names.
4. Call only the explicitly read-only operation with a finite HTTP timeout. Parse and retain only a compact, non-sensitive result (counts, IDs/states as appropriate); discard full raw payloads.
5. Re-read the resource after any timed-out automation attempt if the attempted job could have side effects. Compare stable IDs/states against a pre-run snapshot; a timeout is not evidence that no side effect occurred.

This fallback is for verification, not a way to bypass Hermes policy or turn a write tool into a read. Keep the original MCP server configuration and token manager as the production path.

## Cron execution timeout and recovery

A cron row with `enabled=true` or `state=scheduled` is not a completed run. For a manual or scheduled execution that exceeds the caller's timeout:

- inspect `~/.hermes/cron/executions.db` and record the latest status, PID, owner/process ID, start time, and error;
- verify the owner PID is gone before calling the execution abandoned;
- use the scheduler's `recover_interrupted_executions()` to mark a dead `claimed`/`running` attempt as `unknown`; never rewrite it as success;
- clear a stale `fire_claim` only after the owner is confirmed dead and the execution is recorded as `unknown`, so the next scheduled occurrence is not blocked;
- re-read `jobs.json` to confirm the job remains enabled, has the expected pinned model/provider, and has a future `next_run_at`;
- if the job may have changed external state before the timeout, perform a read-only domain reconciliation before retrying.

Do not report an `unknown` run as successful, and do not assume that a killed subprocess had zero side effects without a post-run read-back.

## Safe user handoff language

Ask the user for an account-side action, not a secret:

> Please log in to the provider console, save the exact callback under Allowed Return URLs, and reply only when it is saved. Do not send your password, client secret, access token, refresh token, or authorization code.

For messaging verification, the equivalent user-owned actions are scanning a QR code or sending a test message; request only confirmation of completion.
