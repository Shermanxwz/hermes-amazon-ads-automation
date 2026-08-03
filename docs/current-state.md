# Current product state

This repository is now a deployable Hermes-native Amazon Ads autopilot rather than a documentation-only handoff.

## Runtime chain

```text
Hermes cron or user conversation
  -> Main controller reads Ads and creates a bounded task
  -> Hermes delegate_task starts a child with [ads-task:<id>]
  -> plugin subagent_start binds the real child session
  -> pre_tool_call asks the loopback control plane for every Ads operation
  -> Main writes are blocked; matching Worker writes may proceed
  -> post_tool_call records redacted outcome and idempotency state
  -> Worker reads back the entity and completes the task
  -> Web dashboard exposes tasks, Workers, actions, blocks, and events
```

## Enforcement state

- The control plane, not the language model, decides whether an Ads write is allowed.
- Main can read, analyze, create tasks, delegate, and review; it cannot execute Ads writes.
- A Worker is trusted only after a real Hermes child session is bound to a task.
- Writes must match structured `expected_actions` by default.
- Successful plan keys cannot be executed twice.
- Delete/archive and unknown Ads operations fail closed.
- Controller failure leaves clearly classified Ads reads available but blocks writes and unknown operations.
- No user approval step is required in `autopilot`; the operator can switch the whole system to `observe` or `paused` from the Web panel.

## Current deployment target

- Debian/Ubuntu Linux VPS, including the user's 2C2G dedicated-IP host.
- Control plane: loopback-only Python service, SQLite WAL, approximately 109 MiB RSS in sandbox E2E testing.
- Browser UI: published through a dedicated HTTPS reverse proxy; no direct public bind.
- Seller Central browser profile remains separate and is never read by the plugin or control plane.

## Validation completed

- 26 unit/integration/process tests.
- Real separate-process main → block → Worker bind → write → result → duplicate block → read-back completion flow.
- Fresh-directory installation smoke test.
- Python compile checks and repository secret scan.

Live Amazon Ads calls are intentionally not fabricated in CI. Final production acceptance still requires the user's existing Amazon OAuth profile and the actual MCP tool schema visible on the VPS.
