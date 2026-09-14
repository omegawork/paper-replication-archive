#!/usr/bin/env python3
"""Small standard-library JSON Schema subset used as the versioned validation truth."""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any


SCHEMA_DIR = Path(__file__).resolve().parent.parent / "schemas"


class SchemaValidationError(ValueError):
    """Raised when a document does not satisfy a bundled schema."""


def load_schema(name: str, version: int = 5) -> dict[str, Any]:
    directory = SCHEMA_DIR / "v4" if version == 4 else SCHEMA_DIR
    path = directory / (name if name.endswith(".json") else f"{name}.schema.json")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SchemaValidationError(f"schema not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise SchemaValidationError(f"invalid bundled schema {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise SchemaValidationError(f"schema root must be an object: {path}")
    return value


def _type_matches(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    raise SchemaValidationError(f"unsupported schema type: {expected}")


def _json_equal(left: Any, right: Any) -> bool:
    # Python considers True == 1; JSON Schema deliberately does not.
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(_json_equal(left[k], right[k]) for k in left)
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(_json_equal(a, b) for a, b in zip(left, right))
    return left == right


def _validate(value: Any, schema: dict[str, Any], location: str) -> list[str]:
    errors: list[str] = []
    if "oneOf" in schema:
        branches = schema["oneOf"]
        passed = [not _validate(value, branch, location) for branch in branches]
        if sum(passed) != 1:
            return [f"{location}: expected exactly one oneOf branch to match"]
    if "anyOf" in schema:
        if not any(not _validate(value, branch, location) for branch in schema["anyOf"]):
            return [f"{location}: no anyOf branch matched"]

    expected = schema.get("type")
    expected_types = [expected] if isinstance(expected, str) else expected
    if expected_types:
        if not any(_type_matches(value, item) for item in expected_types):
            return [f"{location}: expected type {expected_types}, got {type(value).__name__}"]

    if "const" in schema and not _json_equal(value, schema["const"]):
        errors.append(f"{location}: expected constant {schema['const']!r}")
    if "enum" in schema and not any(_json_equal(value, option) for option in schema["enum"]):
        errors.append(f"{location}: {value!r} is not in {schema['enum']!r}")

    if isinstance(value, dict):
        required = schema.get("required", [])
        for key in required:
            if key not in value:
                errors.append(f"{location}: missing required property {key!r}")
        properties = schema.get("properties", {})
        additional = schema.get("additionalProperties", True)
        for key, item in value.items():
            child = f"{location}.{key}"
            if key in properties:
                errors.extend(_validate(item, properties[key], child))
            elif additional is False:
                errors.append(f"{child}: additional property is not allowed")
            elif isinstance(additional, dict):
                errors.extend(_validate(item, additional, child))
    elif isinstance(value, list):
        minimum = schema.get("minItems")
        if minimum is not None and len(value) < minimum:
            errors.append(f"{location}: expected at least {minimum} items")
        maximum = schema.get("maxItems")
        if maximum is not None and len(value) > maximum:
            errors.append(f"{location}: expected at most {maximum} items")
        if schema.get("uniqueItems"):
            seen: set[str] = set()
            for item in value:
                marker = json.dumps(item, sort_keys=True, ensure_ascii=False)
                if marker in seen:
                    errors.append(f"{location}: duplicate array item {item!r}")
                    break
                seen.add(marker)
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                errors.extend(_validate(item, item_schema, f"{location}[{index}]"))
    elif isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append(f"{location}: string is shorter than {schema['minLength']}")
        if "pattern" in schema and re.fullmatch(schema["pattern"], value) is None:
            errors.append(f"{location}: does not match pattern {schema['pattern']!r}")
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{location}: must be >= {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{location}: must be <= {schema['maximum']}")
    return errors


def validate_document(value: Any, schema_name: str) -> None:
    version = value.get("schema_version", 5) if isinstance(value, dict) else 5
    errors = _validate(value, load_schema(schema_name, version), "$")
    if schema_name == "digitization_record" and isinstance(value, dict) and value.get("review_status") == "agent_checked":
        reviewer = value.get("reviewer", {})
        if (reviewer.get("source") != "platform_spawn_result" or not reviewer.get("agent_id")
                or reviewer.get("agent_id") == value.get("operator")
                or re.fullmatch(r"[0-9a-f]{64}", str(reviewer.get("report_sha256", ""))) is None
                or not value.get("exported_points") or not value.get("estimated_uncertainty")):
            errors.append("agent_checked requires independent native reviewer, report hash, points and uncertainty")
    if errors:
        raise SchemaValidationError("; ".join(errors))


def enum_values(schema_name: str, *property_path: str) -> tuple[Any, ...]:
    node: dict[str, Any] = load_schema(schema_name)
    for key in property_path:
        if key == "[]":
            node = node.get("items", {})
        else:
            node = node.get("properties", {}).get(key, {})
    values = node.get("enum")
    if not isinstance(values, list):
        raise SchemaValidationError(
            f"schema {schema_name} property {'.'.join(property_path)} has no enum"
        )
    return tuple(values)
