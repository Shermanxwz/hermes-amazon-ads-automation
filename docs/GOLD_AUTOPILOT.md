# Gold Autopilot operating model

This branch treats Amazon Ads as a closed-loop control system rather than a set
of independent `if ACOS > X` automations.

## Objective and boundary

The autonomous objective is advertising-only:

- control waste and converge toward the configured target ACOS;
- preserve enough bounded exploration to keep discovering scalable traffic;
- avoid profit, inventory, refund, pricing, Seller Central browser automation,
  creative generation, and other business-data dependencies;
- require no per-action human approval during normal operation.

The existing Web remains an observation and emergency-control surface. It is not
turned into a second configuration product.

## Decision loop

Every cycle follows the same deterministic sequence:

1. verify trusted source, report window, attribution maturity and account totals;
2. reject malformed rows without silently converting invalid numbers to zero;
3. infer entity lifecycle and suppress actions inside the post-change cooldown;
4. calculate evidence confidence from clicks, orders and spend;
5. diagnose waste, over-ACOS, budget pressure, placement loss or scalable traffic;
6. emit only bounded, single-entity, independently verifiable writes;
7. reserve atomically, execute once, record the Amazon result durably;
8. read Amazon again through a separate verifier session;
9. commit, quarantine uncertainty, or surface a mismatch for recovery.

## Strategy upgrades

The optimizer now includes:

- confidence-gated waste reduction and scaling;
- lifecycle states: explore, learning, stable, scale, declining and recovery;
- target-CPC calculation from target ACOS, conversion rate and order value
  already present in the advertising report;
- cooldown suppression after bid, budget or placement changes;
- bid bounds and per-cycle decision limits;
- campaign budget expansion for capped winners;
- campaign budget containment for high-ACOS campaigns that are actively spending;
- independent placement increase and decrease for Top of Search, Product Pages
  and Rest of Search;
- exact-target overlap detection that suppresses duplicate scaling;
- search-term harvest payloads that require create-and-verify before any source
  negative is allowed;
- row-level rejection reasons and strategy summaries for Web/audit display;
- correct parsing of textual boolean configuration values.

## Durable result callback

The Hermes post-tool callback does not call Amazon again. It delivers the
original Amazon result to the control plane with a deterministic event ID.

If the control plane is unavailable:

- the result is written to a mode-0600 JSONL outbox;
- duplicate callbacks collapse to the same event ID;
- later successful catalog synchronization flushes pending results;
- the original reservation token, decision ID, tool call ID and response are
  preserved;
- recovery never replays the underlying Amazon mutation.

This closes the failure window where Amazon accepted a write but the plugin
process or local network failed before the controller recorded the response.

## Official contract compiler

`scripts/sync_official_contracts.py` now compiles the public Postman collection
into a semantic manifest rather than checking folder keywords only.

The manifest contains:

- normalized HTTP method and path without hosts, query strings or secret values;
- API version, header names/variables, body mode and JSON field paths;
- asynchronous workflow hints for report, poll, status, download, snapshot and
  export endpoints;
- core and extended capability coverage;
- per-endpoint contract IDs and a collection-wide semantic fingerprint;
- optional baseline comparison that fails on removed or materially changed
  contracts.

Core capabilities remain the default CI gate for compatibility. Extended
capabilities can be made strict with `--strict-extended`.

## Deliberately excluded automation

The following remain outside autonomous scope:

- billing and account administration;
- delete/archive operations;
- unknown, composite or schema-drifted MCP writes;
- blind application of Amazon recommendations;
- Sponsored TV creation and creative generation;
- business-profit decisions requiring cost, refund, inventory or organic sales;
- Seller Central browser automation.

## Production acceptance that cannot be simulated

No repository-only change can prove advertiser-specific production behavior.
Before switching a real profile from Observe to Autopilot, the owner environment
still must complete:

1. OAuth refresh and real Profile/manager-account visibility;
2. authenticated MCP `initialize` and `tools/list` schema synchronization;
3. real asynchronous report creation, polling, download and throttling behavior;
4. bounded Amazon test-account or canary mutation with independent read-back;
5. restart/reboot recovery with a pending result outbox entry;
6. sustained Observe/shadow evaluation on the account's historical data;
7. VPS resource soak under the actual report sizes and Hermes model settings.

The controller remains fail-closed until those credentialed checks pass.
