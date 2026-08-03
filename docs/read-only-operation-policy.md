# Observe and fail-closed policy

Read-only operation remains a first-class safety mode inside the full autopilot.

## `observe` mode

The controller and Workers may collect, calculate, diagnose, plan, and report, but every Amazon Ads write is rejected by the control plane. Use this mode while validating a new OAuth profile, MCP schema change, or strategy revision.

## Controller outage

The Hermes plugin applies a local fallback boundary:

- clearly classified Ads reads may continue for diagnosis;
- Ads writes and semantically unknown Ads operations are blocked;
- unrelated Hermes tools are unaffected.

This prevents a control-plane outage from silently removing write protection.

## Read classification

Only names with clear read semantics such as `list`, `query`, `get`, `retrieve`, `check`, `describe`, `search`, `report`, or `status` are accepted as reads. An Ads-like tool with ambiguous semantics is classified as unknown and fails closed.

## Audit-only sessions

`config/amazon-ads-readonly-tools.example.yaml` remains available for a separately constrained audit session. Validate every listed tool against the live Amazon MCP schema before using it; natural-language prompts are not a security boundary.
