from __future__ import annotations

import re
from typing import Any


def _type_ok(value: Any, expected: str) -> bool:
    if expected == "object": return isinstance(value, dict)
    if expected == "array": return isinstance(value, list)
    if expected == "string": return isinstance(value, str)
    if expected == "integer": return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number": return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean": return isinstance(value, bool)
    if expected == "null": return value is None
    return True


def _resolve_local_ref(root: dict[str, Any], ref: str) -> dict[str, Any] | None:
    if not ref.startswith("#/"):
        return None
    current: Any = root
    for part in ref[2:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current if isinstance(current, dict) else None


def validate_instance(
    value: Any,
    schema: dict[str, Any] | None,
    path: str = "$",
    *,
    _root: dict[str, Any] | None = None,
    _depth: int = 0,
) -> list[str]:
    """Validate the safety-relevant JSON Schema subset used by Hermes MCP tools.

    Hermes remains the canonical validator. This independent preflight rejects
    malformed or schema-drifted mutations before they reach Amazon. Local refs,
    composition, scalar limits and collection bounds are supported; unknown
    keywords are never interpreted permissively as authorization.
    """
    if not isinstance(schema, dict) or not schema:
        return []
    if _depth > 40:
        return [f"{path}: schema recursion limit exceeded"]
    if isinstance(schema.get("parameters"), dict):
        schema = schema["parameters"]
    root = _root or schema
    if isinstance(schema.get("$ref"), str):
        resolved = _resolve_local_ref(root, schema["$ref"])
        return validate_instance(value, resolved, path, _root=root, _depth=_depth + 1) if resolved else [f"{path}: unresolved schema ref"]

    errors: list[str] = []
    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: value does not match const")
    all_of = schema.get("allOf")
    if isinstance(all_of, list):
        for option in all_of:
            if isinstance(option, dict):
                errors.extend(validate_instance(value, option, path, _root=root, _depth=_depth + 1))
    alternatives = schema.get("oneOf") or schema.get("anyOf")
    if isinstance(alternatives, list) and alternatives:
        matches = sum(not validate_instance(value, option, path, _root=root, _depth=_depth + 1) for option in alternatives if isinstance(option, dict))
        required_matches = 1 if "oneOf" in schema else 1
        if matches < required_matches or ("oneOf" in schema and matches != 1):
            errors.append(f"{path}: does not match allowed schema alternatives")
        return errors

    expected = schema.get("type")
    if isinstance(expected, list):
        if not any(_type_ok(value, item) for item in expected):
            return errors + [f"{path}: expected one of {expected}"]
    elif isinstance(expected, str) and not _type_ok(value, expected):
        return errors + [f"{path}: expected {expected}"]
    if "enum" in schema and value not in schema.get("enum", []):
        errors.append(f"{path}: value is outside enum")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]: errors.append(f"{path}: below minimum")
        if "maximum" in schema and value > schema["maximum"]: errors.append(f"{path}: above maximum")
        if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]: errors.append(f"{path}: below exclusiveMinimum")
        if "exclusiveMaximum" in schema and value >= schema["exclusiveMaximum"]: errors.append(f"{path}: above exclusiveMaximum")
        if "multipleOf" in schema:
            multiple = float(schema["multipleOf"])
            if multiple > 0 and abs((float(value) / multiple) - round(float(value) / multiple)) > 1e-9:
                errors.append(f"{path}: not a multipleOf value")
    if isinstance(value, str):
        if "minLength" in schema and len(value) < int(schema["minLength"]): errors.append(f"{path}: shorter than minLength")
        if "maxLength" in schema and len(value) > int(schema["maxLength"]): errors.append(f"{path}: longer than maxLength")
        if "pattern" in schema:
            try:
                if re.search(str(schema["pattern"]), value) is None: errors.append(f"{path}: does not match pattern")
            except re.error:
                errors.append(f"{path}: invalid schema pattern")
    if isinstance(value, dict):
        required = schema.get("required") if isinstance(schema.get("required"), list) else []
        for key in required:
            if key not in value: errors.append(f"{path}.{key}: required")
        properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
        pattern_properties = schema.get("patternProperties") if isinstance(schema.get("patternProperties"), dict) else {}
        for key, item in value.items():
            child = properties.get(key)
            matched = child is not None
            if isinstance(child, dict):
                errors.extend(validate_instance(item, child, f"{path}.{key}", _root=root, _depth=_depth + 1))
            for pattern, child_schema in pattern_properties.items():
                try: pattern_match = re.search(pattern, key) is not None
                except re.error: pattern_match = False
                if pattern_match and isinstance(child_schema, dict):
                    matched = True
                    errors.extend(validate_instance(item, child_schema, f"{path}.{key}", _root=root, _depth=_depth + 1))
            if schema.get("additionalProperties") is False and not matched:
                errors.append(f"{path}.{key}: additional property not allowed")
        if "minProperties" in schema and len(value) < int(schema["minProperties"]): errors.append(f"{path}: fewer than minProperties")
        if "maxProperties" in schema and len(value) > int(schema["maxProperties"]): errors.append(f"{path}: more than maxProperties")
    if isinstance(value, list):
        if "minItems" in schema and len(value) < int(schema["minItems"]): errors.append(f"{path}: fewer than minItems")
        if "maxItems" in schema and len(value) > int(schema["maxItems"]): errors.append(f"{path}: more than maxItems")
        if schema.get("uniqueItems") is True:
            normalized = [repr(item) for item in value]
            if len(set(normalized)) != len(normalized): errors.append(f"{path}: items must be unique")
        if isinstance(schema.get("items"), dict):
            for index, item in enumerate(value):
                errors.extend(validate_instance(item, schema["items"], f"{path}[{index}]", _root=root, _depth=_depth + 1))
    return errors
