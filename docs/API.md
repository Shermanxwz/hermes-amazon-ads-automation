# Control-plane API

All JSON. Browser routes use an HttpOnly SameSite cookie. Mutations require `Origin` and `X-CSRF-Token`. Agent routes require `Authorization: Bearer <ADS_CONTROL_AGENT_TOKEN>` and are intended for loopback only.

## Health

- `GET /health/live`
- `GET /health/ready`

## Browser

- `POST /api/login`, `POST /api/logout`, `GET /api/session`
- `GET /api/dashboard`
- `GET /api/cycles`, `/api/decisions`, `/api/tasks`, `/api/actions`, `/api/verifications`
- `GET /api/events`, `/api/alerts`, `/api/workers`, `/api/profiles`, `/api/catalog`, `/api/settings`
- `PUT /api/settings`
- `PUT /api/catalog/<registered-name>/acknowledge`
- `PUT /api/profiles/<profile-id>`

## Hermes agent

- `POST /api/agent/catalog-sync`: exact tools and schemas from Hermes registry.
- `POST /api/agent/cycles/plan`: normalized Amazon snapshot; returns deterministic cycle and decisions.
- `POST /api/agent/tasks`: create task from a cycle.
- `POST /api/agent/worker-bind`: bind `executor` or `verifier` child session.
- `POST /api/agent/tool-check`: pre-tool authorization and atomic reservation.
- `POST /api/agent/tool-result`: structured execution outcome.
- `POST /api/agent/verify`: independent actual-state verification.
- `POST /api/agent/task-finalize`: finalize only after all decisions settle.
- `POST /api/agent/stream-events`: deduplicated Marketing Stream events.
- `POST /api/agent/events`, `/api/agent/worker-stop`.

Tool checks return `allowed`, `reason`, semantic operation, role, task/decision IDs and reservation token. A denied check returns HTTP 403.
