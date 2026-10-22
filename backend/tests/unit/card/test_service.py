"""The radar card's owner side: who may share, what the card draws (worked out in code), the link's life, the version, and
keeping the snapshot on the account's newest profile."""

from __future__ import annotations

import pytest

from tayyari.card.content import content_of
from tayyari.card.service import (
    BLOCKED_ANONYMOUS,
    CardProfileNotFound,
    CardRoleNotFound,
    CardSharingBlocked,
    NotShared,
    SkillNotOffered,
    drawn_areas,
    new_slug,
    offered_skills,
)
from tayyari.contract.card import CardNewLink, CardUpdate
from tayyari.contract.models import Profile
from tests.unit.card.support import PUBLIC, World, profile_doc, world

P1, P2, P3 = "1" * 32, "2" * 32, "3" * 32


def update(pid: str = P1, **over: object) -> CardUpdate:
    values: dict[str, object] = {"profileId": pid, "shared": True, "skills": [], "theme": "light"}
    values.update(over)
    return CardUpdate.model_validate(values)


async def owned(w: World) -> str:
    uid = await w.account()
    await w.profile(P1, owner=uid)
    return uid


# ───────────────────────── who may share ─────────────────────────


async def test_the_sample_is_offered_no_switch_and_cannot_be_shared() -> None:
    w = world()
    uid = await w.account()
    await w.profile(P1, sample=True)
    view = await w.service.view(uid, P1)
    assert view.blocked == "sample" and not view.settings.shared and view.slug is None
    assert [a.id for a in view.areas][:2] == ["frontend", "design-motion"]
    with pytest.raises(CardSharingBlocked):
        await w.service.update(uid, update())
    assert w.fakes.card.rows == {}


async def test_an_anonymous_graph_asks_to_sign_in_first() -> None:
    w = world()
    await w.profile(P1)
    assert (await w.service.view(None, P1)).blocked == "anonymous"
    with pytest.raises(CardSharingBlocked) as caught:
        await w.service.update(None, update())
    assert caught.value.user_message == BLOCKED_ANONYMOUS and caught.value.status == 403


async def test_another_accounts_graph_is_not_found() -> None:
    w = world()
    await owned(w)
    other = await w.account("ben@example.com")
    for call in (
        w.service.view(other, P1),
        w.service.update(other, update()),
        w.service.new_link(other, CardNewLink(profileId=P1)),
        w.service.view(None, P1),
        w.service.view(other, "9" * 32),
    ):
        with pytest.raises(CardProfileNotFound):
            await call


# ───────────────────────── what it draws ─────────────────────────


async def test_the_first_view_is_the_graph_with_sharing_off_and_stores_nothing() -> None:
    w = world()
    uid = await owned(w)
    view = await w.service.view(uid, P1)
    assert view.blocked is None and view.settings.shared is False and view.settings.theme == "light"
    assert [(a.short, a.score) for a in view.areas] == [
        ("Frontend", 81),
        ("Design & Moti…", 85),
        ("Backend & APIs", 58),
        ("AI & LLM", 36),
        ("Eng Practice", 55),
        ("Product & Col…", 55),
    ]
    assert all(a.roleTarget is None for a in view.areas)
    assert len(view.skillsOffered) == 8
    scores = [s.score for s in view.skillsOffered]
    assert scores == sorted(scores, reverse=True)
    assert view.rolesOffered[0].id == "design-engineer"
    assert view.slug is None and view.markdown is None
    assert w.fakes.card.rows == {}


def test_a_rim_label_is_at_most_fourteen_characters() -> None:
    doc = profile_doc(P1)
    doc["domains"][0]["short"] = "Frontend engineering at scale"
    doc["domains"][1]["short"] = ""
    areas = drawn_areas(Profile.model_validate(doc), None)
    assert areas[0][1].short == "Frontend engi…" and len(areas[0][1].short) == 14
    assert areas[1][1].short == doc["domains"][1]["name"][:13].rstrip() + "…"


def test_past_eight_areas_the_eight_strongest_stay_in_the_graphs_order() -> None:
    doc = profile_doc(P1)
    base = doc["domains"][0]
    doc["domains"] = [
        {**base, "id": f"d{i}", "name": f"Area {i}", "short": f"A{i}", "score": s}
        for i, s in enumerate([50, 10, 90, 20, 70, 30, 80, 40, 60, 5])
    ]
    for skill in doc["skills"]:
        skill["domainId"] = "d0"
    areas = drawn_areas(Profile.model_validate(doc), None)
    assert [aid for aid, _ in areas] == ["d0", "d2", "d3", "d4", "d5", "d6", "d7", "d8"]


