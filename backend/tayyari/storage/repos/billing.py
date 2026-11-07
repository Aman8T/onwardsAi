"""Orders, balances and the credit ledger.

Every change to a balance is one transaction that starts with a write to the account's ``entitlements`` row (an
``UPDATE ... SET updated_at`` that also returns the balance). On Postgres that write takes the row lock, so a second
request for the same account waits for the first and then sees its result; on SQLite it takes the database's write
lock. Nothing is ever read first and written later, which is what makes a lost update (two requests spending the
same last credit) impossible, and the ledger rows written under the lock keep the invariant ``credits == sum(delta)``.

The ledger is append-only. A use that is given back (`release`) is not deleted or edited: a ``refund`` row whose
``ref`` is ``refund:<id of the use>`` reverses it, and a use with such a row no longer counts, not for the idempotency
check and not for the month's free allowance.

An auto-apply credit hold is two-phase on the same ledger. `hold` writes ``apply_free`` (one of the account's free
applications, delta 0) or ``apply_hold`` (minus the price, or 0 while no price is set) with ``ref``
``apply:<application id>``. `capture` charges it with an ``apply_capture`` row (delta 0, ``ref`` ``capture:<id of the
hold row>``); `release` gives it back with a ``refund`` row like any other use, and refuses a captured hold. A hold
with neither is still open. All three run under the account's lock and are idempotent.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta
from typing import Any, Final

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from tayyari.core.clock import Clock, SystemClock

from ..db import Database, dialect_insert, rowcount
from ..ports import ConsumeOutcome, ConsumeSource, HoldState, SettleResult, StoredEntitlements, StoredOrder
from ..tables import CreditLedgerRow, EntitlementRow, OrderRow

MATCH_REASONS: Final = ("match", "free_monthly")
"""Ledger reasons of a paid posting match (`consume`)."""
HOLD_REASONS: Final = ("apply_hold", "apply_free")
"""Ledger reasons of an auto-apply credit hold (`hold`)."""
USE_REASONS: Final = (*MATCH_REASONS, *HOLD_REASONS)
"""Ledger reasons that are a use of an entitlement (the ones `release` can reverse)."""
CAPTURE_REASON: Final = "apply_capture"
REFUND_PREFIX: Final = "refund:"
CAPTURE_PREFIX: Final = "capture:"

_ORDER_COLUMNS = (
    OrderRow.id,
    OrderRow.user_id,
    OrderRow.provider,
    OrderRow.provider_order_id,
    OrderRow.product,
    OrderRow.currency,
    OrderRow.amount_minor,
    OrderRow.status,
    OrderRow.created_at,
    OrderRow.paid_at,
    OrderRow.raw,
)


def _order(row: Any) -> StoredOrder:
    return StoredOrder(
        id=row.id,
        user_id=row.user_id,
        provider=row.provider,
        provider_order_id=row.provider_order_id,
        product=row.product,
        currency=row.currency,
        amount_minor=row.amount_minor,
        status=row.status,
        created_at=row.created_at,
        paid_at=row.paid_at,
        raw=row.raw,
    )


def _new_id() -> str:
    return secrets.token_hex(16)


def _source_of(reason: str, delta: int) -> ConsumeSource:
    if reason == "free_monthly":
        return "free_monthly"
    if reason == "apply_free":
        return "free_apply"
    if reason == "apply_hold":
        return "credit" if delta < 0 else "hold"
    return "credit" if delta < 0 else "pass"


class SqlBillingRepo:
    def __init__(self, db: Database, clock: Clock | None = None) -> None:
        self._db = db
        self._clock = clock or SystemClock()

    # ───────────────────────── orders ─────────────────────────

    async def add_order(self, rec: StoredOrder) -> None:
        async with self._db.session() as s:
            s.add(
                OrderRow(
                    id=rec.id,
                    user_id=rec.user_id,
                    provider=rec.provider,
                    provider_order_id=rec.provider_order_id,
                    product=rec.product,
                    currency=rec.currency,
                    amount_minor=rec.amount_minor,
                    status=rec.status,
                    created_at=rec.created_at,
                    paid_at=rec.paid_at,
                    raw=rec.raw,
                )
            )

    async def get_order(self, order_id: str) -> StoredOrder | None:
        async with self._db.session() as s:
            row = (await s.execute(select(*_ORDER_COLUMNS).where(OrderRow.id == order_id))).one_or_none()
        return None if row is None else _order(row)

    async def get_order_by_provider_id(self, provider_order_id: str) -> StoredOrder | None:
        stmt = select(*_ORDER_COLUMNS).where(OrderRow.provider_order_id == provider_order_id)
        async with self._db.session() as s:
            row = (await s.execute(stmt)).one_or_none()
        return None if row is None else _order(row)

    async def settle_order(
        self,
        provider_order_id: str,
        *,
        now: datetime,
        credits: int,
        pass_days: int,
        raw: dict[str, Any] | None,
        amount_minor: int | None = None,
        currency: str | None = None,
    ) -> SettleResult:
        conditions = [
            OrderRow.provider_order_id == provider_order_id,
            OrderRow.status.in_(("created", "failed")),
        ]
        if amount_minor is not None:
            conditions.append(OrderRow.amount_minor == amount_minor)
        if currency is not None:
            conditions.append(OrderRow.currency == currency)
        flip = (
            update(OrderRow)
            .where(*conditions)
            .values(status="paid", paid_at=now, raw=raw)
            .returning(*_ORDER_COLUMNS)
        )
        async with self._db.session() as s:
            row = (await s.execute(flip)).one_or_none()
            if row is None:
                existing = (
                    await s.execute(
                        select(*_ORDER_COLUMNS).where(OrderRow.provider_order_id == provider_order_id)
                    )
                ).one_or_none()
                if existing is None:
                    return SettleResult("unknown_order", None)
                if existing.status in ("paid", "refunded"):
                    return SettleResult("already_paid", _order(existing))
                return SettleResult("mismatch", _order(existing))
            order = _order(row)
            if order.user_id is None:
                return SettleResult("orphaned", order)
            await self._apply_grant(
                s, order.user_id, credits=credits, pass_days=pass_days, ref=order.id, now=now
            )
            return SettleResult("granted", order)

    async def fail_order(self, provider_order_id: str) -> bool:
        stmt = (
            update(OrderRow)
            .where(OrderRow.provider_order_id == provider_order_id, OrderRow.status == "created")
            .values(status="failed")
        )
        async with self._db.session() as s:
            return rowcount(await s.execute(stmt)) > 0

    # ───────────────────────── balances ─────────────────────────

    async def entitlements(self, user_id: str, *, month_start: datetime) -> StoredEntitlements:
        async with self._db.session() as s:
            row = (
                await s.execute(
                    select(EntitlementRow.credits, EntitlementRow.pass_until).where(
                        EntitlementRow.user_id == user_id
                    )
                )
            ).one_or_none()
            used = await _live_uses(s, user_id, reasons=("free_monthly",), since=month_start)
        credits, pass_until = (0, None) if row is None else (row.credits, row.pass_until)
        return StoredEntitlements(
            user_id=user_id, credits=credits, pass_until=pass_until, free_used=len(used)
        )

    async def grant(self, user_id: str, *, credits: int, pass_days: int, ref: str, now: datetime) -> bool:
        async with self._db.session() as s:
            return await self._apply_grant(s, user_id, credits=credits, pass_days=pass_days, ref=ref, now=now)

    async def consume(
        self, user_id: str, ref: str, *, now: datetime, month_start: datetime, free_allowance: int
    ) -> ConsumeOutcome | None:
        async with self._db.session() as s:
            credits, pass_until = await self._lock(s, user_id, now)
            earlier = await _live_uses(s, user_id, ref=ref, reasons=MATCH_REASONS)
            if earlier:
                use = earlier[0]
                return ConsumeOutcome(_source_of(use.reason, use.delta), credits, pass_until, replayed=True)
            if pass_until is not None and pass_until > now:
                _ledger(s, user_id, 0, "match", ref, now)
                return ConsumeOutcome("pass", credits, pass_until)
            used = await _live_uses(s, user_id, reasons=("free_monthly",), since=month_start)
            if len(used) < free_allowance:
                _ledger(s, user_id, 0, "free_monthly", ref, now)
                return ConsumeOutcome("free_monthly", credits, pass_until)
            if credits > 0:
                await s.execute(
                    update(EntitlementRow)
                    .where(EntitlementRow.user_id == user_id)
                    .values(credits=EntitlementRow.credits - 1)
                )
                _ledger(s, user_id, -1, "match", ref, now)
                return ConsumeOutcome("credit", credits - 1, pass_until)
            return None

    async def release(self, user_id: str, ref: str, *, now: datetime) -> bool:
        async with self._db.session() as s:
            await self._lock(s, user_id, now)
            earlier = await _live_uses(s, user_id, ref=ref, reasons=USE_REASONS)
            if not earlier:
                return False
            use = earlier[0]
            if use.reason in HOLD_REASONS and await _captured(s, user_id, use.id):
                return False  # charged: a capture is never given back here
            if use.delta < 0:
                await s.execute(
                    update(EntitlementRow)
                    .where(EntitlementRow.user_id == user_id)
                    .values(credits=EntitlementRow.credits - use.delta)
                )
            _ledger(s, user_id, -use.delta, "refund", f"{REFUND_PREFIX}{use.id}", now)
            return True

    # ───────────────────────── auto-apply credit hold ─────────────────────────

    async def hold(
        self, user_id: str, ref: str, *, now: datetime, free_allowance: int, cost: int | None
    ) -> ConsumeOutcome | None:
        async with self._db.session() as s:
            credits, pass_until = await self._lock(s, user_id, now)
            earlier = await _live_uses(s, user_id, ref=ref, reasons=HOLD_REASONS)
            if earlier:
                use = earlier[0]
                return ConsumeOutcome(_source_of(use.reason, use.delta), credits, pass_until, replayed=True)
            free_used = await _live_uses(s, user_id, reasons=("apply_free",))
            if len(free_used) < free_allowance:
                _ledger(s, user_id, 0, "apply_free", ref, now)
                return ConsumeOutcome("free_apply", credits, pass_until)
            price = 0 if cost is None else max(cost, 0)
            if price == 0:
                _ledger(s, user_id, 0, "apply_hold", ref, now)
                return ConsumeOutcome("hold", credits, pass_until)
            if credits < price:
                return None
            await s.execute(
                update(EntitlementRow)
                .where(EntitlementRow.user_id == user_id)
                .values(credits=EntitlementRow.credits - price)
            )
            _ledger(s, user_id, -price, "apply_hold", ref, now)
            return ConsumeOutcome("credit", credits - price, pass_until)

    async def capture(self, user_id: str, ref: str, *, now: datetime) -> bool:
        async with self._db.session() as s:
            await self._lock(s, user_id, now)
            live = await _live_uses(s, user_id, ref=ref, reasons=HOLD_REASONS)
            if not live:
                return False
            held = live[0]
            if not await _captured(s, user_id, held.id):
                _ledger(s, user_id, 0, CAPTURE_REASON, f"{CAPTURE_PREFIX}{held.id}", now)
            return True

    async def hold_state(self, user_id: str, ref: str) -> HoldState | None:
        async with self._db.session() as s:
            ever = (
                await s.execute(
                    select(CreditLedgerRow.id).where(
                        CreditLedgerRow.user_id == user_id,
                        CreditLedgerRow.ref == ref,
                        CreditLedgerRow.reason.in_(HOLD_REASONS),
                    )
                )
            ).first()
            if ever is None:
                return None
            live = await _live_uses(s, user_id, ref=ref, reasons=HOLD_REASONS)
            if not live:
                return "released"
            return "charged" if await _captured(s, user_id, live[0].id) else "held"

    # ───────────────────────── inside a transaction ─────────────────────────

    async def _lock(self, s: AsyncSession, user_id: str, now: datetime) -> tuple[int, datetime | None]:
        """Make sure the account has a balance row, take its lock, and return ``(credits, pass_until)``."""
        await s.execute(
            dialect_insert(self._db.dialect, EntitlementRow)
            .values(user_id=user_id, credits=0, pass_until=None, updated_at=now)
            .on_conflict_do_nothing(index_elements=[EntitlementRow.user_id])
        )
        row = (
            await s.execute(
                update(EntitlementRow)
                .where(EntitlementRow.user_id == user_id)
                .values(updated_at=now)
                .returning(EntitlementRow.credits, EntitlementRow.pass_until)
            )
        ).one()
        return row.credits, row.pass_until

    async def _apply_grant(
        self, s: AsyncSession, user_id: str, *, credits: int, pass_days: int, ref: str, now: datetime
    ) -> bool:
        _credits, pass_until = await self._lock(s, user_id, now)
        already = (
            await s.execute(
                select(CreditLedgerRow.id).where(
                    CreditLedgerRow.user_id == user_id,
                    CreditLedgerRow.reason == "purchase",
                    CreditLedgerRow.ref == ref,
                )
            )
        ).first()
        if already is not None:
            return False
        values: dict[str, Any] = {}
        if credits:
            values["credits"] = EntitlementRow.credits + credits
        if pass_days:
            start = max(now, pass_until) if pass_until is not None else now
            values["pass_until"] = start + timedelta(days=pass_days)
        if values:
            await s.execute(update(EntitlementRow).where(EntitlementRow.user_id == user_id).values(**values))
        _ledger(s, user_id, credits, "purchase", ref, now)
        return True


def _ledger(s: AsyncSession, user_id: str, delta: int, reason: str, ref: str | None, now: datetime) -> None:
    s.add(CreditLedgerRow(id=_new_id(), user_id=user_id, delta=delta, reason=reason, ref=ref, created_at=now))


async def _captured(s: AsyncSession, user_id: str, hold_id: str) -> bool:
    """Whether the hold row ``hold_id`` has been charged (an ``apply_capture`` row points at it)."""
    found = (
        await s.execute(
            select(CreditLedgerRow.id).where(
                CreditLedgerRow.user_id == user_id,
                CreditLedgerRow.reason == CAPTURE_REASON,
                CreditLedgerRow.ref == f"{CAPTURE_PREFIX}{hold_id}",
            )
        )
    ).first()
    return found is not None


async def _live_uses(
    s: AsyncSession,
    user_id: str,
    *,
    reasons: tuple[str, ...],
    ref: str | None = None,
    since: datetime | None = None,
) -> list[Any]:
    """The uses of an entitlement (ledger rows with one of ``reasons``) that have not been given back, newest first."""
    stmt = select(
        CreditLedgerRow.id, CreditLedgerRow.delta, CreditLedgerRow.reason, CreditLedgerRow.created_at
    ).where(CreditLedgerRow.user_id == user_id, CreditLedgerRow.reason.in_(reasons))
    if ref is not None:
        stmt = stmt.where(CreditLedgerRow.ref == ref)
    if since is not None:
        stmt = stmt.where(CreditLedgerRow.created_at >= since)
    rows = (await s.execute(stmt.order_by(CreditLedgerRow.created_at.desc(), CreditLedgerRow.id))).all()
    if not rows:
        return []
    refunds = (
        await s.execute(
            select(CreditLedgerRow.ref).where(
                CreditLedgerRow.user_id == user_id,
                CreditLedgerRow.reason == "refund",
                CreditLedgerRow.ref.in_([f"{REFUND_PREFIX}{row.id}" for row in rows]),
            )
        )
    ).scalars()
    given_back = set(refunds)
    return [row for row in rows if f"{REFUND_PREFIX}{row.id}" not in given_back]


__all__ = [
    "CAPTURE_PREFIX",
    "CAPTURE_REASON",
    "HOLD_REASONS",
    "MATCH_REASONS",
    "REFUND_PREFIX",
    "USE_REASONS",
    "SqlBillingRepo",
]
