# Full isolated sandbox execution — 2026-08-04

This is the latest executed sandbox evidence for the Amazon Ads MCP and Hermes integration audit. It supersedes the earlier 92/0/14 intermediate snapshot recorded while regional routing was still being added.

## Result

- Overall: **PASS_WITH_EXTERNAL_ACCEPTANCE**
- PASS: **104**
- FAIL: **0**
- EXTERNAL: **15**
- Total recorded checks: **119**

## Evidence boundary

The 104 passing checks were executed in an independent isolated deterministic simulator covering the protocol, authorization, state machines, recovery rules and interaction contracts. They are not a claim that the private repository checkout, the pinned Hermes package or a credentialed Amazon account executed in the current container.

Direct repository checkout was unavailable in the execution container, and GitHub Actions continued to return jobs with no allocated Steps or logs. The repository therefore includes `scripts/run_full_sandbox.sh` so the same gates can be run from an actual checkout when a Runner or owner host is available.

## Passing layers

| Layer | PASS |
|---|---:|
| Amazon MCP protocol and authority | 14 |
| Control plane | 6 |
| Report lifecycle | 10 |
| Data quality and attribution | 4 |
| Deterministic strategy | 3 |
| Hermes interaction model | 10 |
| Execution authorization | 6 |
| Independent verification | 10 |
| Recovery and Outbox | 8 |
| Approval trust boundary | 5 |
| Structural Campaign hierarchy | 6 |
| Web authentication and CSRF | 5 |
| Storage, integrity and restart | 5 |
| Regional MCP routing | 10 |
| Official capability policy | 2 |

## Complete simulated path

### Amazon MCP protocol

- protected endpoint behavior;
- MCP initialize request and server contract;
- initialized notification;
- Session identifier handling;
- paginated tools/list;
- cursor-loop protection;
- unique Tool names;
- object-root JSON Schemas;
- deterministic Schema and description hashes;
- read/job/write/composite/destructive/account/billing classification;
- required Profile, report and Campaign workflow coverage;
- unknown semantics fail-closed.

### Official capability inventory

- every enabled public Postman capability has an explicit project treatment;
- valid modes are autonomous, approval-gated, bounded job, read-only, compile/decompose, production acceptance or permanently blocked;
- end-to-end Campaign and locale-expansion workflows are compile/decompose, not direct black-box writes;
- billing, account administration and irreversible deletion remain permanently blocked.

### Regional NA/EU/FE routing

- US → NA, DE → EU and SG → FE mapping;
- canonical and explicitly named regional Hermes MCP Toolset discovery;
- regional source tags on live Catalog rows;
- matching FE Profile and FE report allowed;
- SG Profile with NA report blocked and audited;
- regional job without a known Profile blocked;
- Profile/account discovery allowed before local Profile binding;
- structural cross-region plan blocked;
- Executor and Verifier task region enforcement;
- one call/task cannot span multiple regions.

### Report and strategy path

- create/recover stable report transaction;
- submit, poll, succeed, download, validate and ingest;
- required action evidence for Amazon-driven transitions;
- invalid and backward transitions rejected;
- duplicate report key reuse;
- Profile/window/hash lineage;
- stale, future, incomplete and immature attribution data blocked;
- mature deterministic routine decisions generated;
- bounded bid, budget, placement, negative and harvest rules.

### Hermes and execution path

- Main cannot write;
- unbound Session cannot write;
- delegated task and role markers required;
- one current Executor per task;
- a different current Verifier Session required;
- Compare-And-Set pre-write read evidence;
- one entity per mutation while one Target may contain multiple valid expressions;
- atomic reservation and race loss;
- success, pending, failure, partial and unknown outcomes;
- uncertain mutation is never blindly replayed;
- post-tool callback uses the original result envelope;
- durable Outbox dedupe and later delivery;
- model fallback forces OBSERVE and disables execution.

### Approval and structural path

- AI/Agent Token can request but cannot approve;
- exact Payload Hash and typed phrase required;
- CSRF and Origin required for browser approval;
- expiry blocks new work;
- approval decision is consumed once;
- Campaign → Ad Group → Target → Product Ad dependency order;
- returned unique Amazon IDs are bound to logical objects;
- approved placeholders render only confirmed parent IDs;
- approval Hash remains stable after runtime ID binding;
- tampered parent ID, budget, bid, name, product, target or state blocked;
- missing or ambiguous created ID becomes uncertain and blocks dependents;
- different Verifier Session reads every resulting object;
- mismatch completes with issues, not false success.

### Web, storage and recovery

- password login and Session Cookie;
- Origin and CSRF rejection;
- full approval parameter and Hash display;
- exact approval mutation;
- logout;
- SQLite integrity and foreign-key checks;
- backup creation and restore validation;
- reservation expiry quarantine;
- restart reconciliation;
- storage pressure and bounded Outbox behavior.

## External acceptance items

The following 15 items were intentionally recorded as `EXTERNAL`, not failed or silently skipped:

1. Login with Amazon OAuth consent and refresh-token rotation.
2. Authenticated live MCP initialize and every tools/list page.
3. Live Tool descriptions, Schemas, hashes and removal/drift behavior.
4. Owner-supplied exact MCP endpoint and regional mapping evidence.
5. Real NA/EU/FE Profiles, currencies and manager relationships.
6. Real report submit, poll, GZIP download and parsing.
7. Real Amazon 429 and Retry-After behavior.
8. Marketing Stream delivery through the owner's AWS resources.
9. Test Account or tightly bounded Campaign hierarchy canary.
10. Independent Amazon persisted-state read-back.
11. A complete mature attribution-window shadow evaluation.
12. Pinned `hermes-agent==0.18.2` package load from an actual checkout.
13. CLI, Gateway, Cron and delegated-child interaction checks on every deployed surface.
14. 2C2G resource soak, reboot and systemd recovery.
15. Deployed HTTPS and backup-restore drill.

## Repository command

From an actual checkout:

```bash
bash scripts/run_full_sandbox.sh
```

Credentialed live MCP contract check:

```bash
FULL_SANDBOX_LIVE_MCP=1 \
AMAZON_ADS_MCP_ACCESS_TOKEN='...' \
bash scripts/run_full_sandbox.sh
```

A release may be called production-accepted only when the repository gate has no `FAIL`, all applicable `EXTERNAL` items have dated owner evidence, and the PR has a real CI run with allocated Steps and downloadable logs.