def test_a_roles_level_is_the_mean_target_of_its_requirements_in_each_area() -> None:
    profile = Profile.model_validate(profile_doc(P1))
    role = next(r for r in profile.roles if r.id == "senior-frontend-engineer")
    area_of = {s.id: s.domainId for s in profile.skills}
    by_name = {s.name.casefold(): s.domainId for s in profile.skills}
    expect: dict[str, list[int]] = {}
    for req in role.requirements:
        area = area_of.get(req.skillId or "") or by_name.get(req.name.casefold())
        if area:
            expect.setdefault(area, []).append(req.target)
    areas = dict(drawn_areas(profile, role.id))
    assert expect, "the fixture role names areas"
    for aid, area in areas.items():
        if aid in expect:
            mean = sum(expect[aid]) / len(expect[aid])
            assert area.role_target == int(mean + 0.5)
        else:
            assert area.role_target == area.score


def test_a_requirement_without_a_skill_id_is_placed_by_the_skills_name() -> None:
    doc = profile_doc(P1)
    skill = doc["skills"][0]
    doc["roles"][0]["requirements"] = [
        {"name": skill["name"].upper(), "have": True, "current": 50, "target": 97}
    ]
    areas = dict(drawn_areas(Profile.model_validate(doc), doc["roles"][0]["id"]))
    assert areas[skill["domainId"]].role_target == 97


# ───────────────────────── settings, the link and the version ─────────────────────────


async def test_sharing_on_makes_a_link_and_off_removes_it() -> None:
    w = world()
    uid = await owned(w)
    on = await w.service.update(uid, update())
    assert on.settings.shared and on.slug == "aaaaaaaaaa01"
    assert on.pageUrl == f"{PUBLIC}/u/aaaaaaaaaa01"
    assert on.svgUrl == f"{PUBLIC}/r/aaaaaaaaaa01.svg"
    assert on.markdown == f"[![Skill shape]({PUBLIC}/r/aaaaaaaaaa01.svg)]({PUBLIC}/u/aaaaaaaaaa01)"
    stored = w.fakes.card.rows[uid]
    assert on.ogUrl == f"{PUBLIC}/og/aaaaaaaaaa01-{stored.version}.png"
    assert on.updatedAt == stored.updated_at.isoformat()
    assert await w.service.shared("aaaaaaaaaa01") is not None

    again = await w.service.update(uid, update(theme="dark"))
    assert again.slug == "aaaaaaaaaa01", "changing a setting keeps the link"

    off = await w.service.update(uid, update(shared=False))
    assert off.slug is None and off.pageUrl is None and off.markdown is None and not off.settings.shared
    assert w.fakes.card.rows[uid].slug is None
    assert await w.service.shared("aaaaaaaaaa01") is None

    back = await w.service.update(uid, update())
    assert back.slug == "aaaaaaaaaa02", "turning sharing on again makes a new link"


async def test_a_new_link_stops_the_old_one_at_once() -> None:
    w = world()
    uid = await owned(w)
    await w.service.update(uid, update())
    view = await w.service.new_link(uid, CardNewLink(profileId=P1))
    assert view.slug == "aaaaaaaaaa02"
    assert await w.service.shared("aaaaaaaaaa01") is None
    assert await w.service.shared("aaaaaaaaaa02") is not None


async def test_a_new_link_needs_sharing_on() -> None:
    w = world()
    uid = await owned(w)
    with pytest.raises(NotShared):
        await w.service.new_link(uid, CardNewLink(profileId=P1))
    await w.service.update(uid, update(shared=False))
    with pytest.raises(NotShared):
        await w.service.new_link(uid, CardNewLink(profileId=P1))


async def test_skills_must_be_offered_and_the_role_on_the_graph() -> None:
    w = world()
    uid = await owned(w)
    profile = Profile.model_validate(profile_doc(P1))
    weakest = min(profile.skills, key=lambda s: s.score)
    assert weakest.id not in {s.id for s in offered_skills(profile)}
    with pytest.raises(SkillNotOffered) as bad_skill:
        await w.service.update(uid, update(skills=[weakest.id]))
    assert bad_skill.value.status == 400
    with pytest.raises(CardRoleNotFound) as bad_role:
        await w.service.update(uid, update(roleId="astronaut"))
    assert bad_role.value.status == 400
    assert w.fakes.card.rows == {}


async def test_chosen_skills_and_role_are_drawn_and_shown() -> None:
    w = world()
    uid = await owned(w)
    top = [s.id for s in offered_skills(Profile.model_validate(profile_doc(P1)))[:3]]
    view = await w.service.update(
        uid, update(skills=[top[1], top[0], top[1]], roleId="senior-frontend-engineer")
    )
    assert view.settings.skills == [top[1], top[0]]
    assert view.settings.roleId == "senior-frontend-engineer"
    assert all(a.roleTarget is not None for a in view.areas)
    content = content_of(w.fakes.card.rows[uid])
    assert [s.name for s in content.skills] == [
        s.name for s in view.skillsOffered if s.id in (top[1], top[0])
    ][::-1]
    assert content.role_title == "Senior Frontend Engineer"
    assert view.svgUrl == f"{PUBLIC}/r/aaaaaaaaaa01.svg?compare=senior-frontend-engineer"


