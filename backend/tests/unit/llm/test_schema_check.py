"""`check_schema`: the legacy behaviour (types, required, enums, nesting, null tolerance, the 10-issue cap) and
the cheap additions (additionalProperties false, size bounds, const, anyOf, list-valued type)."""

from __future__ import annotations

from typing import Any

import pytest

from tayyari.llm.schema_check import MAX_ISSUES, check_schema

PERSON: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "age": {"type": "integer"},
        "ratio": {"type": "number"},
        "active": {"type": "boolean"},
        "level": {"type": "string", "enum": ["low", "high"]},
        "tags": {"type": "array", "items": {"type": "string"}},
        "address": {
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
        },
    },
    "required": ["name"],
}


def test_valid_document_has_no_issues() -> None:
    data = {
        "name": "Ada",
        "age": 36,
        "ratio": 0.5,
        "active": True,
        "level": "low",
        "tags": ["a", "b"],
        "address": {"city": "London"},
    }
    assert check_schema(data, PERSON) == []


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        ([], "$: expected object, got list"),
        ("x", "$: expected object, got str"),
        (None, "$: expected object, got null"),
        ({"name": 3}, "$.name: expected string, got int"),
        ({"name": "A", "age": "7"}, "$.age: expected integer, got str"),
        ({"name": "A", "age": True}, "$.age: expected integer, got bool"),
        ({"name": "A", "ratio": "0.5"}, "$.ratio: expected number, got str"),
        ({"name": "A", "ratio": False}, "$.ratio: expected number, got bool"),
        ({"name": "A", "active": 1}, "$.active: expected boolean, got int"),
        ({"name": "A", "tags": "a"}, "$.tags: expected array, got str"),
        ({"name": "A", "tags": ["a", 2]}, "$.tags[1]: expected string, got int"),
        ({"name": "A", "address": {}}, "$.address.city: missing required field"),
        ({"name": "A", "address": {"city": 1}}, "$.address.city: expected string, got int"),
    ],
)
def test_type_and_nesting_errors_name_the_path(data: Any, expected: str) -> None:
    assert expected in check_schema(data, PERSON)


def test_required_field_missing_or_null_is_an_issue() -> None:
    assert check_schema({}, PERSON) == ["$.name: missing required field"]
    assert check_schema({"name": None}, PERSON) == ["$.name: missing required field"]


def test_required_property_that_allows_null_may_be_null_but_not_absent() -> None:
    schema = {
        "type": "object",
        "properties": {"note": {"type": ["string", "null"]}},
        "required": ["note"],
    }
    assert check_schema({"note": None}, schema) == []
    assert check_schema({"note": "x"}, schema) == []
    assert check_schema({}, schema) == ["$.note: missing required field"]


def test_null_for_an_optional_property_is_tolerated() -> None:
    assert check_schema({"name": "A", "age": None, "tags": None, "address": None}, PERSON) == []


def test_enum_violation() -> None:
    issues = check_schema({"name": "A", "level": "mid"}, PERSON)
    assert len(issues) == 1
    assert issues[0].startswith("$.level: 'mid' not in ")


def test_integer_accepts_whole_floats_only() -> None:
    schema = {"type": "integer"}
    assert check_schema(3.0, schema) == []
    assert check_schema(3.5, schema) == ["$: expected integer, got float"]


def test_additional_properties_false_rejects_unknown_keys() -> None:
    schema = {"type": "object", "properties": {"a": {"type": "string"}}, "additionalProperties": False}
    assert check_schema({"a": "x"}, schema) == []
    assert check_schema({"a": "x", "b": 1}, schema) == ["$: unexpected property b"]
    many = check_schema({"a": "x", "b": 1, "c": 2, "d": 3, "e": 4, "f": 5}, schema)
    assert many == ["$: unexpected properties b, c, d and 2 more"]


def test_additional_properties_default_allows_unknown_keys() -> None:
    assert check_schema({"name": "A", "extra": 1}, PERSON) == []


def test_additional_properties_schema_checks_the_extra_values() -> None:
    schema = {"type": "object", "additionalProperties": {"type": "integer"}}
    assert check_schema({"a": 1, "b": 2}, schema) == []
    assert check_schema({"a": 1, "b": "x"}, schema) == ["$.b: expected integer, got str"]


def test_array_size_bounds() -> None:
    schema = {"type": "array", "minItems": 2, "maxItems": 3, "items": {"type": "integer"}}
    assert check_schema([1, 2], schema) == []
    assert check_schema([1], schema) == ["$: expected at least 2 items, got 1"]
    assert check_schema([1, 2, 3, 4], schema) == ["$: expected at most 3 items, got 4"]


def test_string_length_bounds() -> None:
    schema = {"type": "string", "minLength": 2, "maxLength": 4}
    assert check_schema("abc", schema) == []
    assert check_schema("a", schema) == ["$: expected at least 2 characters, got 1"]
    assert check_schema("abcde", schema) == ["$: expected at most 4 characters, got 5"]


def test_number_bounds() -> None:
    schema = {"type": "integer", "minimum": 0, "maximum": 100}
    assert check_schema(50, schema) == []
    assert check_schema(-1, schema) == ["$: -1 is below the minimum 0"]
    assert check_schema(101, schema) == ["$: 101 is above the maximum 100"]


def test_const() -> None:
    assert check_schema("a", {"const": "a"}) == []
    assert check_schema("b", {"const": "a"}) == ["$: 'b' is not 'a'"]


def test_list_valued_type_allows_null() -> None:
    schema = {"type": ["string", "null"]}
    assert check_schema("x", schema) == []
    assert check_schema(None, schema) == []
    assert check_schema(3, schema) == ["$: expected string or null, got int"]


def test_any_of_needs_one_matching_alternative() -> None:
    schema = {"anyOf": [{"type": "string"}, {"type": "integer"}]}
    assert check_schema("x", schema) == []
    assert check_schema(3, schema) == []
    issues = check_schema([], schema)
    assert len(issues) == 1
    assert issues[0].startswith("$: matches none of the 2 allowed shapes")


def test_unknown_schema_features_never_reject() -> None:
    schema = {"$ref": "#/defs/x", "pattern": "^a$", "format": "email", "type": "frobnicate"}
    assert check_schema("zzz", schema) == []


def test_issues_are_capped() -> None:
    schema = {"type": "array", "items": {"type": "integer"}}
    issues = check_schema(["x"] * 50, schema)
    assert len(issues) == MAX_ISSUES == 10


def test_shared_issue_list_is_appended_to_and_capped() -> None:
    shared: list[str] = ["already"]
    result = check_schema("x", {"type": "integer"}, "$.field", shared)
    assert result is shared
    assert shared == ["already", "$.field: expected integer, got str"]


def test_long_values_are_shortened_in_messages() -> None:
    issues = check_schema("x" * 500, {"enum": ["a"]})
    assert len(issues[0]) < 200
