"""Which email goes on forms (docs/auto-apply.md §3.4, §8 decision 5): the person's active tracking address while the
application inbox is on, else the saved email. Chosen in one place, the fill context of `ApplyEngine`, through the
``form_email`` seam; the saved answer itself never changes, and "ask each time" is respected."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from tayyari.apply.answers import BankEntry, FillContext, saved_value
from tayyari.apply.engine import ApplyEngine, with_form_email
from tayyari.core.settings import ApplySettings
from tayyari.storage.ports import StoredAnswer, StoredApplication
from tests.fakes.apply import FakeApplyRepo

T0 = datetime(2026, 11, 10, 9, 0, tzinfo=UTC)
USER = "u1"
TRACKING = "riya.sk7m4q@apply.leafout.in"


class NoOpener:
    async def new_page(self, *args: Any, **kwargs: Any) -> Any:  # pragma: no cover - never opened here
        raise AssertionError("no browser in these tests")


class Clock:
    def now(self) -> datetime:
        return T0

    def monotonic(self) -> float:
        return 0.0

    async def sleep(self, seconds: float) -> None:
        return None


def app() -> StoredApplication:
    return StoredApplication(
        id="ap1", owner_key=f"u:{USER}", user_id=USER, source="match", job_url="https://x", domain="x",
        ats="greenhouse", stage="queued", created_at=T0, updated_at=T0,
    )  # fmt: skip


async def engine(
    *, email: str | None = "riya@gmail.com", ask_each_time: bool = False, form_email: Any = None
) -> ApplyEngine:
    repo = FakeApplyRepo()
    if email is not None or ask_each_time:
        await repo.put_answer(
            StoredAnswer(user_id=USER, question_key="email", question="Email", source="you",
                         ask_each_time=ask_each_time, answered_at=T0, created_at=T0, updated_at=T0, value=email)
        )  # fmt: skip
    return ApplyEngine(repo, None, ApplySettings(), NoOpener(), Clock(), form_email=form_email)  # type: ignore[arg-type]


def returns(value: str | None) -> Any:
    async def form_email(user_id: str) -> str | None:
        assert user_id == USER
        return value

    return form_email


def email_of(ctx: FillContext) -> str | None:
    return saved_value(ctx, "email")[0]


def test_with_form_email_puts_the_tracking_address_in_place_of_the_saved_one() -> None:
    bank = {"email": BankEntry("riya@gmail.com", "resume", False, T0), "phone": BankEntry("+91 98765 43210")}
    swapped = with_form_email(bank, TRACKING)
    assert swapped["email"].value == TRACKING and swapped["phone"] == bank["phone"]
    assert bank["email"].value == "riya@gmail.com"  # the saved answer is untouched
    assert with_form_email({}, TRACKING)["email"].value == TRACKING


def test_without_an_address_or_with_ask_each_time_the_bank_stays() -> None:
    bank = {"email": BankEntry("riya@gmail.com")}
    assert with_form_email(bank, None) is bank
    asked = {"email": BankEntry(None, "you", True)}
    assert with_form_email(asked, TRACKING) is asked


async def test_the_fill_context_carries_the_active_tracking_address() -> None:
    eng = await engine(form_email=returns(TRACKING))
    assert email_of(await eng._context(app(), with_facts=False)) == TRACKING


async def test_no_address_or_no_seam_keeps_the_persons_own_email() -> None:
    off = await engine(form_email=returns(None))
    assert email_of(await off._context(app(), with_facts=False)) == "riya@gmail.com"
    plain = await engine()
    assert email_of(await plain._context(app(), with_facts=False)) == "riya@gmail.com"


async def test_ask_each_time_is_respected() -> None:
    eng = await engine(email=None, ask_each_time=True, form_email=returns(TRACKING))
    value, _, ask = saved_value(await eng._context(app(), with_facts=False), "email")
    assert value is None and ask is True


async def test_a_lookup_that_fails_falls_back_to_the_saved_email() -> None:
    async def broken(user_id: str) -> str | None:
        raise RuntimeError("database asleep")

    eng = await engine(form_email=broken)
    assert email_of(await eng._context(app(), with_facts=False)) == "riya@gmail.com"
