"""The report's owner side: who may share, the link's life, the choices it keeps, the employer names it finds, the stored
content (public and redacted only), and keeping the snapshot on the account's newest profile."""

from __future__ import annotations

import json

import pytest

from tayyari.contract.report import DEFAULT_CHOICES, ReportNewLink, ReportUpdate
from tayyari.report.service import (
    BLOCKED_ANONYMOUS,
    QuoteNotOffered,
    ReportNotShared,
    ReportProfileNotFound,
    ReportSharingBlocked,
    new_slug,
)
from tests.unit.report.support import ORG, PUBLIC, World, profile_doc, world

P1, P2 = "1" * 32, "2" * 32


def update(pid: str = P1, shared: bool = True, **choices: object) -> ReportUpdate:
    return ReportUpdate.model_validate(
        {"profileId": pid, "shared": shared, "choices": {**DEFAULT_CHOICES.model_dump(), **choices}}
    )


async def owned(w: World, *, employer: bool = True) -> str:
    uid = await w.account()
    await w.profile(P1, owner=uid)
    if employer:
        await w.resume_with_employer(P1)
    return uid


# ───────────────────────── who may share ─────────────────────────


async def test_the_sample_is_offered_no_switch_and_cannot_be_shared() -> None:
    w = world()
    uid = await w.account()
    await w.profile(P1, sample=True)
    view = await w.service.view(uid, P1)
    assert view.blocked == "sample" and view.slug is None and view.offered.roles
    with pytest.raises(ReportSharingBlocked):
        await w.service.update(uid, update())
    assert w.fakes.report.rows == {}


async def test_an_anonymous_graph_asks_to_sign_in_first() -> None:
    w = world()
    await w.profile(P1)
    assert (await w.service.view(None, P1)).blocked == "anonymous"
    with pytest.raises(ReportSharingBlocked) as caught:
        await w.service.update(None, update())
    assert caught.value.user_message == BLOCKED_ANONYMOUS and caught.value.status == 403


async def test_another_accounts_graph_is_not_found() -> None:
    w = world()
    await owned(w)
    other = await w.account("ben@example.com")
    for call in (
        w.service.view(other, P1),
        w.service.update(other, update()),
        w.service.new_link(other, ReportNewLink(profileId=P1)),
        w.service.view(None, P1),
        w.service.view(other, "9" * 32),
    ):
        with pytest.raises(ReportProfileNotFound):
            await call


# ───────────────────────── the link ─────────────────────────


async def test_sharing_makes_a_link_turning_off_stops_it_and_a_new_link_replaces_it() -> None:
    w = world()
    uid = await owned(w)
    view = await w.service.update(uid, update())
    assert view.slug == "rrrrrrrrrr01"
    assert view.pageUrl == f"{PUBLIC}/next/rrrrrrrrrr01"
    assert view.ogUrl is not None and view.ogUrl.startswith(f"{PUBLIC}/og/next/rrrrrrrrrr01-")
    assert await w.service.shared("rrrrrrrrrr01") is not None

    again = await w.service.update(uid, update(showName=False))
    assert again.slug == "rrrrrrrrrr01"  # a change of choices keeps the link

    fresh = await w.service.new_link(uid, ReportNewLink(profileId=P1))
    assert fresh.slug == "rrrrrrrrrr02"
    assert await w.service.shared("rrrrrrrrrr01") is None
    assert await w.service.shared("rrrrrrrrrr02") is not None

    off = await w.service.update(uid, update(shared=False))
    assert off.slug is None and off.pageUrl is None
    assert await w.service.shared("rrrrrrrrrr02") is None
    with pytest.raises(ReportNotShared):
        await w.service.new_link(uid, ReportNewLink(profileId=P1))


async def test_a_slug_of_the_wrong_shape_is_never_looked_up() -> None:
    w = world()
    for bad in ("", "RRRRRRRRRR01", "rrrrrrrrrr0", "rrrrrrrrrr012", "../../etc/pa"):
        assert await w.service.shared(bad) is None
    assert len(new_slug()) == 12 and new_slug().isalnum()


