# Long-running storage lifecycle

The control plane treats disk capacity as a safety boundary. Cleanup is automatic at startup and every six hours by default. Storage maintenance never weakens write verification and never deletes undelivered Amazon mutation results.

## Default retention tiers

| Data class | Default | Long-term representation |
|---|---:|---|
| Full MCP action arguments/results | 30 days | SHA-256, identifiers, status, size and summary |
| Detailed normalized metric rows | 60 days | Cycle KPI, data-quality result, decisions and hashes |
| Compressed normalized report Snapshot | 45 days | Report metadata, hashes, row count and transition history |
| Completed tasks, cycles, actions, verification and terminal reports | 180 days | Removed after relationally safe retention |
| Marketing Stream envelopes and ordinary events | 180 days | Removed after retention |
| Resolved alerts | 180 days | Removed after retention |
| Orphaned old open alerts | 180 days | Monthly severity/code/Profile rollup, capped at 5,000 buckets |
| Maintenance history | latest 100 runs | Older maintenance rows removed |
| Corrupt Outbox quarantine artifacts | 30 days / latest 3 | Older or excess artifacts removed |
| Pending Outbox events | Until delivered | Never removed by retention |

All periods are configurable. Under soft pressure, effective retention is shortened; under hard pressure, it becomes more aggressive while preserving current tasks, unresolved live alerts, hashes and safety evidence.

## Immediate write-time bounds

- A stored Action argument or result is bounded to `max_action_payload_bytes` (default 256 KiB after redaction).
- Oversized payloads are replaced with a compact envelope containing a safe canonical SHA-256, original size, important Amazon identifiers, statuses and shape metadata.
- The exact normalized report Snapshot is stored once as gzip. It is not duplicated inside report transition history or returned wholesale by dashboard APIs.
- Dashboard list endpoints remain bounded and never stream the internal compressed Snapshot.

## SQLite maintenance

Each maintenance pass:

1. applies relational retention to completed data;
2. compacts old Action payloads;
3. removes old detailed metric rows while retaining Cycle KPIs and decision evidence;
4. removes old terminal report Snapshot blobs while retaining immutable hashes;
5. rolls orphaned historical alerts into bounded summaries;
6. truncates the WAL with `wal_checkpoint(TRUNCATE)`;
7. runs `PRAGMA optimize`;
8. runs `VACUUM` only when there are no active tasks, reclaimable space is meaningful and free disk is sufficient.

A maintenance run cannot overlap another run.

## Pressure circuit breaker

Default thresholds for the SQLite database plus WAL are:

- soft: 512 MiB;
- hard: 1,024 MiB;
- minimum filesystem free space: 1,024 MiB.

Soft pressure triggers aggressive cleanup and a warning. If hard pressure remains after cleanup, the controller changes to `paused`, disables execution and raises `STORAGE_HARD_LIMIT`. It fails closed before disk exhaustion can corrupt state or make write results uncertain.

The Hermes durable result Outbox has separate defaults of 1,000 events or 8 MiB. The plugin first attempts delivery; if the Outbox remains over its limit, every new Amazon Ads MCP operation is blocked before authorization. Existing pending events remain durable and can still be flushed.

## Observation and administration

The existing Web shows database/WAL size, filesystem free space, reclaimable SQLite pages, current pressure, Outbox state and historical alert rollups.

Useful commands:

```bash
python scripts/control_cli.py storage-status
python scripts/control_cli.py maintain-storage
python scripts/control_cli.py doctor --full
```

## Host-level boundary

The application bounds its own SQLite, WAL, report, Action, Outbox and quarantine artifacts. Files created outside its managed paths remain the host operator's responsibility, especially manually named database backups and the system-wide journald quota. The systemd unit rate-limits service log storms; production acceptance must also confirm host journal limits, backup rotation and real-report storage soak on the target VPS.
