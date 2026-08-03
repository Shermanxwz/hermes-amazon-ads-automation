# Architecture and trust boundary

## Why this is a Hermes plugin, not another agent framework

Hermes already supplies the conversation loop, cron, delegation, model/provider routing, MCP client, and session identity. The project therefore embeds at Hermes's supported extension points instead of reimplementing them:

- `pre_llm_call`: inject compact current role/mode/task context.
- `pre_tool_call`: synchronously authorize or block every Amazon Ads operation.
- `post_tool_call`: record bounded/redacted result metadata.
- `subagent_start`: bind a real Hermes child session to a control-plane task through `[ads-task:<id>]`.
- `subagent_stop`: observe the worker lifecycle when Hermes supplies a child session identifier.
- `ads_control_complete_task`: authoritative task close with structured read-back verification, because current Hermes `subagent_stop` payloads do not guarantee a child session ID.
- bundled skill: defines the controller/worker operating procedure.

## Authority model

The LLM is not the authority for role claims. The control plane derives Worker status only from a server-side child-session binding created by the plugin's real `subagent_start` hook. Caller-supplied `role=worker` fields are ignored.

A write is allowed only when all are true:

1. the tool is classified as an Amazon Ads write;
2. system mode is `autopilot` and execution is enabled;
3. the caller's Hermes session is currently bound to a task;
4. the task is write-enabled and running;
5. the requested change is within guardrails;
6. task and daily action limits are not exhausted.

Unknown Amazon Ads operations fail closed. Non-Amazon tools remain Hermes's responsibility.

## Main versus Worker

Main owns understanding and coordination. Main can query Ads, calculate metrics, create tasks, delegate, inspect results, and explain decisions. Main cannot call Ads writes.

Worker receives one bound task and owns execution plus read-back verification. A Worker cannot use one binding to execute an unrelated task without leaving visible evidence in the tool arguments and task timeline.

## Audit data

The SQLite database stores redacted, bounded metadata. It must not store access/refresh tokens, Authorization headers, browser cookies, client secrets, raw customer data, or full report payloads. Common secret-shaped keys are replaced with `[redacted]` before persistence.

The audit record is operationally append-only through the HTTP API. The Web interface has no mutation endpoint for actions/events/tasks.
