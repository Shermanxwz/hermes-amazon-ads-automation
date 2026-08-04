from __future__ import annotations

import json
import math
import re
from typing import Any

# Keywords whose semantics affect acceptance but are intentionally not implemented. Their
# presence must fail closed for stateful MCP operations rather than silently weaken a contract.
_UNSUPPORTED_ASSERTIONS = {
    "unevaluatedProperties", "unevaluatedItems", "contentSchema",
}


def _type_ok(value: Any, expected: str) -> bool:
    if expected == "object": return isinstance(value, dict)
    if expected == "array": return isinstance(value, list)
    if expected == "string": return isinstance(value, str)
    if expected == "integer": return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number": return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))
    if expected == "boolean": return isinstance(value, bool)
    if expected == "null": return value is None
    return True


def _resolve_local_ref(root: dict[str, Any], ref: str) -> dict[str, Any] | bool | None:
    if not ref.startswith("#/"):
        return None
    current: Any = root
    for part in ref[2:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current if isinstance(current, (dict, bool)) else None


def _canonical(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError):
        return repr(value)


def validate_instance(
    value: Any,
    schema: dict[str, Any] | bool | None,
    path: str = "$",
    *,
    _root: dict[str, Any] | None = None,
    _depth: int = 0,
) -> list[str]:
    """Validate the safety-relevant JSON Schema subset used by Hermes MCP tools.

    Hermes remains the canonical validator. This independent preflight rejects malformed or
    drifted mutations before Amazon. Common 2020-12 assertions, local refs and composition are
    supported. Assertion keywords that are not safely implemented fail closed.
    """
    if schema is True or schema is None:
        return []
    if schema is False:
        return [f"{path}: schema rejects all values"]
    if not isinstance(schema, dict) or not schema:
        return []
    if _depth > 40:
        return [f"{path}: schema recursion limit exceeded"]

    original = schema
    if isinstance(schema.get("parameters"), dict):
        schema = schema["parameters"]
    root = _root or (schema if any(key in schema for key in ("$defs", "definitions")) else original)

    unsupported = sorted(_UNSUPPORTED_ASSERTIONS & schema.keys())
    if unsupported:
        return [f"{path}: unsupported assertion keyword(s): {', '.join(unsupported)}"]

    if isinstance(schema.get("$ref"), str):
        resolved = _resolve_local_ref(root, schema["$ref"])
        if resolved is None:
            return [f"{path}: unresolved schema ref"]
        # Sibling assertions are valid in modern JSON Schema and must also apply.
        ref_errors = validate_instance(value, resolved, path, _root=root, _depth=_depth + 1)
        siblings = {key: item for key, item in schema.items() if key != "$ref"}
        return ref_errors + validate_instance(value, siblings, path, _root=root, _depth=_depth + 1)

    errors: list[str] = []
    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: value does not match const")

    all_of = schema.get("allOf")
    if isinstance(all_of, list):
        for option in all_of:
            if isinstance(option, (dict, bool)):
                errors.extend(validate_instance(value, option, path, _root=root, _depth=_depth + 1))

    one_of = schema.get("oneOf")
    if isinstance(one_of, list) and one_of:
        matches = sum(not validate_instance(value, option, path, _root=root, _depth=_depth + 1)
                      for option in one_of if isinstance(option, (dict, bool)))
        if matches != 1:
            errors.append(f"{path}: does not match exactly one schema alternative")

    any_of = schema.get("anyOf")
    if isinstance(any_of, list) and any_of:
        matches = sum(not validate_instance(value, option, path, _root=root, _depth=_depth + 1)
                      for option in any_of if isinstance(option, (dict, bool)))
        if matches < 1:
            errors.append(f"{path}: does not match any allowed schema alternative")

    not_schema = schema.get("not")
    if isinstance(not_schema, (dict, bool)) and not validate_instance(value, not_schema, path, _root=root, _depth=_depth + 1):
        errors.append(f"{path}: matches forbidden schema")

    if_schema = schema.get("if")
    if isinstance(if_schema, (dict, bool)):
        branch = schema.get("then") if not validate_instance(value, if_schema, path, _root=root, _depth=_depth + 1) else schema.get("else")
        if isinstance(branch, (dict, bool)):
            errors.extend(validate_instance(value, branch, path, _root=root, _depth=_depth + 1))

    expected = schema.get("type")
    if isinstance(expected, list):
        if not any(isinstance(item, str) and _type_ok(value, item) for item in expected):
            return errors + [f"{path}: expected one of {expected}"]
    elif isinstance(expected, str) and not _type_ok(value, expected):
        return errors + [f"{path}: expected {expected}"]

    if "enum" in schema and value not in schema.get("enum", []):
        errors.append(f"{path}: value is outside enum")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if not math.isfinite(float(value)):
            errors.append(f"{path}: number must be finite")
        else:
            if "minimum" in schema and value < schema["minimum"]: errors.append(f"{path}: below minimum")
            if "maximum" in schema and value > schema["maximum"]: errors.append(f"{path}: above maximum")
            if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]: errors.append(f"{path}: below exclusiveMinimum")
            if "exclusiveMaximum" in schema and value >= schema["exclusiveMaximum"]: errors.append(f"{path}: above exclusiveMaximum")
            if "multipleOf" in schema:
                try:
                    multiple = float(schema["multipleOf"])
                    if multiple <= 0 or not math.isfinite(multiple):
                        errors.append(f"{path}: invalid multipleOf constraint")
                    elif abs((float(value) / multiple) - round(float(value) / multiple)) > 1e-9:
                        errors.append(f"{path}: not a multipleOf value")
                except (TypeError, ValueError):
                    errors.append(f"{path}: invalid multipleOf constraint")

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
        if "minProperties" in schema and len(value) < int(schema["minProperties"]): errors.append(f"{path}: fewer than minProperties")
        if "maxProperties" in schema and len(value) > int(schema["maxProperties"]): errors.append(f"{path}: more than maxProperties")

        property_names = schema.get("propertyNames")
        if isinstance(property_names, (dict, bool)):
            for key in value:
                errors.extend(validate_instance(key, property_names, f"{path}.[property:{key}]", _root=root, _depth=_depth + 1))

        properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
        pattern_properties = schema.get("patternProperties") if isinstance(schema.get("patternProperties"), dict) else {}
        additional = schema.get("additionalProperties", True)
        for key, item in value.items():
            matched = False
            child = properties.get(key)
            if isinstance(child, (dict, bool)):
                matched = True
                errors.extend(validate_instance(item, child, f"{path}.{key}", _root=root, _depth=_depth + 1))
            for pattern, child_schema in pattern_properties.items():
                try:
                    pattern_match = re.search(pattern, key) is not None
                except re.error:
                    errors.append(f"{path}: invalid patternProperties regex")
                    pattern_match = False
                if pattern_match and isinstance(child_schema, (dict, bool)):
                    matched = True
                    errors.extend(validate_instance(item, child_schema, f"{path}.{key}", _root=root, _depth=_depth + 1))
            if not matched:
                if additional is False:
                    errors.append(f"{path}.{key}: additional property not allowed")
                elif isinstance(additional, dict):
                    errors.extend(validate_instance(item, additional, f"{path}.{key}", _root=root, _depth=_depth + 1))

        dependencies = schema.get("dependentRequired")
        if isinstance(dependencies, dict):
            for key, required_keys in dependencies.items():
                if key in value and isinstance(required_keys, list):
                    for required_key in required_keys:
                        if required_key not in value:
                            errors.append(f"{path}.{required_key}: required when {key} is present")
        dependent_schemas = schema.get("dependentSchemas")
        if isinstance(dependent_schemas, dict):
            for key, child_schema in dependent_schemas.items():
                if key in value and isinstance(child_schema, (dict, bool)):
                    errors.extend(validate_instance(value, child_schema, path, _root=root, _depth=_depth + 1))

    if isinstance(value, list):
        if "minItems" in schema and len(value) < int(schema["minItems"]): errors.append(f"{path}: fewer than minItems")
        if "maxItems" in schema and len(value) > int(schema["maxItems"]): errors.append(f"{path}: more than maxItems")
        if schema.get("uniqueItems") is True:
            normalized = [_canonical(item) for item in value]
            if len(set(normalized)) != len(normalized): errors.append(f"{path}: items must be unique")

        prefix_items = schema.get("prefixItems") if isinstance(schema.get("prefixItems"), list) else []
        for index, child_schema in enumerate(prefix_items[:len(value)]):
            if isinstance(child_schema, (dict, bool)):
                errors.extend(validate_instance(value[index], child_schema, f"{path}[{index}]", _root=root, _depth=_depth + 1))
        items_schema = schema.get("items")
        start = len(prefix_items)
        if isinstance(items_schema, (dict, bool)):
            for index, item in enumerate(value[start:], start):
                errors.extend(validate_instance(item, items_schema, f"{path}[{index}]", _root=root, _depth=_depth + 1))

        contains_schema = schema.get("contains")
        if isinstance(contains_schema, (dict, bool)):
            matches = sum(not validate_instance(item, contains_schema, f"{path}[{index}]", _root=root, _depth=_depth + 1)
                          for index, item in enumerate(value))
            minimum = int(schema.get("minContains", 1))
            maximum = int(schema.get("maxContains", len(value)))
            if matches < minimum or matches > maximum:
                errors.append(f"{path}: contains match count {matches} is outside {minimum}..{maximum}")

    return errors
