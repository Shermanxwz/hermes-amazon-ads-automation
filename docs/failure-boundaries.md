# Failure boundaries and recovery

## Context overflow

A long-running Hermes session can exceed the upstream effective context even when the local model catalog advertises a larger limit. Large MCP schemas, tool results, reasoning, and historical messages all contribute. Start a fresh session or run `/compress` before continuing; do not repeatedly retry an already overflowing request.

## Incomplete Responses

`Codex response remained incomplete after 3 continuation attempts` means the Responses channel returned incomplete turns repeatedly. It is separate from Amazon OAuth and should be investigated together with request size, tool-loop length, MCP failures, and upstream streaming behavior.

## Amazon MCP unavailable

After repeated MCP failures, stop the audit, mark the affected data `metrics_unavailable`, and do not use a write fallback. A timeout is not proof that no side effect occurred; reconcile stable entity IDs and states before retrying anything.

## Cron status

`enabled=true` or `state=scheduled` does not prove a successful run. Inspect executions, recover stale owners as `unknown`, clear stale claims only after owner death is confirmed, and keep a read-back record.
