from __future__ import annotations

from datetime import datetime, timezone
import json
import re
from typing import Any

from .db import Store
from .policy import Guardrails, classify_tool, match_planned_action, redact, redact_text, validate_write

TASK_MARKER = re.compile(r"\[ads-task:([a-f0-9]{8,32})\]", re.I)


class ControlService:
    def __init__(self, store: Store):
        self.store = store

    def create_task(self, payload: dict[str, Any], actor: str) -> dict[str, Any]:
        title = str(payload.get("title", "")).strip()
        if not title:
            raise ValueError("title is required")
        kind = str(payload.get("kind", "optimization")).strip().lower()
        if kind not in {"audit", "optimization", "recovery", "report", "maintenance"}:
            raise ValueError("unsupported task kind")
        write_allowed = bool(payload.get("write_allowed", kind in {"optimization", "recovery"}))
        return self.store.create_task(
            title=title,
            kind=kind,
            created_by=actor,
            parent_session_id=payload.get("parent_session_id"),
            write_allowed=write_allowed,
            payload=redact({
                "objective": str(payload.get("objective", ""))[:8000],
                "constraints": payload.get("constraints", {}),
                "evidence": payload.get("evidence", {}),
                "expected_actions": payload.get("expected_actions", []) if isinstance(payload.get("expected_actions", []), list) else [],
            }),
        )

    def bind_worker(self, payload: dict[str, Any]) -> dict[str, Any]:
        task_id = str(payload.get("task_id", ""))
        goal = str(payload.get("goal", ""))
        if not task_id:
            match = TASK_MARKER.search(goal)
            task_id = match.group(1) if match else ""
        if not task_id:
            raise ValueError("task_id or [ads-task:<id>] marker is required")
        session_id = str(payload.get("worker_session_id", ""))
        if not session_id:
            raise ValueError("worker_session_id is required")
        return self.store.bind_worker(
            task_id=task_id,
            parent_session_id=payload.get("parent_session_id"),
            worker_session_id=session_id,
            worker_subagent_id=payload.get("worker_subagent_id"),
            goal=goal,
            role=str(payload.get("role", "worker")),
            model=payload.get("model"),
        )

    def authorize_tool(self, payload: dict[str, Any]) -> dict[str, Any]:
        tool_name = str(payload.get("tool_name", ""))
        args = redact(payload.get("args", {}))
        session_id = str(payload.get("session_id") or payload.get("task_id") or "") or None
        operation = classify_tool(tool_name)
        worker = self.store.worker_for_session(session_id)
        actor_role = "worker" if worker else "main"
        task_id = worker.get("task_id") if worker else None
        settings = self.store.get_settings()
        allowed, reason = True, "not an Amazon Ads write operation"
        plan_key = None

        if operation == "unknown":
            allowed, reason = False, "unknown Amazon Ads operation is fail-closed"
        elif operation == "write":
            if settings.get("mode") != "autopilot" or not settings.get("execution_enabled"):
                allowed, reason = False, "autonomous execution is disabled"
            elif not worker:
                allowed, reason = False, "Amazon Ads writes are worker-only"
            else:
                task = self.store.get_task(task_id)
                if not task or not task["write_allowed"]:
                    allowed, reason = False, "task is not write-enabled"
                else:
                    guardrails = Guardrails.from_mapping(settings)
                    planned, plan_reason = match_planned_action(tool_name, args, task.get("payload", {}).get("expected_actions", []))
                    if settings.get("require_planned_writes", True) and not planned:
                        allowed, reason = False, plan_reason
                    else:
                        plan_key = planned.get("plan_key") if planned else None
                        allowed, reason = validate_write(tool_name, args, guardrails)
                        if allowed and planned and isinstance(planned.get("before"), (int, float)) and isinstance(planned.get("after"), (int, float)):
                            before = float(planned["before"])
                            after = float(planned["after"])
                            if before <= 0:
                                allowed, reason = False, "planned before value must be positive"
                            else:
                                pct = abs(after - before) / before * 100
                                limit = guardrails.max_budget_change_pct if "budget" in tool_name.lower() else guardrails.max_bid_change_pct
                                if pct > limit:
                                    allowed, reason = False, f"planned change {pct:.2f}% exceeds {limit}% guardrail"
                        if allowed and plan_key and self.store.successful_plan_exists(task_id, plan_key):
                            allowed, reason = False, "planned action already completed successfully"
                        if allowed and self.store.count_actions(task_id=task_id) >= guardrails.max_actions_per_task:
                            allowed, reason = False, "task action limit reached"
                        day_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
                        if allowed and self.store.count_actions(since=day_start) >= guardrails.max_actions_per_day:
                            allowed, reason = False, "daily action limit reached"
        elif operation == "read":
            allowed, reason = True, "read operation"

        self.store.touch_worker(session_id)
        action_id = self.store.record_action(
            task_id=task_id,
            session_id=session_id,
            actor_role=actor_role,
            phase="before",
            plan_key=plan_key,
            tool_name=tool_name,
            operation=operation,
            allowed=allowed,
            args=args,
            reason=reason,
        )
        if not allowed:
            self.store.event("warning", "tool.blocked", actor_role, task_id, f"Blocked {tool_name}: {reason}", {"action_id": action_id})
        return {
            "allowed": allowed,
            "reason": reason,
            "operation": operation,
            "actor_role": actor_role,
            "task_id": task_id,
            "action_id": action_id,
            "plan_key": plan_key,
        }

    def finish_tool(self, payload: dict[str, Any]) -> dict[str, Any]:
        tool_name = str(payload.get("tool_name", ""))
        session_id = str(payload.get("session_id") or payload.get("task_id") or "") or None
        worker = self.store.worker_for_session(session_id)
        task_id = worker.get("task_id") if worker else None
        plan_key = None
        if task_id:
            task = self.store.get_task(task_id)
            planned, _ = match_planned_action(tool_name, redact(payload.get("args", {})), task.get("payload", {}).get("expected_actions", []) if task else [])
            plan_key = planned.get("plan_key") if planned else None
        self.store.touch_worker(session_id)
        result = payload.get("result", "")
        if isinstance(result, str):
            try:
                result_text = json.dumps(redact(json.loads(result)), ensure_ascii=False)
            except (json.JSONDecodeError, TypeError):
                result_text = redact_text(result)
        else:
            result_text = json.dumps(redact(result), ensure_ascii=False)
        success = not any(marker in result_text.lower() for marker in ('"error"', "traceback", "exception"))
        action_id = self.store.record_action(
            task_id=task_id,
            session_id=session_id,
            actor_role="worker" if worker else "main",
            phase="after",
            plan_key=plan_key,
            tool_name=tool_name,
            operation=classify_tool(tool_name),
            allowed=True,
            success=success,
            args={},
            result_summary=result_text[:4000],
            duration_ms=int(payload.get("duration_ms") or 0),
        )
        return {"recorded": True, "action_id": action_id, "success": success}

    def context(self, session_id: str | None) -> dict[str, Any]:
        worker = self.store.worker_for_session(session_id)
        settings = self.store.get_settings()
        if worker:
            task = self.store.get_task(worker["task_id"])
            role = "worker"
        else:
            task, role = None, "main"
        return {
            "role": role,
            "mode": settings.get("mode"),
            "execution_enabled": settings.get("execution_enabled"),
            "task": task,
            "instructions": (
                "主控：负责读取、分析、创建任务、delegate_task 和复核；不得直接执行 Amazon Ads 写操作。"
                if role == "main" else
                "Worker：只执行绑定任务；严格遵守任务目标和控制面 guardrails，完成后读回验证。"
            ),
        }
