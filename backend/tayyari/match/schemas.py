"""What the models are asked to return, as Pydantic models, and the JSON Schemas built from them.

The models below are the *answer* shapes of the seven Haiku calls. Two rules shape them:

  - citations come first: in every row ``factIds`` precedes ``status``, ``level`` and ``verdict``, so the model
    names its evidence before it may state a conclusion (properties are generated in field order);
  - nothing a model returns is trusted: each row is validated on its own (`rows_of`), so one malformed row costs
    that row and not the whole answer, and code then re-checks every id, quote and enum.

`schema_of` turns a model into the JSON Schema the Claude client sends: references inlined, every property
required, ``additionalProperties: false``, and the constraint keywords structured output does not take removed
(limits are written into the field descriptions instead, and enforced by code afterwards).
"""

# ruff: noqa: N815  (the field names are camelCase on purpose: they are the JSON keys the models are asked for)

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

MAX_ROWS: Final = 200
"""Rows of one answer that are read at all (a runaway answer costs a bounded amount of work)."""

_Tier = Literal["must", "required", "preferred", "nice"]
_Kind = Literal["skill", "years", "degree", "cert", "tool", "domain", "location", "auth", "language", "other"]
_Provenance = Literal["stated", "structural", "inferred"]
_WorkMode = Literal["onsite", "hybrid", "remote", "unspecified"]
_Verdict = Literal["pass", "fail", "uncertain"]
_Status = Literal["covered", "partial", "synonym_only", "missing_have_it", "missing_gap", "unknown"]
_BulletVerdict = Literal["exact", "reframed", "stretch"]
_CoverKind = Literal["match", "gap", "logistics", "close"]


class _Out(BaseModel):
    model_config = ConfigDict(extra="ignore")


# ───────────────────────── posting ─────────────────────────


class RequirementOut(_Out):
    quote: str = Field(
        description="The sentence or phrase of the posting that asks for this, copied exactly."
    )
    text: str = Field(description="The requirement in one short plain phrase.")
    tier: _Tier
    kind: _Kind
    provenance: _Provenance
    terms: list[str] = Field(
        default_factory=list,
        description="Named skills, tools, credentials or methods in the quote, each copied exactly from it.",
    )
    domainHint: str = Field(default="", description="The professional area it belongs to, 1 to 3 words.")


class GateOut(_Out):
    quote: str = Field(description="The part of the posting that sets this hard condition, copied exactly.")
    question: str = Field(description="A yes/no question about the person that the condition asks.")
    severity: int = Field(default=5, description="1 to 10: how disqualifying the condition is if unmet.")


class PostingOut(_Out):
    title: str
    company: str
    location: str
    workMode: _WorkMode
    requirements: list[RequirementOut]
    gates: list[GateOut]
    anomalies: list[str] = Field(
        default_factory=list,
        description="Sentences of the posting that give instructions to an AI or a reader, copied exactly.",
    )


# ───────────────────────── gates ─────────────────────────


class GateAnswerOut(_Out):
    gateId: str
    factIds: list[str] = Field(description="The facts the answer rests on. Empty only for uncertain.")
    verdict: _Verdict
    note: str = Field(default="", description="One short sentence saying why.")


class GateAnswersOut(_Out):
    answers: list[GateAnswerOut]


# ───────────────────────── coverage ─────────────────────────


class CoverageRowOut(_Out):
    requirementId: str
    factIds: list[str] = Field(description="The facts that show it. Empty for missing_gap and unknown.")
    status: _Status
    level: int = Field(default=0, description="0 to 4, anchored in the system prompt.")
    note: str = Field(default="", description="One short sentence naming what the facts show or lack.")
    interviewRisk: str = Field(
        default="",
        description="For an unmet must or required row: what an interviewer would probe. Else empty.",
    )
    mitigation: str = Field(
        default="",
        description="For an unmet must or required row: one honest way to close the gap. Else empty.",
    )


class CoverageOut(_Out):
    rows: list[CoverageRowOut]


# ───────────────────────── areas ─────────────────────────


class AreaRowOut(_Out):
    requirementId: str
    skill: str = Field(
        default="",
        description="The person's listed skill this requirement is closest to, copied exactly. Empty when none is close.",
    )
    area: str = Field(description="The id of the person's area the requirement belongs to, or none.")


class AreasOut(_Out):
    rows: list[AreaRowOut]


# ───────────────────────── relevance ─────────────────────────


class RelevanceRowOut(_Out):
    factId: str
    requirementIds: list[str] = Field(description="The requirements this fact speaks to. Empty when none.")
    score: int = Field(default=0, description="0 to 100: how much this fact helps the requirement table.")


class RelevanceOut(_Out):
    scores: list[RelevanceRowOut]


