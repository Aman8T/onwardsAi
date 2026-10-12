"""tayyari.contract.billing must match shared/billing.ts exactly (interfaces, optionality, the unions, the constant),
and the request models must refuse what the billing routes must refuse.

Reuses the TypeScript reader and the shape comparison of test_ts_parity.py. `Currency` is owned by shared/auth.ts, so
the two files are read together.
"""

from __future__ import annotations

import re
from typing import get_args

import pytest
from pydantic import BaseModel, ValidationError

from tayyari.contract import billing
from tayyari.core.paths import REPO_ROOT
from tests.unit.contract.test_ts_parity import TsTypes, diff, model_shape

AUTH_TS = REPO_ROOT / "shared" / "auth.ts"
BILLING_TS = REPO_ROOT / "shared" / "billing.ts"

if not (AUTH_TS.is_file() and BILLING_TS.is_file()):
    pytest.skip("the frontend sources are not part of this checkout", allow_module_level=True)


def _ts() -> TsTypes:
    return TsTypes(AUTH_TS.read_text(encoding="utf-8") + "\n" + BILLING_TS.read_text(encoding="utf-8"))


INTERFACE_MODELS: dict[str, type[BaseModel]] = {
    "ProductPrices": billing.ProductPrices,
    "PriceBook": billing.PriceBook,
    "EntitlementsResponse": billing.EntitlementsResponse,
    "CheckoutRequest": billing.CheckoutRequest,
    "CheckoutResponse": billing.CheckoutResponse,
    "VerifyRequest": billing.VerifyRequest,
    "VerifyResponse": billing.VerifyResponse,
}


def test_every_interface_has_a_mirror() -> None:
    own = TsTypes(BILLING_TS.read_text(encoding="utf-8"))
    assert set(own.interfaces) == set(INTERFACE_MODELS)


@pytest.mark.parametrize("name", sorted(INTERFACE_MODELS))
def test_interface_matches_its_model(name: str) -> None:
    assert diff(_ts().interface(name), model_shape(INTERFACE_MODELS[name])) == []


def test_the_unions_match() -> None:
    ts = _ts()
    assert ts.shape("Product") == ("lit", frozenset(get_args(billing.Product)))
    assert ts.shape("Provider") == ("lit", frozenset(get_args(billing.Provider)))
    assert ts.shape("Currency") == ("lit", frozenset(get_args(billing.Currency)))


def test_the_timezone_limit_matches() -> None:
    found = re.search(r"export const TIMEZONE_MAX_CHARS = (\d+);", BILLING_TS.read_text(encoding="utf-8"))
    assert found and int(found.group(1)) == billing.TIMEZONE_MAX_CHARS


def test_an_empty_price_stays_an_empty_object_on_the_wire() -> None:
    body = billing.EntitlementsResponse(
        credits=0,
        freeMatchAvailable=False,
        currency="USD",
        prices=billing.PriceBook(INR=billing.ProductPrices(), USD=billing.ProductPrices(pack3=900)),
    ).to_dict()
    assert body == {
        "credits": 0,
        "freeMatchAvailable": False,
        "currency": "USD",
        "prices": {"INR": {}, "USD": {"pack3": 900}},
    }


# ───────────────────────── request models ─────────────────────────


def test_a_checkout_request_names_a_known_product_and_currency() -> None:
    assert billing.CheckoutRequest(product="pack3").currency is None
    assert (
        billing.CheckoutRequest(product="pass30", currency="INR", timezone="Asia/Kolkata").timezone
        == "Asia/Kolkata"
    )
    for bad in (
        {},
        {"product": "pack30"},
        {"product": "annual"},
        {"product": "pack3", "currency": "EUR"},
        {"product": "pack3", "currency": "inr"},
        {"product": "pack3", "timezone": "x" * 65},
    ):
        with pytest.raises(ValidationError):
            billing.CheckoutRequest.model_validate(bad)


def test_the_price_is_never_taken_from_the_request() -> None:
    parsed = billing.CheckoutRequest.model_validate(
        {"product": "pack3", "amount": 1, "price": 1, "amount_minor": 1}
    )
    assert set(parsed.model_dump()) == {"product", "currency", "timezone"}


def test_a_verify_request_needs_all_three_razorpay_values() -> None:
    ok = {"razorpay_order_id": "order_1", "razorpay_payment_id": "pay_1", "razorpay_signature": "ab" * 32}
    assert billing.VerifyRequest.model_validate(ok).razorpay_order_id == "order_1"
    for missing in ok:
        with pytest.raises(ValidationError):
            billing.VerifyRequest.model_validate({k: v for k, v in ok.items() if k != missing})
    for bad in ({**ok, "razorpay_signature": ""}, {**ok, "razorpay_order_id": "x" * 129}):
        with pytest.raises(ValidationError):
            billing.VerifyRequest.model_validate(bad)
