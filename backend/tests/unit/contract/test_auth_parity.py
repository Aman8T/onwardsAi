"""tayyari.contract.auth must match shared/auth.ts exactly (interfaces, optionality, the currency union, the constant),
and the request models must refuse what the sign-in routes must refuse.

Reuses the TypeScript reader and the shape comparison of test_ts_parity.py.
"""

from __future__ import annotations

import re
from typing import get_args

import pytest
from pydantic import BaseModel, ValidationError

from tayyari.contract import auth
from tayyari.core.paths import REPO_ROOT
from tests.unit.contract.test_ts_parity import TsTypes, diff, model_shape

AUTH_TS = REPO_ROOT / "shared" / "auth.ts"

if not AUTH_TS.is_file():
    pytest.skip("the frontend sources are not part of this checkout", allow_module_level=True)


def _ts() -> TsTypes:
    return TsTypes(AUTH_TS.read_text(encoding="utf-8"))


INTERFACE_MODELS: dict[str, type[BaseModel]] = {
    "MeUser": auth.MeUser,
    "MeProfile": auth.MeProfile,
    "MeEntitlements": auth.MeEntitlements,
    "MeProviders": auth.MeProviders,
    "MeResponse": auth.MeResponse,
    "MagicLinkRequest": auth.MagicLinkRequest,
    "MagicLinkResponse": auth.MagicLinkResponse,
    "ClaimRequest": auth.ClaimRequest,
    "MePatch": auth.MePatch,
}


def test_every_interface_has_a_mirror() -> None:
    assert set(_ts().interfaces) == set(INTERFACE_MODELS)


@pytest.mark.parametrize("name", sorted(INTERFACE_MODELS))
def test_interface_matches_its_model(name: str) -> None:
    assert diff(_ts().interface(name), model_shape(INTERFACE_MODELS[name])) == []


def test_the_currency_union_matches() -> None:
    assert _ts().shape("Currency") == ("lit", frozenset(get_args(auth.Currency)))


def test_the_name_limit_matches() -> None:
    found = re.search(r"export const NAME_MAX_CHARS = (\d+);", AUTH_TS.read_text(encoding="utf-8"))
    assert found and int(found.group(1)) == auth.NAME_MAX_CHARS


def test_an_anonymous_me_has_no_user_key_at_all() -> None:
    body = auth.MeResponse(
        profiles=[],
        entitlements=auth.MeEntitlements(credits=0, freeMatchAvailable=False),
        providers=auth.MeProviders(google=True, magic=False),
        anonymousMatch=False,
    ).to_dict()
    assert body == {
        "profiles": [],
        "entitlements": {"credits": 0, "freeMatchAvailable": False},
        "providers": {"google": True, "magic": False},
        "anonymousMatch": False,
    }


def test_anonymous_match_is_a_required_boolean_on_both_sides() -> None:
    """``anonymousMatch: boolean`` (not optional) in shared/auth.ts, a required ``bool`` in the model: the web app
    reads it without a fallback, so a response that left it out would be a bug on the server."""
    _, fields = _ts().interface("MeResponse")
    assert fields["anonymousMatch"] == (False, ("bool",))  # (optional?, shape): not optional, a boolean
    assert auth.MeResponse.model_fields["anonymousMatch"].is_required()
    assert auth.MeResponse.model_fields["anonymousMatch"].annotation is bool
    with pytest.raises(ValidationError):
        auth.MeResponse.model_validate(
            {
                "profiles": [],
                "entitlements": {"credits": 0, "freeMatchAvailable": False},
                "providers": {"google": False, "magic": False},
            }
        )


# ───────────────────────── request models ─────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("ana@example.com", "ana@example.com"),
        ("  Ana.Rao+jobs@Example.COM \n", "ana.rao+jobs@example.com"),
        ("a@b.co", "a@b.co"),
        ("x@sub.domain.example.org", "x@sub.domain.example.org"),
        ("o'brien@example.ie", "o'brien@example.ie"),
        ("", None),
        ("plainaddress", None),
        ("@example.com", None),
        ("ana@", None),
        ("ana@example", None),  # no dot in the domain
        ("ana@@example.com", None),
        ("ana smith@example.com", None),
        ("ana@exam ple.com", None),
        ("ana@example..com", None),
        ("ana@-example.com", None),
        ("ana@example.com\r\nBcc: victim@example.com", None),
        ("<script>@example.com", None),
        ("ana@example.com,raj@example.com", None),
        ("a" * 65 + "@example.com", None),
        ("a@" + "b" * 250 + ".com", None),
        ("ana@exämple.com", None),
    ],
)
def test_email_normalisation(raw: str, expected: str | None) -> None:
    assert auth.normalise_email(raw) == expected


def test_a_magic_link_request_normalises_or_refuses() -> None:
    assert auth.MagicLinkRequest(email="  Ana@Example.com ").email == "ana@example.com"
    with pytest.raises(ValidationError):
        auth.MagicLinkRequest(email="nope")
    with pytest.raises(ValidationError):
        auth.MagicLinkRequest.model_validate({})
    with pytest.raises(ValidationError):
        auth.MagicLinkRequest(email="a" * 400 + "@example.com")


def test_a_patch_trims_the_name_and_checks_the_currency() -> None:
    assert auth.MePatch(name="  Ana   Rao ").name == "Ana Rao"
    assert auth.MePatch(currency="INR").currency == "INR"
    assert auth.MePatch().model_dump(exclude_none=True) == {}
    for bad in ({"name": "   "}, {"name": "x" * 81}, {"currency": "EUR"}, {"currency": "inr"}):
        with pytest.raises(ValidationError):
            auth.MePatch.model_validate(bad)


def test_a_claim_needs_a_profile_id() -> None:
    assert auth.ClaimRequest(profileId="abc").profileId == "abc"
    for bad in ({}, {"profileId": ""}, {"profileId": "x" * 97}):
        with pytest.raises(ValidationError):
            auth.ClaimRequest.model_validate(bad)
