# Engineering hardening 3.3

This release keeps the deterministic Amazon Ads closed-loop design and hardens its runtime boundary, browser status model, compatibility matrix and extension composition.

## Runtime states

The control plane now exposes distinct states through `/health/ready`:

- `configured`: AUTOPILOT and execution are enabled in settings.
- `ready`: all runtime dependencies required for a write are healthy.
- `writable`: configured and ready at the same time.
- `degraded`: the service can still serve reads, but one or more write dependencies are unhealthy.
- `blocked`: AUTOPILOT was requested but writes are fail-closed.

New write reservations and new write-enabled tasks are rejected when the runtime is not writable. Existing read, diagnostic and recovery paths remain available when database integrity and writability permit them.

The write gate checks database integrity and writability, MCP catalog presence and drift, hard storage pressure, Hermes plugin presence and heartbeat freshness, the last catalog synchronization, callback backlog and durable result-outbox pressure.

Thresholds can be tuned without weakening the invariant that every check must pass:

- `ADS_HERMES_HEARTBEAT_MAX_AGE_SECONDS` (default `120`)
- `ADS_PENDING_CALLBACKS_READY_LIMIT` (default `25`)
- `ADS_RESULT_OUTBOX_READY_LIMIT` (default `100` pending records)
- `ADS_RESULT_OUTBOX_READY_BYTES` (default `8388608` bytes)

## Tool-result authorization binding

The Hermes plugin client now independently binds every successful tool authorization to:

- `tool_call_id` when Hermes supplies one;
- the exact Session, tool name and canonical argument SHA-256;
- a short TTL and a bounded in-memory cache;
- one tool-result delivery only.

Metadata supplied by the post-tool hook is removed and reconstructed from this secure cache. A missing, expired or mismatched entry is sent without reservation metadata, causing stateful write completion to fail closed. Session end, reset and worker stop clear pending entries.

Configuration:

- `ADS_AUTHORIZATION_CACHE_TTL_SECONDS` (default `900`, bounded to `5..3600`)
- `ADS_AUTHORIZATION_CACHE_MAX_ENTRIES` (default `2048`)

## Extension composition

Ordered runtime extensions are now installed through one authoritative `extension_registry.py`. The registry preserves the established security-sensitive order, rejects duplicate entries, validates every installer and exposes the installed sequence for regression tests. This removes package-level import-order sprawl without changing the current extension behavior.

## Web and test coverage

The dashboard displays the actual runtime state instead of equating `execution_enabled` with writability. It shows blocking checks and distinguishes configured, ready, writable, degraded and blocked conditions.

Hosted CI now includes:

- Hermes Agent `0.18.2`, `0.19.0` and `0.20.0` PluginManager smoke tests;
- Chromium, Firefox and WebKit browser coverage;
- successful approval, rejection and Schema acknowledgement;
- strategy saving, session expiry and network-failure recovery;
- HTTP boundary tests proving the runtime gate blocks before reservation;
- authorization TTL, argument binding, one-shot consumption and cleanup tests;
- extension-order and version-owner tests.

Version labels are unified at `3.3.0` / `HermesAdsControl/3.3`.
