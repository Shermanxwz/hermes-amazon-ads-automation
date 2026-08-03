from __future__ import annotations

from datetime import datetime, timezone
import json
import math
import re
from typing import Any

from .catalog import ToolDescriptor, descriptor_from_payload, is_registered_amazon_tool
from .db import Store
from .outcome import parse_tool_outcome
from .policy import redact, redact_text
from .strategy import OptimizationEngine, StrategyPolicy
from .schema_validation import validate_instance

TASK_MARKER = re.compile(r"\[ads-task:([a-f0-9]{8,32})\]", re.I)
ROLE_MARKER = re.compile(r"\[ads-role:(executor|verifier)\]", re.I)
UTC = timezone.utc


def _walk_values(value: Any):
    if isinstance(value, dict):
        for key, item in value.items():
            yield str(key), item
            yield from _walk_values(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_values(item)


def _contains_value(value: Any, wanted: Any) -> bool:
    needle = str(wanted).strip().lower()
    if not needle:
        return True
    for _key, item in _walk_values(value):
        if isinstance(item, (str, int, float)) and str(item).strip().lower() == needle:
            return True
    return needle in json.dumps(value, ensure_ascii=False, default=str).lower()


def _key_norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _find_field_values(value: Any, wanted: str) -> list[Any]:
    aliases = {_key_norm(part) for part in wanted.split("|") if part.strip()}
    found: list[Any] = []
    for key, item in _walk_values(value):
        key_normalized = _key_norm(key)
        if any(key_normalized == alias or key_normalized.endswith(alias) for alias in aliases):
            found.append(item)
    return found


def _write_batch_violation(value: Any, maximum: int, path: str = "$") -> str | None:
    if isinstance(value, list):
        if len(value) > maximum:
            return f"{path} contains {len(value)} items; autonomous write batch limit is {maximum}"
        for index, item in enumerate(value):
            violation = _write_batch_violation(item, maximum, f"{path}[{index}]")
            if violation:
                return violation
    elif isinstance(value, dict):
        for key, item in value.items():
            violation = _write_batch_violation(item, maximum, f"{path}.{key}")
            if violation:
                return violation
    return None


def _numeric_equal(left: Any, right: Any, *, tolerance: float = 0.01) -> bool:
    try:
        return math.isclose(float(left), float(right), rel_tol=1e-6, abs_tol=tolerance)
    except (TypeError, ValueError):
        return str(left) == str(right)


def _expected_differences(expected: dict[str, Any], actual: dict[str, Any]) -> dict[str, Any]:
    differences: dict[str, Any] = {}
    for field, wanted in expected.items():
        values = _find_field_values(actual, field)
        if not values:
            differences[field] = {"expected": wanted, "actual": "[missing]"}
            continue
        if not any(_numeric_equal(item, wanted) for item in values):
            differences[field] = {"expected": wanted, "actual": values[:10]}
    return differences


def _walk_scalars(value: Any, path: str = "") -> list[tuple[str, Any]]:
    found: list[tuple[str, Any]] = []
    if isinstance(value, dict):
        for key, item in value.items():
            child = f"{path}.{key}" if path else str(key)
            found.extend(_walk_scalars(item, child))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(_walk_scalars(item, f"{path}[{index}]"))
    else:
        found.append((path.lower(), value))
    return found


def _first_numeric(items: list[tuple[str, Any]], names: tuple[str, ...]) -> float | None:
    for path, value in items:
        leaf = path.rsplit(".", 1)[-1].split("[", 1)[0].replace("_", "").lower()
        if any(leaf == name.replace("_", "").lower() for name in names):
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
    return None


class ControlService:
    """Deterministic Amazon Ads control, execution and verification boundary."""

    def __init__(self, store: Store):
        self.store = store
        self.engine = OptimizationEngine()

    # Catalog and planning -------------------------------------------------
    def sync_catalog(self, payload: dict[str, Any]) -> dict[str, Any]:
        raw_tools = payload.get("tools")
        if not isinstance(raw_tools, list) or not raw_tools:
            raise ValueError("tools must be a non-empty array")
        tools: list[ToolDescriptor] = []
        for raw in raw_tools:
            if not isinstance(raw, dict):
                raise ValueError("each catalog tool must be an object")
            descriptor = descriptor_from_payload(raw)
            if not is_registered_amazon_tool(descriptor.registered_name):
                raise ValueError(f"tool is outside mcp-amazon-ads: {descriptor.registered_name}")
            tools.append(descriptor)
        return self.store.sync_catalog(tools)

    def plan_cycle(self, payload: dict[str, Any], actor: str) -> dict[str, Any]:
        snapshot = payload.get("snapshot") if isinstance(payload.get("snapshot"), dict) else payload
        profile = snapshot.get("profile") if isinstance(snapshot.get("profile"), dict) else {}
        profile_id = str(profile.get("profile_id") or profile.get("id") or "").strip()
        if not profile_id:
            raise ValueError("snapshot.profile.profile_id is required")
        existing = self.store.get_profile(profile_id)
        settings = self.store.get_settings()
        merged_policy = dict(settings)
        if existing and isinstance(existing.get("strategy"), dict):
            merged_policy.update(existing["strategy"])
        if isinstance(payload.get("policy"), dict):
            merged_policy.update(payload["policy"])
        plan = self.engine.plan(snapshot, StrategyPolicy.from_mapping(merged_policy))
        result = self.store.create_cycle(
            profile=plan.profile,
            source=str(snapshot.get("source") or payload.get("source") or "amazon-ads-mcp"),
            window=plan.window,
            data_quality=plan.data_quality,
            kpis=plan.kpis,
            snapshot=snapshot,
            decisions=[decision.as_dict() for decision in plan.decisions],
            created_by=actor,
        )
        if not plan.data_quality.get("eligible_for_writes"):
            self.store.alert(
                "warning", "DATA_NOT_MATURE", profile_id, None, None,
                "Optimization cycle is observe-only because data is incomplete or attribution is immature",
                plan.data_quality,
            )
        return result

    def create_task(self, payload: dict[str, Any], actor: str) -> dict[str, Any]:
        cycle_id = str(payload.get("cycle_id") or "").strip()
        if cycle_id:
            return self.store.create_task_from_cycle(
                cycle_id,
                actor,
                payload.get("parent_session_id"),
                limit=int(payload.get("limit") or 25),
            )
        # Kept for bounded recovery/manual plans. It never turns arbitrary prose into a write.
        title = str(payload.get("title") or "").strip()
        decision_ids = payload.get("decision_ids") if isinstance(payload.get("decision_ids"), list) else []
        if not title or not decision_ids:
            raise ValueError("cycle_id or title plus decision_ids is required")
        decisions = [self.store.get_decision(str(item)) for item in decision_ids]
        if any(item is None for item in decisions):
            raise ValueError("one or more decision_ids do not exist")
        cycles = {str(item["cycle_id"]) for item in decisions if item}
        profiles = {str(item["profile_id"]) for item in decisions if item}
        if len(cycles) != 1 or len(profiles) != 1 or any(item.get("status") != "planned" or item.get("task_id") for item in decisions if item):
            raise ValueError("manual task decisions must be unassigned planned decisions from one cycle and profile")
        return self.store.create_task(
            title=title,
            kind=str(payload.get("kind") or "optimization"),
            created_by=actor,
            parent_session_id=payload.get("parent_session_id"),
            write_allowed=bool(payload.get("write_allowed", True)),
            payload={
                "objective": str(payload.get("objective") or title)[:8000],
                "decision_ids": decision_ids,
            },
            cycle_id=str(decisions[0]["cycle_id"]),
            decision_ids=[str(item) for item in decision_ids],
        )

    # Hermes worker binding ------------------------------------------------
    def bind_worker(self, payload: dict[str, Any]) -> dict[str, Any]:
        goal = str(payload.get("goal") or "")
        task_id = str(payload.get("task_id") or "").strip()
        if not task_id:
            match = TASK_MARKER.search(goal)
            task_id = match.group(1) if match else ""
        if not task_id:
            raise ValueError("task_id or [ads-task:<id>] marker is required")
        session_id = str(payload.get("worker_session_id") or "").strip()
        if not session_id:
            raise ValueError("worker_session_id is required")
        role = str(payload.get("role") or "").lower().strip()
        role_match = ROLE_MARKER.search(goal)
        marker_role = role_match.group(1).lower() if role_match else ""
        if not role:
            role = marker_role
        if role not in {"executor", "verifier"}:
            raise ValueError("explicit executor or verifier role is required")
        if marker_role and marker_role != role:
            raise ValueError("worker role does not match delegated goal marker")
        return self.store.bind_worker(
            task_id=task_id,
            parent_session_id=payload.get("parent_session_id"),
            worker_session_id=session_id,
            worker_subagent_id=payload.get("worker_subagent_id"),
            goal=goal,
            role=role,
            model=payload.get("model"),
        )

    def _match_decision(self, task_id: str, tool: dict[str, Any], args: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        candidates = self.store.list_decisions(task_id=task_id, status="planned", limit=500)
        matches: list[dict[str, Any]] = []
        for decision in candidates:
            if decision.get("expected_family") != tool.get("family"):
                continue
            entity_id = decision.get("entity_id")
            if entity_id and not _contains_value(args, entity_id):
                continue
            payload = decision.get("payload") if isinstance(decision.get("payload"), dict) else {}
            field = str(payload.get("field") or "")
            if field and "after" in payload:
                values = _find_field_values(args, field)
                if not values or not any(_numeric_equal(item, payload["after"]) for item in values):
                    continue
            match_fields = payload.get("match_fields") if isinstance(payload.get("match_fields"), dict) else {}
            field_mismatch = False
            for aliases, wanted in match_fields.items():
                values = _find_field_values(args, str(aliases))
                if not values or not any(_numeric_equal(item, wanted) for item in values):
                    field_mismatch = True
                    break
            if field_mismatch:
                continue
            expected_tool = str(payload.get("tool_name") or "").strip()
            if expected_tool and expected_tool != tool.get("registered_name"):
                continue
            matches.append(decision)
        if not matches:
            return None, "write does not match a planned deterministic decision"
        if len(matches) > 1:
            return None, "write ambiguously matches multiple decisions"
        return matches[0], "matched deterministic decision"

    def _guardrail_check(self, decision: dict[str, Any], tool: dict[str, Any], settings: dict[str, Any]) -> tuple[bool, str]:
        payload = decision.get("payload") if isinstance(decision.get("payload"), dict) else {}
        lowered = str(tool.get("native_name") or "").lower()
        family = str(tool.get("family") or "")
        risk = str(tool.get("risk") or "critical")
        if risk == "critical":
            return False, "critical-risk Amazon Ads tools are not autonomous"
        if settings.get("block_account_admin", True) and family in {"account_admin", "billing"}:
            return False, "account administration and billing are blocked"
        if settings.get("block_deletes", True) and any(word in lowered for word in ("delete", "archive", "remove")):
            return False, "delete/archive/remove operations are blocked"
        before, after = payload.get("before"), payload.get("after")
        if isinstance(before, (int, float)) and isinstance(after, (int, float)):
            if float(before) <= 0:
                return False, "planned before value must be positive"
            change = abs(float(after) - float(before)) / float(before) * 100
            limit = float(settings.get("max_budget_change_pct" if "budget" in lowered or payload.get("field") == "budget" else "max_bid_change_pct", 20))
            if change > limit + 1e-9:
                return False, f"planned change {change:.2f}% exceeds {limit:g}% guardrail"
        if decision.get("action_type") == "create_campaign" and not settings.get("allow_campaign_creation", False):
            return False, "campaign creation is disabled"
        return True, "within deterministic guardrails"

    # Tool boundary --------------------------------------------------------
    def authorize_tool(self, payload: dict[str, Any]) -> dict[str, Any]:
        tool_name = str(payload.get("tool_name") or "")
        args = payload.get("args") if isinstance(payload.get("args"), dict) else {}
        safe_args = redact(args)
        session_id = str(payload.get("session_id") or payload.get("task_id") or "") or None
        tool_call_id = str(payload.get("tool_call_id") or "") or None
        worker = self.store.worker_for_session(session_id)
        actor_role = worker.get("role") if worker else "main"
        task_id = worker.get("task_id") if worker else None
        settings = self.store.get_settings()
        tool = self.store.get_tool(tool_name)
        decision = None
        reservation_token = None

        if not is_registered_amazon_tool(tool_name):
            return {"allowed": True, "reason": "outside Amazon Ads MCP", "operation": "other", "actor_role": actor_role}
        if not tool or not tool.get("enabled"):
            allowed, reason, operation = False, "tool is absent from the synchronized Hermes Amazon Ads catalog", "unknown"
        else:
            operation = str(tool.get("semantic") or "unknown")
            schema = tool.get("schema") if isinstance(tool.get("schema"), dict) else {}
            schema_errors = validate_instance(args, schema)
            allowed, reason = True, "read operation"
            if operation in {"write", "job"} and not schema:
                allowed, reason = False, "live MCP schema is unavailable for a stateful operation"
            elif schema_errors:
                allowed, reason = False, "MCP arguments violate live schema: " + "; ".join(schema_errors[:5])
            elif operation == "unknown":
                allowed, reason = False, "catalog semantic is unknown; fail-closed"
            elif settings.get("mode") == "paused":
                allowed, reason = False, "Amazon Ads activity is paused"
            elif operation == "read":
                allowed, reason = True, "cataloged read operation"
            elif operation == "job":
                if actor_role != "main":
                    allowed, reason = False, "report/export jobs are main-controller only"
                elif not settings.get("allow_data_jobs", True):
                    allowed, reason = False, "report/export jobs are disabled"
                elif settings.get("catalog_drift_blocks_writes", True) and tool.get("drifted"):
                    allowed, reason = False, "MCP job schema drift is unacknowledged"
                elif self.store.count_actions(
                    since=datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0).isoformat(),
                    operations=("job",),
                ) >= int(settings.get("max_data_jobs_per_day", 30)):
                    allowed, reason = False, "daily report/export job limit reached"
                else:
                    allowed, reason = True, "bounded report/export data job"
            elif operation == "write":
                if settings.get("catalog_drift_blocks_writes", True) and tool.get("drifted"):
                    allowed, reason = False, "MCP tool schema drift is unacknowledged"
                elif settings.get("mode") != "autopilot" or not settings.get("execution_enabled"):
                    allowed, reason = False, "autonomous execution is disabled"
                elif not worker or actor_role != "executor":
                    allowed, reason = False, "Amazon Ads writes require a bound executor"
                else:
                    task = self.store.get_task(task_id)
                    if not task or not task.get("write_allowed"):
                        allowed, reason = False, "task is not write-enabled"
                    else:
                        batch_violation = _write_batch_violation(args, int(settings.get("max_write_batch_size", 1)))
                        if batch_violation:
                            allowed, reason = False, batch_violation
                        else:
                            decision, reason = self._match_decision(task_id, tool, args)
                            allowed = decision is not None
                        if allowed and decision:
                            allowed, reason = self._guardrail_check(decision, tool, settings)
                        if allowed and decision:
                            try:
                                decision = self.store.reserve_decision(
                                    decision["id"], task_id, str(session_id), int(settings.get("reservation_ttl_seconds", 900)),
                                    int(settings.get("decision_cooldown_hours", 24)) * 3600,
                                    max_actions_per_task=int(settings.get("max_actions_per_task", 50)),
                                    max_actions_per_day=int(settings.get("max_actions_per_day", 250)),
                                    max_campaign_creates_per_day=int(settings.get("max_campaign_creates_per_day", 2)),
                                )
                                reservation_token = decision.get("reservation_token")
                                reason = "decision atomically reserved for executor"
                            except (ValueError, KeyError) as exc:
                                allowed, reason = False, str(exc)

        self.store.touch_worker(session_id)
        action_id = self.store.record_action(
            decision_id=decision.get("id") if decision else None,
            task_id=task_id,
            session_id=session_id,
            tool_call_id=tool_call_id,
            actor_role=str(actor_role),
            phase="before",
            tool_name=tool_name,
            operation=operation,
            allowed=allowed,
            plan_key=decision.get("plan_key") if decision else None,
            reservation_token=reservation_token,
            args=safe_args,
            reason=reason,
        )
        if not allowed:
            self.store.event("warning", "tool.blocked", str(actor_role), task_id, f"Blocked {tool_name}: {reason}", {"action_id": action_id})
        return {
            "allowed": allowed,
            "reason": reason,
            "operation": operation,
            "actor_role": actor_role,
            "task_id": task_id,
            "action_id": action_id,
            "decision_id": decision.get("id") if decision else None,
            "plan_key": decision.get("plan_key") if decision else None,
            "reservation_token": reservation_token,
            "tool": {key: tool.get(key) for key in ("native_name", "family", "risk", "schema_hash")} if tool else None,
        }

    def finish_tool(self, payload: dict[str, Any]) -> dict[str, Any]:
        tool_name = str(payload.get("tool_name") or "")
        session_id = str(payload.get("session_id") or payload.get("task_id") or "") or None
        worker = self.store.worker_for_session(session_id)
        actor_role = worker.get("role") if worker else "main"
        task_id = worker.get("task_id") if worker else payload.get("task_id")
        decision_id = str(payload.get("decision_id") or "") or None
        reservation_token = str(payload.get("reservation_token") or "") or None
        result = payload.get("result")
        outcome = parse_tool_outcome(result)
        tool = self.store.get_tool(tool_name)
        operation = str(tool.get("semantic")) if tool else "unknown"
        if operation == "write":
            if not decision_id or not reservation_token:
                raise ValueError("write result requires decision_id and reservation_token")
            self.store.mark_execution(
                decision_id=decision_id,
                reservation_token=reservation_token,
                tool_name=tool_name,
                outcome=outcome.status,
                result=outcome.payload,
                failure=None if outcome.status in {"success", "pending"} else outcome.summary,
            )
        self.store.touch_worker(session_id)
        action_id = self.store.record_action(
            decision_id=decision_id,
            task_id=str(task_id) if task_id else None,
            session_id=session_id,
            tool_call_id=str(payload.get("tool_call_id") or "") or None,
            actor_role=str(actor_role),
            phase="after",
            tool_name=tool_name,
            operation=operation,
            allowed=True,
            plan_key=str(payload.get("plan_key") or "") or None,
            reservation_token=reservation_token,
            args={},
            success=outcome.terminal_success,
            outcome_status=outcome.status,
            structured_result=outcome.structured,
            reason=outcome.summary,
            result_summary=json.dumps(redact(outcome.payload), ensure_ascii=False, default=str)[:4000],
            duration_ms=int(payload.get("duration_ms") or 0),
        )
        if operation == "write" and outcome.status not in {"success", "pending"}:
            decision = self.store.get_decision(decision_id) or {}
            self.store.alert(
                "critical", "WRITE_OUTCOME_UNCONFIRMED", decision.get("profile_id"), str(task_id) if task_id else None,
                decision_id, f"Write was not confirmed as successful: {outcome.summary}", {"tool": tool_name, "status": outcome.status},
            )
        return {"recorded": True, "action_id": action_id, "outcome": outcome.__dict__}

    # Independent verification --------------------------------------------
    def verify_decision(self, payload: dict[str, Any]) -> dict[str, Any]:
        decision_id = str(payload.get("decision_id") or "").strip()
        session_id = str(payload.get("session_id") or "").strip()
        if not decision_id or not session_id:
            raise ValueError("decision_id and session_id are required")
        worker = self.store.worker_for_session(session_id)
        if not worker or worker.get("role") != "verifier":
            raise ValueError("only a bound verifier may verify a decision")
        decision = self.store.get_decision(decision_id)
        if not decision or decision.get("task_id") != worker.get("task_id"):
            raise ValueError("decision does not belong to verifier task")
        expected = decision.get("payload", {}).get("expected_state")
        if not isinstance(expected, dict) or not expected:
            expected = {
                str(decision.get("payload", {}).get("field")): decision.get("payload", {}).get("after")
            } if decision.get("payload", {}).get("field") else {}
        actual = payload.get("actual") if isinstance(payload.get("actual"), dict) else {}
        differences = _expected_differences(expected, actual)
        status = "verified" if expected and not differences else "mismatch" if actual else "not_found"
        return self.store.record_verification(
            decision_id=decision_id,
            task_id=worker["task_id"],
            verifier_session_id=session_id,
            expected=expected,
            actual=actual,
            differences=differences,
            status=status,
            message=str(payload.get("message") or ("state matches" if status == "verified" else "state does not match expected write")),
        )

    def finalize_task(self, payload: dict[str, Any], actor: str) -> dict[str, Any]:
        task_id = str(payload.get("task_id") or "").strip()
        if not task_id:
            raise ValueError("task_id is required")
        return self.store.finalize_task(task_id, actor, str(payload.get("summary") or ""))

    # Context and ingestion ------------------------------------------------
    def context(self, session_id: str | None) -> dict[str, Any]:
        worker = self.store.worker_for_session(session_id)
        settings = self.store.get_settings()
        task = self.store.get_task(worker["task_id"]) if worker else None
        role = worker.get("role") if worker else "main"
        decisions = self.store.list_decisions(task_id=task["id"], limit=100) if task else []
        instructions = {
            "main": "Main: synchronize the exact MCP catalog, collect mature reports, plan a deterministic cycle, create a task, delegate an executor, then delegate a separate verifier. Never call Amazon Ads write tools.",
            "executor": "Executor: perform only planned decisions for the bound task. Each write is atomically reserved by pre_tool_call. Do not verify your own writes.",
            "verifier": "Verifier: read Amazon state independently, call ads_control_verify_decision for each executed decision, and never call write tools.",
        }[str(role)]
        return {
            "role": role,
            "mode": settings.get("mode"),
            "execution_enabled": settings.get("execution_enabled"),
            "catalog": {"tools": len(self.store.list_tools()), "drifted": sum(1 for item in self.store.list_tools() if item.get("drifted"))},
            "task": task,
            "decisions": decisions,
            "instructions": instructions,
        }

    def ingest_stream(self, payload: dict[str, Any]) -> dict[str, int]:
        events = [item for item in (payload.get("events") if isinstance(payload.get("events"), list) else [payload]) if isinstance(item, dict)]
        result = self.store.ingest_stream_events(events)
        for event in events:
            profile_id = str(event.get("profile_id") or "") or None
            dataset = str(event.get("dataset_id") or "").lower()
            body = event.get("payload") if isinstance(event.get("payload"), dict) else event
            scalars = _walk_scalars(body)
            usage = _first_numeric(scalars, ("budgetUsagePercent", "budget_usage_percent", "budgetUtilizationPercent"))
            if usage is None:
                spend = _first_numeric(scalars, ("spend", "cost"))
                budget = _first_numeric(scalars, ("budget", "dailyBudget", "budgetAmount"))
                if spend is not None and budget and budget > 0:
                    usage = spend / budget * 100
            if usage is not None and usage >= 95:
                self.store.alert_once("warning", "BUDGET_NEAR_EXHAUSTION", profile_id, None, None,
                                      f"Amazon Ads budget usage reached {usage:.1f}%", {"dataset_id": dataset, "usage_percent": usage})
            status_values = [str(value).lower() for path, value in scalars if path.endswith(("status", "eligibilitystatus", "servingstatus"))]
            if any(value in {"ineligible", "not_eligible", "noteligible", "suspended"} for value in status_values) or "ineligible" in dataset:
                self.store.alert_once("critical", "AD_INELIGIBLE", profile_id, None, None,
                                      "Amazon Ads reported an ineligible or suspended advertising entity", {"dataset_id": dataset, "statuses": status_values[:10]})
        return result
