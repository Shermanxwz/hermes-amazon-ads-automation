# Control-plane API v3.2

All routes use JSON. Browser routes use an HttpOnly SameSite cookie. Browser mutations require a valid session, matching `Origin` and `X-CSRF-Token`. Hermes agent routes require `Authorization: Bearer <ADS_CONTROL_AGENT_TOKEN>` and are intended for loopback only. The optional operator-command routes use a different `X-Operator-Token` and are disabled in normal Hermes deployments.

## Health

- `GET /health/live`
- `GET /health/ready`

## Browser session and operations

- `POST /api/login`
- `POST /api/logout`
- `GET /api/session`
- `GET /api/dashboard`
- `GET /api/cycles`
- `GET /api/decisions`
- `GET /api/tasks`
- `GET /api/actions`
- `GET /api/verifications`
- `GET /api/events`
- `GET /api/alerts`
- `GET /api/workers`
- `GET /api/profiles`
- `GET /api/catalog`
- `GET /api/settings`
- `PUT /api/settings`
- `PUT /api/catalog/<registered-name>/acknowledge`
- `PUT /api/profiles/<profile-id>`

## Browser approval authority

- `GET /api/approvals?status=<optional>`
- `POST /api/approvals/<approval-id>/approve`
- `POST /api/approvals/<approval-id>/reject`

Approval requires:

- authenticated browser Session;
- valid Origin and CSRF token;
- exact full `payload_hash`;
- exact confirmation string `APPROVE <approval-id> <hash-prefix>`;
- pending and unexpired approval state;
- an unchanged canonical task plan.

The response includes the complete normalized plan, action arguments, expected-state templates, dependencies, budget exposure, timestamps and immutable event history. A browser approval cannot be reused for another plan or changed payload.

## Hermes agent routes

- `POST /api/agent/catalog-sync`: synchronize exact live Hermes Amazon Ads tool names and Schemas.
- `POST /api/agent/reports`: create/recover a persistent report job.
- `POST /api/agent/report-evidence`: list same-Session structured report Action evidence.
- `POST /api/agent/reports/transition`: advance the persistent report lifecycle.
- `POST /api/agent/cycles/plan`: plan deterministic routine decisions from a lineage-backed normalized snapshot.
- `POST /api/agent/tasks`: create a routine execution task from a cycle.
- `POST /api/agent/managed-plans`: create an exact structural/high-risk plan and pending approval request.
- `POST /api/agent/approvals/request`: request approval for an existing deterministic task.
- `POST /api/agent/worker-bind`: bind the current Hermes child Session as `executor` or `verifier`.
- `POST /api/agent/worker-stop`: close the bound worker Session.
- `POST /api/agent/tool-check`: pre-MCP authorization and atomic reservation.
- `POST /api/agent/tool-result`: persist the original structured MCP result envelope.
- `POST /api/agent/prepare-write`: bind fresh Compare-And-Set read evidence to a mutable existing-entity write.
- `POST /api/agent/read-evidence`: list eligible Verifier read Actions.
- `POST /api/agent/verify`: independently verify one exact entity object.
- `POST /api/agent/task-finalize`: finalize only after all decisions settle.
- `POST /api/agent/stream-events`: ingest deduplicated Marketing Stream events.
- `POST /api/agent/runtime-status`: record Hermes plugin resources and Outbox state.
- `POST /api/agent/session-event`: record Hermes start/active/reset/end and model-fallback telemetry.
- `POST /api/agent/events`: append an auditable operational event.
- `GET /api/agent/context`: return role, task, decisions, rendered structural arguments, reports, approvals, Catalog, resources and instructions.

The agent may create and explain an approval request but there is no agent-token approval route.

## Optional restricted Hermes command routes

These routes exist only for an explicitly restricted Gateway that has no terminal, file or environment-reading tools:

- `POST /api/operator/approvals/<approval-id>/approve`
- `POST /api/operator/approvals/<approval-id>/reject`

They require `X-Operator-Token`, which must differ from the machine agent token. In normal deployments, the ordinary Hermes process does not receive this token and `ADS_CONTROL_ENABLE_COMMAND_APPROVAL` remains false.

## Authorization response

`POST /api/agent/tool-check` returns:

- `allowed` and `reason`;
- semantic operation, live family, risk and Schema hash;
- authoritative actor role and task ID;
- matched decision and plan key;
- one-time reservation token when a write is allowed.

A structural/high-risk write additionally requires an approved, unexpired canonical plan and exact rendered arguments. Unknown, drifted, account-admin, billing, irreversible delete and black-box composite/bulk operations are denied even when an approval request is attempted.
