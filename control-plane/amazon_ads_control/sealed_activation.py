from __future__ import annotations

from datetime import datetime, timezone
import json
import re
from typing import Any

from .schema_validation import validate_instance
from .sealed_envelope import canonical
from .sealed_plan import validate_standing_plan

_INSTALLED = False

_CREATE_SPECS: dict[str, tuple[str, int, tuple[str, ...]]] = {
    "create_ad": ("ad", 10, ("productad", "product_ad", "ad")),
    "create_target": ("target", 10, ("producttarget", "product_target", "target")),
    "create_keyword": ("target", 10, ("keyword",)),
    "create_ad_group": ("ad_group", 20, ("adgroup", "ad_group")),
    "create_campaign": ("campaign", 30, ("campaign",)),
}
_ID_ALIASES: dict[str, set[str]] = {
    "campaign": {"campaignid", "campaign_id", "id"},
    "ad_group": {"adgroupid", "ad_group_id", "id"},
    "ad": {"adid", "productadid", "product_ad_id", "id"},
    "target": {
        "targetid", "target_id", "keywordid", "keyword_id",
        "targetingclauseid", "producttargetingid", "id",
    },
}
_STATE_ALIASES = {"state", "status"}
_PROFILE_ALIASES = {"profileid", "profile_id", "advertisingprofileid"}
_FORBIDDEN_TOOL_WORDS = {"create", "delete", "archive", "remove", "workflow", "bulk", "batch", "composite"}
_UPDATE_TOOL_WORDS = {"update", "enable", "resume", "set", "state", "status"}
_PLACEHOLDER = re.compile(r"^\{\{decision:([A-Za-z0-9_.:-]{1,240})\.entity_id\}\}$")


def _norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def _walk(value: Any):
    if isinstance(value, dict):
        for key, item in value.items():
            yield str(key), item
            yield from _walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk(item)