async def test_the_readme_snippet_follows_the_theme() -> None:
    w = world()
    uid = await owned(w)
    dark = await w.service.update(uid, update(theme="dark", roleId="design-engineer"))
    assert dark.svgUrl == f"{PUBLIC}/r/aaaaaaaaaa01.svg?theme=dark&compare=design-engineer"
    clear = await w.service.update(uid, update(theme="transparent"))
    assert clear.svgUrl == f"{PUBLIC}/r/aaaaaaaaaa01.svg?theme=transparent"
    match = await w.service.update(uid, update(theme="match", roleId="design-engineer"))
    assert match.settings.theme == "match"
    assert match.svgUrl == f"{PUBLIC}/r/aaaaaaaaaa01.svg?compare=design-engineer"
    assert match.markdown == (
        f'<a href="{PUBLIC}/u/aaaaaaaaaa01">\n  <picture>\n'
        f'    <source media="(prefers-color-scheme: dark)" srcset="{PUBLIC}/r/aaaaaaaaaa01.svg?theme=dark&compare=design-engineer">\n'
        f'    <img alt="Skill shape" src="{PUBLIC}/r/aaaaaaaaaa01.svg?compare=design-engineer">\n  </picture>\n</a>'
    )


async def test_the_version_and_the_date_change_only_with_what_is_drawn() -> None:
    w = world()
    uid = await owned(w)
    await w.service.update(uid, update())
    first = w.fakes.card.rows[uid]
    w.clock.advance(86_400)
    await w.service.update(uid, update(theme="dark"))
    await w.service.new_link(uid, CardNewLink(profileId=P1))
    await w.service.update(uid, update(shared=False))
    same = w.fakes.card.rows[uid]
    assert (same.version, same.updated_at) == (first.version, first.updated_at)
    w.clock.advance(86_400)
    await w.service.update(uid, update(roleId="design-engineer"))
    changed = w.fakes.card.rows[uid]
    assert changed.version != first.version and changed.updated_at > first.updated_at
    assert len(changed.version) == 8 and int(changed.version, 16) >= 0


# ───────────────────────── keeping it current ─────────────────────────


async def test_a_refresh_picks_up_the_accounts_newest_profile() -> None:
    w = world()
    uid = await owned(w)
    skill = offered_skills(Profile.model_validate(profile_doc(P1)))[0]
    await w.service.update(uid, update(skills=[skill.id], roleId="design-engineer"))
    before = w.fakes.card.rows[uid]

    doc = profile_doc(P2)
    doc["domains"][0]["score"] = 12
    doc["roles"] = [r for r in doc["roles"] if r["id"] != "design-engineer"]
    await w.profile(P2, owner=uid, doc=doc)
    after = await w.service.refresh(uid)
    assert after is not None and after.profile_id == P2
    assert after.version != before.version and after.areas[0]["score"] == 12
    assert after.role_id is None, "a role the new graph no longer has is dropped"
    assert [s["id"] for s in after.skills] == [skill.id]
    assert after.slug == before.slug and after.shared

    assert await w.service.refresh(uid) == after, "nothing changed, nothing written"


async def test_opening_the_sheet_refreshes_a_card_left_on_an_older_profile() -> None:
    w = world()
    uid = await owned(w)
    await w.service.update(uid, update())
    doc = profile_doc(P2)
    doc["domains"][1]["score"] = 3
    await w.profile(P2, owner=uid, doc=doc)
    view = await w.service.view(uid, P1)
    assert view.areas[1].score == 3
    assert w.fakes.card.rows[uid].profile_id == P2


async def test_refresh_without_a_card_does_nothing() -> None:
    w = world()
    uid = await owned(w)
    assert await w.service.refresh(uid) is None
    assert w.fakes.card.rows == {}


async def test_the_card_holds_nothing_of_the_profile_but_what_it_draws() -> None:
    w = world()
    uid = await owned(w)
    top = [s.id for s in offered_skills(Profile.model_validate(profile_doc(P1)))[:3]]
    await w.service.update(uid, update(skills=top, roleId="senior-frontend-engineer"))
    stored = w.fakes.card.rows[uid]
    doc = profile_doc(P1)
    blob = repr(stored)
    for skill in doc["skills"]:
        for ev in skill["evidence"]:
            assert ev["text"] not in blob
    assert doc["person"]["name"] not in blob
    assert {k for a in stored.areas for k in a} <= {"id", "name", "short", "score", "roleTarget"}
    assert {k for s in stored.skills for k in s} == {"id", "name", "score"}


# ───────────────────────── the public lookup ─────────────────────────


async def test_a_slug_of_the_wrong_shape_is_never_looked_up() -> None:
    w = world()
    uid = await owned(w)
    await w.service.update(uid, update())
    for bad in ("", "AAAAAAAAAA01", "aaaaaaaaaa0", "aaaaaaaaaa011", "aaaaaaaaaa-1", "../etc/passw"):
        assert await w.service.shared(bad) is None
    assert await w.service.shared("aaaaaaaaaa01") is not None


def test_new_slugs_are_twelve_lowercase_letters_and_digits() -> None:
    slugs = {new_slug() for _ in range(200)}
    assert len(slugs) == 200
    assert all(len(s) == 12 and s.isascii() and s.isalnum() and s == s.lower() for s in slugs)
