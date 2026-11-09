"""The pydantic contract must match shared/types.ts (and `Health` in src/lib/api.ts) exactly.

A small regex-and-brace parser reads the TypeScript, reduces every type to a comparable shape, and the
same is done for the pydantic models. Compared: interface and event member names, field names, optionality
(``x?:`` against "has a default"), literal unions, and the kind of every field (string, number, boolean,
list, nested object). If someone edits the TypeScript contract and forgets the Python mirror (or the other
way round), these tests fail with the field that drifted.
"""

from __future__ import annotations

import re
import types
from typing import Any, Literal, Union, get_args, get_origin

import pytest
from pydantic import BaseModel

from tayyari.contract import events, models, requests
from tayyari.core.paths import REPO_ROOT

TYPES_TS = REPO_ROOT / "shared" / "types.ts"
API_TS = REPO_ROOT / "src" / "lib" / "api.ts"

if not (TYPES_TS.is_file() and API_TS.is_file()):
    pytest.skip("the frontend sources are not part of this checkout", allow_module_level=True)

Shape = tuple[Any, ...]
"""("str",) | ("num",) | ("bool",) | ("lit", frozenset) | ("list", Shape) | ("obj", {name: (optional, Shape)})"""

# ───────────────────────── TypeScript side ─────────────────────────


def strip_comments(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.DOTALL)
    return re.sub(r"//[^\n]*", "", src)


