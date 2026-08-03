# Observe and paused policy

`OBSERVE` permits catalog sync, reports/data jobs, reads, deterministic planning and Web display. It does not permit advertising mutations. `PAUSED` blocks every Amazon Ads MCP operation, including reads and report jobs. `AUTOPILOT` permits only task-bound Executor writes that pass all immutable safety gates; Main and Verifier remain read-only in every mode.
