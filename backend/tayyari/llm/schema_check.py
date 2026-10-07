"""A small JSON Schema checker for structured model output.

The API's structured outputs already constrain the answer to the schema, but the answer is re-checked here so
callers get a trusted object even from the `emit` tool fallback or from free text that was parsed out of a
reply. This is a port of the legacy `check_schema` (types, required keys, enums, nested objects and arrays)
with a few cheap additions: ``additionalProperties: false``, ``minItems`` / ``maxItems``, ``minLength`` /
``maxLength``, ``minimum`` / ``maximum``, ``const``, ``anyOf`` / ``oneOf`` and list-valued ``type``.

It is deliberately not a validator for the whole specification: ``$ref``, ``pattern``, ``format`` and the
like are ignored (they never produce an issue), so a schema feature this module does not know can only make
it more permissive, never reject a good answer.

Behaviour kept from the legacy function:
  - a ``null`` value for an optional property is tolerated; a required property that is missing or ``null``
    is an issue;
  - the result is a list of readable issues (empty means valid), capped at `MAX_ISSUES`.
"""

from __future__ import annotations

from typing import Any

MAX_ISSUES = 10
"""Issues collected before the check stops (a long list helps nobody, and it is quoted in a repair note)."""

_MAX_DEPTH = 40
_REPR_CHARS = 60

_JSON_TYPES: dict[str, tuple[type, ...]] = {
    "object": (dict,),
    "array": (list,),
    "string": (str,),
    "boolean": (bool,),
    "integer": (int, float),
    "number": (int, float),
    "null": (type(None),),
}


def _type_name(value: Any) -> str:
    return "null" if value is None else type(value).__name__


def _is_type(value: Any, kind: str) -> bool:
    """True when ``value`` has the JSON Schema type ``kind``; an unknown type name accepts everything."""
    expected = _JSON_TYPES.get(kind)
    if expected is None:
        return True
    if kind in ("integer", "number") and isinstance(value, bool):
        return False  # bool is an int in Python, but never a number in JSON
    if kind == "integer" and isinstance(value, float):
        return value.is_integer()  # 3.0 is an integer to JSON Schema
    return isinstance(value, expected)


def _short(value: Any) -> str:
    text = repr(value)
    return text if len(text) <= _REPR_CHARS else f"{text[: _REPR_CHARS - 3]}..."


def check_schema(
    data: Any,
    schema: dict[str, Any],
    path: str = "$",
    issues: list[str] | None = None,
    *,
    _depth: int = 0,
) -> list[str]:
    """Check ``data`` against ``schema`` and return the issues found (empty when valid, at most `MAX_ISSUES`).

    ``path`` prefixes every issue (``$.skills[2].name``); ``issues`` is the list to append to, so recursive
    calls and callers can share one capped list.
    """
    found: list[str] = [] if issues is None else issues
    if len(found) >= MAX_ISSUES or _depth > _MAX_DEPTH or not isinstance(schema, dict):
        return found

    kind = schema.get("type")
    kinds: list[str] = (
        [k for k in kind if isinstance(k, str)]
        if isinstance(kind, list)
        else [kind]
        if isinstance(kind, str)
        else []
    )
    if kinds and not any(_is_type(data, k) for k in kinds):
        found.append(f"{path}: expected {' or '.join(kinds)}, got {_type_name(data)}")
        return found

    if "const" in schema and data != schema["const"]:
        found.append(f"{path}: {_short(data)} is not {_short(schema['const'])}")
    if "enum" in schema and data not in schema["enum"]:
        found.append(f"{path}: {_short(data)} not in {_short(schema['enum'])}")

    for key in ("anyOf", "oneOf"):
        options = schema.get(key)
        if isinstance(options, list) and options:
            attempts = [check_schema(data, option, path, [], _depth=_depth + 1) for option in options]
            if not any(not attempt for attempt in attempts):
                found.append(f"{path}: matches none of the {len(options)} allowed shapes ({attempts[0][0]})")

    if isinstance(data, dict):
        _check_object(data, schema, path, found, _depth)
    elif isinstance(data, list):
        _check_array(data, schema, path, found, _depth)
    elif isinstance(data, str):
        _check_string(data, schema, path, found)
    elif isinstance(data, int | float) and not isinstance(data, bool):
        _check_number(data, schema, path, found)
    del found[MAX_ISSUES:]
    return found


def _allows_null(sub: Any) -> bool:
    """True when a property schema names ``null`` as an allowed type (a required property may then be null)."""
    kind = sub.get("type") if isinstance(sub, dict) else None
    return kind == "null" or (isinstance(kind, list) and "null" in kind)


def _check_object(
    data: dict[str, Any], schema: dict[str, Any], path: str, found: list[str], depth: int
) -> None:
    properties = schema.get("properties")
    props: dict[str, Any] = properties if isinstance(properties, dict) else {}
    required_list = schema.get("required")
    required = {k for k in required_list if isinstance(k, str)} if isinstance(required_list, list) else set()

    for key in sorted(required):
        if data.get(key) is None and not (key in data and _allows_null(props.get(key))):
            found.append(f"{path}.{key}: missing required field")
    for key, sub in props.items():
        # A null is "missing" (reported above when required, tolerated when optional), never a type error.
        if data.get(key) is not None:
            check_schema(data[key], sub, f"{path}.{key}", found, _depth=depth + 1)
    if schema.get("additionalProperties") is False:
        extra = sorted(str(k) for k in data if k not in props)
        if extra:
            shown = ", ".join(extra[:3]) + (f" and {len(extra) - 3} more" if len(extra) > 3 else "")
            found.append(f"{path}: unexpected {'property' if len(extra) == 1 else 'properties'} {shown}")
    elif isinstance(schema.get("additionalProperties"), dict):
        for key, value in data.items():
            if key not in props:
                check_schema(value, schema["additionalProperties"], f"{path}.{key}", found, _depth=depth + 1)


def _check_array(data: list[Any], schema: dict[str, Any], path: str, found: list[str], depth: int) -> None:
    min_items, max_items = schema.get("minItems"), schema.get("maxItems")
    if isinstance(min_items, int) and len(data) < min_items:
        found.append(f"{path}: expected at least {min_items} items, got {len(data)}")
    if isinstance(max_items, int) and len(data) > max_items:
        found.append(f"{path}: expected at most {max_items} items, got {len(data)}")
    items = schema.get("items")
    if isinstance(items, dict):
        for index, item in enumerate(data):
            if len(found) >= MAX_ISSUES:
                break
            check_schema(item, items, f"{path}[{index}]", found, _depth=depth + 1)


def _check_string(data: str, schema: dict[str, Any], path: str, found: list[str]) -> None:
    min_length, max_length = schema.get("minLength"), schema.get("maxLength")
    if isinstance(min_length, int) and len(data) < min_length:
        found.append(f"{path}: expected at least {min_length} characters, got {len(data)}")
    if isinstance(max_length, int) and len(data) > max_length:
        found.append(f"{path}: expected at most {max_length} characters, got {len(data)}")


def _check_number(data: float, schema: dict[str, Any], path: str, found: list[str]) -> None:
    minimum, maximum = schema.get("minimum"), schema.get("maximum")
    if isinstance(minimum, int | float) and not isinstance(minimum, bool) and data < minimum:
        found.append(f"{path}: {data} is below the minimum {minimum}")
    if isinstance(maximum, int | float) and not isinstance(maximum, bool) and data > maximum:
        found.append(f"{path}: {data} is above the maximum {maximum}")


__all__ = ["MAX_ISSUES", "check_schema"]