def matching_brace(src: str, open_idx: int) -> int:
    depth = 0
    for i in range(open_idx, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return i
    raise ValueError("unbalanced braces")


def split_top(text: str, sep: str) -> list[str]:
    """Split on `sep` outside any braces, brackets or parentheses; blank parts are dropped."""
    parts: list[str] = []
    depth = 0
    start = 0
    for i, ch in enumerate(text):
        if ch in "{[(":
            depth += 1
        elif ch in "}])":
            depth -= 1
        elif ch == sep and depth == 0:
            parts.append(text[start:i])
            start = i + 1
    parts.append(text[start:])
    return [p.strip() for p in parts if p.strip()]


class TsTypes:
    """The interfaces, literal-union aliases and unions of one TypeScript source."""

    def __init__(self, text: str) -> None:
        self.text = strip_comments(text)
        self.interfaces: dict[str, str] = {}
        for m in re.finditer(r"export interface (\w+)\s*\{", self.text):
            end = matching_brace(self.text, m.end() - 1)
            self.interfaces[m.group(1)] = self.text[m.end() : end]
        self.aliases: dict[str, str] = {
            m.group(1): m.group(2) for m in re.finditer(r"export type (\w+)\s*=\s*([^;{]*);", self.text)
        }

    def fields(self, body: str) -> dict[str, tuple[bool, Shape]]:
        out: dict[str, tuple[bool, Shape]] = {}
        for part in split_top(body, ";"):
            m = re.fullmatch(r"(\w+)(\?)?\s*:\s*(.+)", part, flags=re.DOTALL)
            assert m, f"cannot parse the field {part!r}"
            out[m.group(1)] = (bool(m.group(2)), self.shape(m.group(3)))
        return out

    def shape(self, expr: str) -> Shape:
        expr = expr.strip()
        members = split_top(expr, "|")
        if len(members) > 1:
            lits: set[Any] = set()
            for member in members:
                member_shape = self.shape(member)
                assert member_shape[0] == "lit", f"only literal unions are supported, got {member!r}"
                lits |= member_shape[1]
            return ("lit", frozenset(lits))
        if expr.startswith("{") and expr.endswith("}"):
            return ("obj", self.fields(expr[1:-1]))
        if expr.endswith("[]"):
            return ("list", self.shape(expr[:-2]))
        if m := re.fullmatch(r"'([^']*)'", expr):
            return ("lit", frozenset({m.group(1)}))
        if re.fullmatch(r"\d+", expr):
            return ("lit", frozenset({int(expr)}))
        if expr in {"string", "number", "boolean"}:
            return ({"string": "str", "number": "num", "boolean": "bool"}[expr],)
        if expr in self.aliases:
            return self.shape(self.aliases[expr])
        if expr in self.interfaces:
            return ("obj", self.fields(self.interfaces[expr]))
        raise AssertionError(f"cannot parse the TypeScript type {expr!r}")

    def interface(self, name: str) -> Shape:
        return ("obj", self.fields(self.interfaces[name]))

    def union_members(self, name: str) -> dict[str, Shape]:
        """The members of ``export type <name> = | { type: 'x'; ... } | ...``, keyed by their `type` literal."""
        m = re.search(rf"export type {name}\s*=", self.text)
        assert m, f"{name} not found"
        depth = 0
        end = None
        for i in range(m.end(), len(self.text)):
            ch = self.text[i]
            if ch in "{[(":
                depth += 1
            elif ch in "}])":
                depth -= 1
            elif ch == ";" and depth == 0:
                end = i
                break
        assert end is not None
        out: dict[str, Shape] = {}
        for member in split_top(self.text[m.end() : end], "|"):
            assert member.startswith("{") and member.endswith("}"), member
            fields = self.fields(member[1:-1])
            (type_value,) = fields["type"][1][1]
            assert type_value not in out
            out[type_value] = ("obj", fields)
        return out


def _ts() -> TsTypes:
    return TsTypes(TYPES_TS.read_text(encoding="utf-8"))


def _ts_health() -> Shape:
    text = API_TS.read_text(encoding="utf-8")
    m = re.search(r"export interface Health\s*\{", text)
    assert m, "Health not found in src/lib/api.ts"
    end = matching_brace(text, m.end() - 1)
    return TsTypes(f"export interface Health {{{text[m.end() : end]}}}").interface("Health")


# ───────────────────────── pydantic side ─────────────────────────


def py_shape(tp: Any) -> Shape:
    origin = get_origin(tp)
    if tp is str:
        return ("str",)
    if tp is bool:
        return ("bool",)
    if tp in (int, float):
        return ("num",)
    if origin is Literal:
        return ("lit", frozenset(get_args(tp)))
    if origin in (Union, types.UnionType):
        members = [py_shape(a) for a in get_args(tp) if a is not type(None)]
        if len(members) == 1:
            return members[0]
        assert all(m[0] == "lit" for m in members), f"unsupported union {tp!r}"
        return ("lit", frozenset().union(*(m[1] for m in members)))
    if origin is list:
        return ("list", py_shape(get_args(tp)[0]))
    if isinstance(tp, type) and issubclass(tp, BaseModel):
        return model_shape(tp)
    raise AssertionError(f"cannot reduce the annotation {tp!r}")


def model_shape(cls: type[BaseModel]) -> Shape:
    return (
        "obj",
        {name: (not f.is_required(), py_shape(f.annotation)) for name, f in cls.model_fields.items()},
    )


# ───────────────────────── comparison ─────────────────────────


def _join(path: str, name: str) -> str:
    return f"{path}.{name}" if path else name


def diff(
    ts: Shape,
    py: Shape,
    path: str = "",
    *,
    lenient: frozenset[str] = frozenset(),
    extra: frozenset[str] = frozenset(),
) -> list[str]:
    """Differences between a TS shape and a pydantic shape.

    ``lenient``: dotted paths where pydantic may default a field the TypeScript requires.
    ``extra``: dotted paths of fields that exist only in pydantic (additive fields).
    """
    where = path or "<root>"
    if ts[0] != py[0]:
        return [f"{where}: TypeScript has a {ts[0]}, pydantic has a {py[0]}"]
    if ts[0] == "lit":
        if ts[1] == py[1]:
            return []
        return [f"{where}: literals differ: TS {sorted(ts[1], key=str)} vs pydantic {sorted(py[1], key=str)}"]
    if ts[0] == "list":
        return diff(ts[1], py[1], f"{path}[]", lenient=lenient, extra=extra)
    if ts[0] != "obj":
        return []
    out: list[str] = []
    ts_fields: dict[str, tuple[bool, Shape]] = ts[1]
    py_fields: dict[str, tuple[bool, Shape]] = py[1]
    for name in sorted(ts_fields.keys() - py_fields.keys()):
        out.append(f"{_join(path, name)}: in TypeScript but missing in pydantic")
    for name in sorted(py_fields.keys() - ts_fields.keys()):
        if _join(path, name) not in extra:
            out.append(f"{_join(path, name)}: in pydantic but not in TypeScript")
    for name in sorted(ts_fields.keys() & py_fields.keys()):
        sub = _join(path, name)
        ts_optional, ts_shape = ts_fields[name]
        py_optional, py_shape_ = py_fields[name]
        if ts_optional != py_optional and not (py_optional and sub in lenient):
            out.append(f"{sub}: optional in {'TypeScript' if ts_optional else 'pydantic'} only")
        out.extend(diff(ts_shape, py_shape_, sub, lenient=lenient, extra=extra))
    return out


def union_classes(adapter_type: Any) -> list[type[BaseModel]]:
    (union, *_metadata) = get_args(adapter_type)  # Annotated[Union[...], Field(discriminator=...)]
    return list(get_args(union))


def literal_type_of(cls: type[BaseModel]) -> str:
    (value,) = get_args(cls.model_fields["type"].annotation)
    assert cls.model_fields["type"].default == value, f"{cls.__name__}.type should default to its literal"
    return str(value)


# ───────────────────────── the tests ─────────────────────────

INTERFACE_MODELS: dict[str, type[BaseModel]] = {
    "SourceSummary": models.SourceSummary,
    "Evidence": models.Evidence,
    "Domain": models.Domain,
    "Skill": models.Skill,
    "RoleRequirement": models.RoleRequirement,
    "Role": models.Role,
    "Person": models.Person,
    "ReadLine": models.ReadLine,
    "Profile": models.Profile,
    "ReaderState": models.ReaderState,
    "ChatOrigin": models.ChatOrigin,
    "ChatRequest": requests.ChatRequestBody,
}

LITERAL_ALIASES: dict[str, Any] = {
    "SourceKind": models.SourceKind,
    "ReaderStatus": models.ReaderStatus,
    "ChatOriginKind": models.ChatOriginKind,
    "ReadTone": models.ReadTone,
}


@pytest.mark.parametrize("alias", sorted(LITERAL_ALIASES))
def test_literal_unions_match(alias: str) -> None:
    assert _ts().shape(alias) == ("lit", frozenset(get_args(LITERAL_ALIASES[alias])))


def test_source_kinds_tuple_matches_the_typescript_union() -> None:
    assert set(models.SOURCE_KINDS) == _ts().shape("SourceKind")[1]
    assert len(models.SOURCE_KINDS) == len(set(models.SOURCE_KINDS))


def test_every_typescript_interface_has_a_python_mirror() -> None:
    assert set(_ts().interfaces) == set(INTERFACE_MODELS), (
        "shared/types.ts and the contract models list different interfaces; mirror the change in "
        "tayyari/contract/models.py (and update INTERFACE_MODELS here)"
    )


@pytest.mark.parametrize("name", sorted(INTERFACE_MODELS))
def test_interface_matches_its_model(name: str) -> None:
    # The chat request body takes `title` and `caption` leniently (they default to "") and is length-capped.
    lenient = frozenset({"origin.title", "origin.caption"}) if name == "ChatRequest" else frozenset()
    assert diff(_ts().interface(name), model_shape(INTERFACE_MODELS[name]), lenient=lenient) == []


def test_model_class_names_follow_the_interface_names() -> None:
    for name, cls in INTERFACE_MODELS.items():
        if name != "ChatRequest":
            assert cls.__name__ == name


def test_health_matches_src_lib_api_ts() -> None:
    assert diff(_ts_health(), model_shape(models.Health), extra=frozenset({"storage"})) == []


def test_health_storage_is_the_only_additive_field_and_is_optional() -> None:
    field = models.Health.model_fields["storage"]
    assert not field.is_required()
    assert py_shape(field.annotation) == ("lit", frozenset({"postgres", "ephemeral"}))
    ts_names = set(_ts_health()[1])
    assert set(models.Health.model_fields) - ts_names == {"storage"}


@pytest.mark.parametrize(
    ("union_name", "alias"),
    [("AnalysisEvent", events.AnalysisEvent), ("ChatEvent", events.ChatEvent)],
)
def test_event_union_members_match(union_name: str, alias: Any) -> None:
    ts_members = _ts().union_members(union_name)
    py_members = {literal_type_of(cls): cls for cls in union_classes(alias)}
    assert set(py_members) == set(ts_members), f"{union_name}: different set of `type` literals"
    assert len(py_members) == len(union_classes(alias))
    problems: list[str] = []
    for type_value, ts_shape in ts_members.items():
        # `type` has a default on the pydantic side so events can be built without repeating it.
        problems += [
            f"[{type_value}] {line}"
            for line in diff(ts_shape, model_shape(py_members[type_value]), lenient=frozenset({"type"}))
        ]
    assert problems == []


def test_event_unions_are_discriminated_on_type() -> None:
    for alias in (events.AnalysisEvent, events.ChatEvent):
        metadata = get_args(alias)[1]
        assert metadata.discriminator == "type"


# ───────────────────────── the checker itself ─────────────────────────


class _Fine(BaseModel):
    a: str
    b: int | None = None
    c: Literal["x", "y"]
    d: list[models.Evidence]


_SMALL_TS = """
export type Pair = 'x' | 'y';
export interface Fine {
  a: string; // a comment; with a semicolon
  b?: number;
  c: Pair;
  d: { sourceId: string; text: string }[];
}
"""


def test_the_checker_accepts_a_matching_pair() -> None:
    assert diff(TsTypes(_SMALL_TS).interface("Fine"), model_shape(_Fine)) == []


@pytest.mark.parametrize(
    ("ts_source", "expected"),
    [
        (_SMALL_TS.replace("b?: number;", "b: number;"), "b: optional in pydantic only"),
        (
            _SMALL_TS.replace("b?: number;", "b?: number; e: string;"),
            "e: in TypeScript but missing in pydantic",
        ),
        (_SMALL_TS.replace("a: string;", ""), "a: in pydantic but not in TypeScript"),
        (_SMALL_TS.replace("'x' | 'y'", "'x' | 'z'"), "c: literals differ"),
        (_SMALL_TS.replace("b?: number;", "b?: string;"), "b: TypeScript has a str, pydantic has a num"),
        (_SMALL_TS.replace("text: string", "text?: string"), "d[].text: optional in TypeScript only"),
        (_SMALL_TS.replace("}[];", "};"), "d: TypeScript has a obj, pydantic has a list"),
    ],
)
def test_the_checker_catches_drift(ts_source: str, expected: str) -> None:
    problems = diff(TsTypes(ts_source).interface("Fine"), model_shape(_Fine))
    assert any(expected in p for p in problems), problems


def test_the_checker_reads_a_union_with_comments_and_inline_objects() -> None:
    src = """
    export type SourceKind = 'a' | 'b';
    export type Ev =
      | { type: 'one'; n: number } // trailing
      | { type: 'two'; kind?: SourceKind | 'extra'; inner: { x: string; y?: boolean } };
    """
    members = TsTypes(src).union_members("Ev")
    assert set(members) == {"one", "two"}
    assert members["two"][1]["kind"] == (True, ("lit", frozenset({"a", "b", "extra"})))
    assert members["two"][1]["inner"] == (False, ("obj", {"x": (False, ("str",)), "y": (True, ("bool",))}))