# ───────────────────────── what is stored ─────────────────────────


async def test_the_stored_content_is_the_public_page_redacted_and_nothing_else() -> None:
    w = world()
    uid = await owned(w)
    view = await w.service.update(uid, update())
    stored = w.fakes.report.rows[uid]
    blob = json.dumps(stored.content).casefold()
    assert "zentrix" not in blob
    assert P1 not in blob and uid not in blob
    assert stored.content == view.public.model_dump(mode="json")
    assert view.orgsFound == 1
    assert w.service.content(stored) == view.public


async def test_without_a_resume_draft_no_employer_is_known_and_the_page_quotes_nothing() -> None:
    w = world()
    uid = await owned(w, employer=False)
    view = await w.service.update(uid, update())
    assert view.orgsFound == 0
    assert all(r.quotes == [] for r in view.public.roles)
    assert "zentrix" not in json.dumps(w.fakes.report.rows[uid].content).casefold()


async def test_a_hidden_quote_must_be_one_the_report_offers() -> None:
    w = world()
    uid = await owned(w)
    view = await w.service.view(uid, P1)
    first = view.offered.roles[0].quotes[0].id
    hidden = await w.service.update(uid, update(hiddenQuotes=[first, first]))
    assert hidden.choices.hiddenQuotes == [first]
    assert first not in {q.id for r in hidden.public.roles for q in r.quotes}
    with pytest.raises(QuoteNotOffered):
        await w.service.update(uid, update(hiddenQuotes=["notaquote12"]))


async def test_the_version_and_date_move_only_with_the_page() -> None:
    w = world()
    uid = await owned(w)
    await w.service.update(uid, update())
    first = w.fakes.report.rows[uid]
    w.clock.advance(3600)
    await w.service.update(uid, update())
    assert w.fakes.report.rows[uid].version == first.version
    assert w.fakes.report.rows[uid].updated_at == first.updated_at
    w.clock.advance(3600)
    await w.service.update(uid, update(showName=False))
    assert w.fakes.report.rows[uid].version != first.version
    assert w.fakes.report.rows[uid].updated_at > first.updated_at


# ───────────────────────── keeping it current ─────────────────────────


async def test_a_new_profile_redraws_the_report_and_drops_hidden_lines_it_no_longer_has() -> None:
    w = world()
    uid = await owned(w)
    view = await w.service.view(uid, P1)
    first = view.offered.roles[0].quotes[0].id
    await w.service.update(uid, update(hiddenQuotes=[first]))
    doc = profile_doc(P2)
    for s in doc["skills"]:
        s["evidence"] = [{"sourceId": "resume", "text": f"A new line about {s['name']}."}]
    await w.profile(P2, owner=uid, doc=doc)
    await w.resume_with_employer(P2)
    after = await w.service.view(uid, P2)
    stored = w.fakes.report.rows[uid]
    assert stored.profile_id == P2
    assert after.choices.hiddenQuotes == []
    assert after.slug == "rrrrrrrrrr01"


async def test_the_paid_step_is_offered_only_when_configured() -> None:
    w = world()
    uid = await owned(w)
    assert (await w.service.view(uid, P1)).offerUrl is None
    w2 = world(offer_url=" https://rzp.io/l/eight-openings ")
    uid2 = await owned(w2)
    assert (await w2.service.view(uid2, P1)).offerUrl == "https://rzp.io/l/eight-openings"


async def test_the_employer_comes_from_the_resume_draft() -> None:
    w = world()
    uid = await owned(w, employer=False)
    await w.resume_with_employer(P1, org=ORG)
    view = await w.service.view(uid, P1)
    assert view.orgsFound == 1
    assert any("their employer" in q.text for r in view.public.roles for q in r.quotes)