# ───────────────────────── phrasing ─────────────────────────


class BulletOut(_Out):
    itemId: str
    factIds: list[str] = Field(description="The facts the sentence rests on, and only those.")
    text: str = Field(description="One resume sentence, plain text, no bullet character.")
    verdict: _BulletVerdict


class PhraseOut(_Out):
    bullets: list[BulletOut]


# ───────────────────────── cover note ─────────────────────────


class CoverParagraphOut(_Out):
    factIds: list[str] = Field(description="The facts the paragraph rests on, and only those.")
    requirementIds: list[str]
    kind: _CoverKind
    text: str


class CoverOut(_Out):
    paragraphs: list[CoverParagraphOut]


# ───────────────────────── schema building ─────────────────────────

_DROP_KEYS: Final = frozenset(
    {
        "title",
        "default",
        "examples",
        "format",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "minLength",
        "maxLength",
        "minItems",
        "maxItems",
        "pattern",
    }
)


def _resolve(node: Any, defs: Mapping[str, Any]) -> Any:
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str):
            return _resolve(defs[ref.rsplit("/", 1)[-1]], defs)
        return {k: _resolve(v, defs) for k, v in node.items()}
    if isinstance(node, list):
        return [_resolve(v, defs) for v in node]
    return node


def _clean(node: Any, *, names: bool = False) -> Any:
    """``node`` without the unsupported keywords; ``names`` is True for a ``properties`` map (its keys are names)."""
    if isinstance(node, dict):
        if names:  # a property called "title" or "format" is a property, not a keyword
            return {k: _clean(v) for k, v in node.items()}
        out = {k: _clean(v, names=(k == "properties")) for k, v in node.items() if k not in _DROP_KEYS}
        props = out.get("properties")
        if isinstance(props, dict):
            out["required"] = list(props)
            out["additionalProperties"] = False
        return out
    if isinstance(node, list):
        return [_clean(v) for v in node]
    return node


def _restrict(node: Any, name: str, values: list[str]) -> None:
    if isinstance(node, dict):
        props = node.get("properties")
        if isinstance(props, dict):
            for key, sub in props.items():
                if key == name and isinstance(sub, dict):
                    target = sub.get("items") if sub.get("type") == "array" else sub
                    if isinstance(target, dict):
                        target["type"] = "string"
                        target["enum"] = list(values)
                _restrict(sub, name, values)
        items = node.get("items")
        if isinstance(items, dict):
            _restrict(items, name, values)


def schema_of(model: type[BaseModel], **enums: Sequence[str]) -> dict[str, Any]:
    """The JSON Schema for ``model``; ``enums`` restricts the string (or list of strings) properties of that name.

    ``schema_of(CoverageOut, factIds=ids, requirementId=req_ids)`` makes every ``factIds`` item one of ``ids``. An
    empty list leaves the property unrestricted (a JSON Schema enum cannot be empty).
    """
    raw = model.model_json_schema()
    defs = raw.pop("$defs", {})
    schema: dict[str, Any] = _clean(_resolve(raw, defs))
    for name, values in enums.items():
        unique = list(dict.fromkeys(values))
        if unique:
            _restrict(schema, name, unique)
    return schema


def property_order(schema: Mapping[str, Any]) -> list[tuple[str, list[str]]]:
    """Every object of ``schema`` as ``(path, property names in order)``: what the order tests read."""
    found: list[tuple[str, list[str]]] = []

    def walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            props = node.get("properties")
            if isinstance(props, dict):
                found.append((path or "$", list(props)))
                for key, sub in props.items():
                    walk(sub, f"{path}.{key}" if path else key)
            items = node.get("items")
            if isinstance(items, dict):
                walk(items, f"{path}[]")

    walk(schema, "")
    return found


def rows_of[T: BaseModel](data: Mapping[str, Any], key: str, model: type[T]) -> list[T]:
    """The rows of ``data[key]`` that validate as ``model``, in order; malformed rows are skipped one by one."""
    raw = data.get(key)
    if not isinstance(raw, list):
        return []
    out: list[T] = []
    for item in raw[:MAX_ROWS]:
        try:
            out.append(model.model_validate(item))
        except ValidationError:
            continue
    return out


__all__ = [
    "MAX_ROWS",
    "AreaRowOut",
    "AreasOut",
    "BulletOut",
    "CoverOut",
    "CoverParagraphOut",
    "CoverageOut",
    "CoverageRowOut",
    "GateAnswerOut",
    "GateAnswersOut",
    "GateOut",
    "PhraseOut",
    "PostingOut",
    "RelevanceOut",
    "RelevanceRowOut",
    "RequirementOut",
    "property_order",
    "rows_of",
    "schema_of",
]
