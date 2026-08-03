# Control-plane API

The control plane binds to loopback by default. Browser endpoints are intended to be exposed only through an HTTPS reverse proxy. Agent endpoints use a separate bearer token and should remain loopback-only.

## Health

- `GET /health/live` — process liveness.
- `GET /health/ready` — database and controller readiness.

## Browser API

### `POST /api/login`

```json
{"password":"operator passphrase"}
```

Sets an HttpOnly, SameSite=Strict session cookie and returns a CSRF token. When `PUBLIC_ORIGIN` is HTTPS, the cookie is also Secure.

### `GET /api/dashboard`

Returns settings, task counts, tasks, active/history workers, recent actions, and recent events. This is the read-only operating record used by the Web UI.

### `PUT /api/settings`

Requires the browser session, `Origin: <PUBLIC_ORIGIN>`, and `X-CSRF-Token`.

```json
{"mode":"autopilot","execution_enabled":true}
```

Supported modes:

- `autopilot`: bound Workers may execute planned writes.
- `observe`: all Ads writes are blocked; reads and analysis continue.
- `paused`: Ads activity is blocked.

The Web UI exposes no task approval or ad-mutation endpoint.

## Hermes agent API

All endpoints require:

```text
Authorization: Bearer <AGENT_INGEST_TOKEN>
```

### `GET /api/agent/context?session_id=<id>`

Returns authoritative role, mode, current task, and compact instructions for the Hermes prompt hook.

### `POST /api/agent/tasks`

Creates an observable task before delegation. `expected_actions` may define exact idempotent writes:

```json
{
  "title":"Reduce waste",
  "kind":"optimization",
  "objective":"Reduce a proven inefficient bid",
  "write_allowed":true,
  "expected_actions":[{
    "idempotency_key":"keyword-123-bid-2026-08-04",
    "tool_contains":"update_bid",
    "entity_id":"keyword-123",
    "field":"bid",
    "before":1.0,
    "after":0.9,
    "reason":"14-day spend without conversions"
  }]
}
```

### `POST /api/agent/worker-bind`

Binds a real Hermes child session to one task. The native plugin calls this from `subagent_start` when the delegated goal contains `[ads-task:<task-id>]`.

### `POST /api/agent/tool-check`

Authoritative pre-execution policy decision. Ads writes require all of the following:

- mode is `autopilot` and execution is enabled;
- caller is a currently bound Worker;
- task allows writes;
- operation is not destructive;
- write matches a planned action when planned-write enforcement is enabled;
- guardrail and idempotency checks pass.

Denied requests return HTTP 403 and a structured reason. Reads return HTTP 200 when allowed.

### `POST /api/agent/tool-result`

Records the redacted result and duration after an allowed tool call. A successful planned write makes its idempotency key non-repeatable.

### `POST /api/agent/worker-stop`

Completes or fails the bound task and stores the Worker summary plus structured read-back verification.

### `POST /api/agent/events`

Appends an operational note or lifecycle event. Secret-shaped keys and values are redacted before persistence.

## Error behavior

The Hermes plugin fails closed for Amazon Ads writes and unknown Ads operations when the controller is unreachable. Clearly classified Ads reads remain available so the agent can diagnose and report the outage. Unrelated Hermes tools are unaffected.