def _source_values(arguments: dict[str, Any]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for key, item in _walk(arguments):
        if isinstance(item, (str, int, float, bool)) or item is None:
            values.setdefault(_norm(key), item)
    return values


def _schema_root(schema: dict[str, Any]) -> dict[str, Any]:
    parameters = schema.get("parameters")
    return parameters if isinstance(parameters, dict) else schema


def _literal(schema: dict[str, Any]) -> Any:
    if "const" in schema:
        return schema["const"]
    if "default" in schema:
        return schema["default"]
    enum = schema.get("enum")
    if isinstance(enum, list) and len(enum) == 1:
        return enum[0]
    return None


def _build_object(
    schema: dict[str, Any],
    source: dict[str, Any],
    family: str,
    placeholder: str,
    profile_id: str,
) -> dict[str, Any] | None:
    properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
    required = {str(item) for item in schema.get("required", []) if isinstance(item, str)}
    output: dict[str, Any] = {}
    saw_id = False
    saw_state = False
    for key, spec in properties.items():
        if not isinstance(spec, dict):
            spec = {}
        normalized = _norm(key)
        value: Any = None
        available = False
        if normalized in {_norm(item) for item in _ID_ALIASES[family]}:
            value, available, saw_id = placeholder, True, True
        elif normalized in {_norm(item) for item in _STATE_ALIASES}:
            value, available, saw_state = "ENABLED", True, True
        elif normalized in {_norm(item) for item in _PROFILE_ALIASES}:
            value, available = profile_id, True
        elif normalized in source:
            value, available = source[normalized], True
        else:
            literal = _literal(spec)
            if literal is not None:
                value, available = literal, True
        if available:
            output[key] = value
        elif key in required:
            return None
    if not saw_id or not saw_state:
        return None
    return output


def _build_activation_args(
    tool: dict[str, Any],
    create_action: dict[str, Any],
    profile_id: str,
    family: str,
) -> dict[str, Any] | None:
    schema = _schema_root(tool.get("schema") if isinstance(tool.get("schema"), dict) else {})
    properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
    required = {str(item) for item in schema.get("required", []) if isinstance(item, str)}
    source_args = create_action.get("arguments") if isinstance(create_action.get("arguments"), dict) else {}
    source = _source_values(source_args)
    plan_key = str(create_action.get("plan_key") or "").strip()
    if not plan_key:
        return None
    placeholder = f"{{{{decision:{plan_key}.entity_id}}}}"
    tokens = _CREATE_SPECS[str(create_action.get("action_type") or "")][2]

    array_candidates: list[tuple[int, str, dict[str, Any]]] = []
    for key, spec in properties.items():
        if not isinstance(spec, dict) or spec.get("type") != "array" or not isinstance(spec.get("items"), dict):
            continue
        normalized = _norm(key)
        score = 100 if any(_norm(token) in normalized for token in tokens) else 0
        if key in required:
            score += 10
        array_candidates.append((score, key, spec))
    array_candidates.sort(reverse=True)

    if array_candidates and (array_candidates[0][0] > 0 or len(array_candidates) == 1):
        _score, collection_key, collection_schema = array_candidates[0]
        item = _build_object(collection_schema["items"], source, family, placeholder, profile_id)
        if item is None:
            return None
        output: dict[str, Any] = {collection_key: [item]}
        for key in required - {collection_key}:
            spec = properties.get(key) if isinstance(properties.get(key), dict) else {}
            normalized = _norm(key)
            if normalized in {_norm(item) for item in _PROFILE_ALIASES}:
                output[key] = profile_id
            elif normalized in source:
                output[key] = source[normalized]
            else:
                literal = _literal(spec)
                if literal is None:
                    return None
                output[key] = literal
    else:
        output = _build_object(schema, source, family, placeholder, profile_id) or {}
        if not output:
            return None

    errors = validate_instance(output, tool.get("schema") if isinstance(tool.get("schema"), dict) else {})
    encoded = canonical(output)
    if errors or placeholder not in encoded or "ENABLED" not in encoded:
        return None
    return output


def _activation_tool(
    store: Any,
    create_action: dict[str, Any],
    profile_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    action_type = str(create_action.get("action_type") or "")
    family, _rank, tokens = _CREATE_SPECS[action_type]
    candidates: list[tuple[int, str, dict[str, Any], dict[str, Any]]] = []
    for tool in store.list_tools(2000):
        if not tool or not tool.get("enabled") or tool.get("drifted"):
            continue
        if tool.get("semantic") != "write" or str(tool.get("family") or "") != family:
            continue
        native = str(tool.get("native_name") or "").lower()
        words = {_norm(item) for item in re.split(r"[^a-z0-9]+", native) if item}
        if any(word in words for word in _FORBIDDEN_TOOL_WORDS):
            continue
        if not any(word in words for word in _UPDATE_TOOL_WORDS):
            continue
        args = _build_activation_args(tool, create_action, profile_id, family)
        if args is None:
            continue
        compact = _norm(native)
        score = 30 if "update" in words else 20
        score += 20 if any(_norm(token) in compact for token in tokens) else 0
        score += 5 if "enable" in words else 0
        candidates.append((score, str(tool.get("registered_name") or ""), tool, args))
    if not candidates:
        raise ValueError(
            f"{action_type} has no enabled, schema-valid atomic activation tool; "
            "full-managed creation cannot guarantee PAUSED-to-ENABLED closure"
        )
    candidates.sort(key=lambda item: (-item[0], item[1]))
    if len(candidates) > 1 and candidates[0][0] == candidates[1][0]:
        raise ValueError(
            f"{action_type} activation tool selection is ambiguous between "
            f"{candidates[0][1]} and {candidates[1][1]}"
        )
    return candidates[0][2], candidates[0][3]


def _activation_action(
    create_action: dict[str, Any],
    tool: dict[str, Any],
    arguments: dict[str, Any],
) -> dict[str, Any]:
    action_type = str(create_action.get("action_type") or "")
    family, rank, _tokens = _CREATE_SPECS[action_type]
    source_key = str(create_action.get("plan_key") or "").strip()
    activation_key = f"{source_key}:verified-enable"
    return {
        "plan_key": activation_key,
        "depends_on": [source_key],
        "tool_name": str(tool.get("registered_name") or ""),
        "action_type": "enable",
        "entity_type": str(create_action.get("entity_type") or family),
        "entity_id": f"{{{{decision:{source_key}.entity_id}}}}",
        "arguments": arguments,
        "expected_state": {"state|status": "ENABLED"},
        "observed_in_ads": True,
        "verified_create": True,
        "purpose": "verified_create",
        "activation_phase": True,
        "activation_source_plan_key": source_key,
        "activation_rank": rank,
        "reason": "Independent read-back of the PAUSED entity graph must complete before staged activation",
        "priority": max(1, 40 - rank),
    }


def _is_activation(decision: dict[str, Any]) -> bool:
    payload = decision.get("payload") if isinstance(decision.get("payload"), dict) else {}
    return payload.get("activation_phase") is True


def _task_activation_summary(store: Any, task_id: str) -> dict[str, Any]:
    rows = store.list_decisions(task_id=task_id, limit=500)
    activation = [item for item in rows if _is_activation(item)]
    ranks = sorted({int(item.get("payload", {}).get("activation_rank") or 0) for item in activation})
    return {
        "task_id": task_id,
        "total": len(activation),
        "blocked": sum(item.get("status") == "blocked" for item in activation),
        "planned": sum(item.get("status") == "planned" for item in activation),
        "verified": sum(item.get("status") == "verified" for item in activation),
        "failed": sum(item.get("status") in {"failed", "mismatch"} for item in activation),
        "ranks": ranks,
    }


def _set_task_phase(store: Any, task_id: str, state: str, rank: int | None = None) -> None:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with store.connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute("SELECT payload_json FROM tasks WHERE id=?", (task_id,)).fetchone()
            payload = json.loads(row["payload_json"] or "{}") if row else {}
            payload["activation_state"] = state
            payload["activation_rank"] = rank
            conn.execute(
                "UPDATE tasks SET status='planned',worker_session_id=NULL,worker_subagent_id=NULL,"
                "verifier_session_id=NULL,verifier_subagent_id=NULL,payload_json=? WHERE id=?",
                (json.dumps(payload, ensure_ascii=False), task_id),
            )
            conn.execute(
                "UPDATE workers SET status='completed',stopped_at=?,last_seen_at=? "
                "WHERE task_id=? AND status='running'",
                (now, now, task_id),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def _fail_blocked_activations(store: Any, task_id: str, reason: str) -> int:
    ids = [
        str(item["id"])
        for item in store.list_decisions(task_id=task_id, limit=500)
        if item.get("status") == "blocked" and _is_activation(item)
    ]
    if not ids:
        return 0
    placeholders = ",".join("?" for _ in ids)
    with store.connection() as conn:
        cursor = conn.execute(
            f"UPDATE decisions SET status='failed',failure=? WHERE id IN ({placeholders}) AND status='blocked'",
            (reason, *ids),
        )
    return int(cursor.rowcount)


def _advance_activation(store: Any, task_id: str) -> dict[str, Any]:
    rows = store.list_decisions(task_id=task_id, limit=500)
    activation = [item for item in rows if _is_activation(item)]
    if not activation:
        return {"applied": False}
    creates = [item for item in rows if item.get("action_type") in _CREATE_SPECS and not _is_activation(item)]
    bad_create = [item for item in creates if item.get("status") in {"failed", "mismatch", "blocked"}]
    if bad_create:
        failed = _fail_blocked_activations(store, task_id, "activation dependency failed independent verification")
        store.alert_once(
            "critical", "SEALED_ACTIVATION_ABORTED", bad_create[0].get("profile_id"), task_id,
            bad_create[0].get("id"),
            "A created Sponsored Products entity failed verification; the graph remains PAUSED",
            {"failed_dependencies": [item.get("id") for item in bad_create], "blocked_activations": failed},
            window_seconds=86400,
        )
        return {"applied": True, "state": "aborted", "failed": failed}
    if any(item.get("status") != "verified" for item in creates):
        return {"applied": True, "state": "awaiting_create_verification"}

    bad_activation = [item for item in activation if item.get("status") in {"failed", "mismatch"}]
    if bad_activation:
        failed = _fail_blocked_activations(store, task_id, "an earlier activation stage failed verification")
        store.alert_once(
            "critical", "SEALED_ACTIVATION_STAGE_FAILED", bad_activation[0].get("profile_id"), task_id,
            bad_activation[0].get("id"),
            "A staged activation failed verification; Campaign activation remains blocked",
            {"failed_stage": [item.get("id") for item in bad_activation], "blocked_activations": failed},
            window_seconds=86400,
        )
        return {"applied": True, "state": "aborted", "failed": failed}

    active = [
        item for item in activation
        if item.get("status") in {"planned", "reserved", "executed", "pending", "uncertain"}
    ]
    if active:
        return {
            "applied": True,
            "state": "activation_in_progress",
            "rank": min(int(item.get("payload", {}).get("activation_rank") or 0) for item in active),
        }

    blocked = [item for item in activation if item.get("status") == "blocked"]
    if blocked:
        rank = min(int(item.get("payload", {}).get("activation_rank") or 0) for item in blocked)
        releasing = [item for item in blocked if int(item.get("payload", {}).get("activation_rank") or 0) == rank]
        unresolved = [
            item for item in releasing
            if not str(item.get("entity_id") or "")
            or str(item.get("entity_id") or "").startswith("planned:")
            or _PLACEHOLDER.fullmatch(str(item.get("entity_id") or ""))
        ]
        if unresolved:
            failed = _fail_blocked_activations(store, task_id, "verified create did not bind one unique Amazon entity ID")
            store.alert_once(
                "critical", "SEALED_ACTIVATION_ID_UNRESOLVED", unresolved[0].get("profile_id"), task_id,
                unresolved[0].get("id"),
                "Verified creation could not bind activation to one Amazon entity; the graph remains PAUSED",
                {"decisions": [item.get("id") for item in unresolved], "failed": failed},
                window_seconds=86400,
            )
            return {"applied": True, "state": "aborted", "failed": failed}
        ids = [str(item["id"]) for item in releasing]
        placeholders = ",".join("?" for _ in ids)
        with store.connection() as conn:
            conn.execute(
                f"UPDATE decisions SET status='planned',failure=NULL WHERE id IN ({placeholders}) AND status='blocked'",
                ids,
            )
        _set_task_phase(store, task_id, "activation_planned", rank)
        store.event(
            "info", "sealed_activation.stage_released", "controller", task_id,
            f"Released verified activation stage {rank}",
            {"rank": rank, "decision_ids": ids},
        )
        return {"applied": True, "state": "activation_planned", "rank": rank, "released": ids}

    if all(item.get("status") == "verified" for item in activation):
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with store.connection() as conn:
            conn.execute(
                "UPDATE workers SET status='completed',stopped_at=?,last_seen_at=? "
                "WHERE task_id=? AND status='running'",
                (now, now, task_id),
            )
        task = store.finalize_task(
            task_id,
            "sealed-activation",
            "PAUSED creation graph independently verified and activated in leaf-to-Campaign stages",
        )
        store.event(
            "info", "sealed_activation.completed", "controller", task_id,
            "Sponsored Products creation graph is independently verified and live",
            {"activation_decisions": len(activation)},
        )
        return {"applied": True, "state": "completed", "task_status": task.get("status")}
    return {"applied": True, "state": "waiting"}


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    from .db import Store
    from .service import ControlService

    original_create_plan = ControlService.create_managed_plan
    original_mark_execution = Store.mark_execution
    original_record_verification = Store.record_verification

    def create_plan(self, payload: dict[str, Any], actor: str = "hermes-main"):
        raw_actions = payload.get("actions") if isinstance(payload.get("actions"), list) else []
        try:
            validate_standing_plan(self, payload)
            sealed = True
        except (KeyError, ValueError):
            sealed = False
        create_actions = [
            item for item in raw_actions
            if isinstance(item, dict) and str(item.get("action_type") or "") in _CREATE_SPECS
        ]
        generated: list[dict[str, Any]] = []
        clean = payload
        if sealed and create_actions:
            profile = payload.get("profile") if isinstance(payload.get("profile"), dict) else {}
            profile_id = str(profile.get("profile_id") or profile.get("id") or "").strip()
            existing_sources = {
                str(item.get("activation_source_plan_key") or "")
                for item in raw_actions if isinstance(item, dict) and item.get("activation_phase") is True
            }
            for action in create_actions:
                source_key = str(action.get("plan_key") or "").strip()
                if source_key in existing_sources:
                    continue
                tool, args = _activation_tool(self.store, action, profile_id)
                generated.append(_activation_action(action, tool, args))
            if generated:
                clean = dict(payload)
                clean["actions"] = [dict(item) if isinstance(item, dict) else item for item in raw_actions] + generated

        result = original_create_plan(self, clean, actor)
        if not generated or not result.get("standing_authorization", {}).get("applied"):
            return result
        task_id = str(result.get("task", {}).get("id") or "")
        by_key = {str(item["plan_key"]): item for item in generated}
        activation_ids: list[str] = []
        with self.store.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                rows = conn.execute(
                    "SELECT id,plan_key,payload_json FROM decisions WHERE task_id=?",
                    (task_id,),
                ).fetchall()
                for row in rows:
                    generated_row = by_key.get(str(row["plan_key"] or ""))
                    if not generated_row:
                        continue
                    body = json.loads(row["payload_json"] or "{}")
                    body.update({
                        "activation_phase": True,
                        "activation_source_plan_key": generated_row["activation_source_plan_key"],
                        "activation_rank": generated_row["activation_rank"],
                        "activation_requires_independent_verification": True,
                    })
                    conn.execute(
                        "UPDATE decisions SET status='blocked',failure=?,payload_json=? WHERE id=?",
                        (
                            "awaiting independent verification of the complete PAUSED creation graph",
                            json.dumps(body, ensure_ascii=False),
                            row["id"],
                        ),
                    )
                    activation_ids.append(str(row["id"]))
                task = conn.execute("SELECT payload_json FROM tasks WHERE id=?", (task_id,)).fetchone()
                task_payload = json.loads(task["payload_json"] or "{}") if task else {}
                task_payload.update({
                    "activation_state": "awaiting_create_verification",
                    "activation_decision_ids": activation_ids,
                    "activation_order": ["ad/target/keyword", "ad_group", "campaign"],
                })
                conn.execute(
                    "UPDATE tasks SET payload_json=? WHERE id=?",
                    (json.dumps(task_payload, ensure_ascii=False), task_id),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        self.store.event(
            "info", "sealed_activation.plan_compiled", actor, task_id,
            "Compiled PAUSED creation plus independently verified staged activation",
            {"activation_decision_ids": activation_ids},
        )
        result["task"] = self.store.get_task(task_id)
        result["cycle"] = self.store.get_cycle(str(result.get("cycle", {}).get("id") or ""))
        result["activation"] = _task_activation_summary(self.store, task_id)
        return result

    def mark_execution(self, *args, **kwargs):
        result = original_mark_execution(self, *args, **kwargs)
        decision_id = str(kwargs.get("decision_id") or (args[0] if args else ""))
        decision = self.get_decision(decision_id) or {}
        if decision.get("action_type") not in _CREATE_SPECS:
            return result
        entity_id = str(decision.get("entity_id") or "")
        task_id = str(decision.get("task_id") or "")
        plan_key = str(decision.get("plan_key") or "")
        if not task_id or not plan_key or not entity_id or entity_id.startswith("planned:"):
            return result
        for candidate in self.list_decisions(task_id=task_id, limit=500):
            payload = candidate.get("payload") if isinstance(candidate.get("payload"), dict) else {}
            if payload.get("activation_source_plan_key") != plan_key:
                continue
            with self.connection() as conn:
                conn.execute("UPDATE decisions SET entity_id=? WHERE id=?", (entity_id, candidate["id"]))
        return result

    def record_verification(self, **kwargs):
        result = original_record_verification(self, **kwargs)
        task_id = str(kwargs.get("task_id") or "")
        if task_id:
            transition = _advance_activation(self, task_id)
            if transition.get("applied"):
                result = dict(result)
                result["activation_transition"] = transition
                result["activation"] = _task_activation_summary(self, task_id)
                result["task"] = self.get_task(task_id)
        return result

    ControlService.create_managed_plan = create_plan
    Store.mark_execution = mark_execution
    Store.record_verification = record_verification
    _INSTALLED = True
