"""The radar card's owner side (R1): what the share sheet shows, the settings it saves, the link, and keeping the snapshot
current. No model is called; everything on the card is worked out here, in code, from the account's newest profile.

The rules (spec R1, "The rules the design holds to"):

- Only an account that owns the profile can share. The shared sample and an anonymous graph get a view with ``blocked`` and
  no switch; another account's profile is "not found", exactly as `require_profile_access` answers it.
- One card per account, drawn from its newest owned profile (analysing again makes a new profile).
- The areas, their order and scores are `Profile.domains`; a rim label is ``Domain.short`` (else the name), at most 14
  characters; past eight areas, the eight strongest stay, in the graph's order.
- A role's level per area is the mean ``target`` of its requirements whose skill sits in that area (by ``skillId``, then by
  the skill's name); where it names nothing, it is the person's own score, so the outline adds nothing there.
- The link is 12 random characters of ``[a-z0-9]``, new each time sharing is turned on; "Make a new link" replaces it and the
  old one stops at once. It is the only key: never a username or a profile id.
- The version changes only when what the card draws changes; ``updated_at`` (the date on the card) moves with it.
- The one line under the shape is the owner's choice and theirs alone: a sentence of their read (`offered_lines`) or their own
  words, at most `CARD_LINE_MAX` characters. It is the only prose the card carries, and it never comes from the evidence.
"""

from __future__ import annotations

import math
import re
import secrets
import string
from collections.abc import Callable, Sequence
from dataclasses import replace
from typing import Final
from urllib.parse import quote

from pydantic import ValidationError

from tayyari.card.content import (
    CardContent,
    DrawnArea,
    DrawnSkill,
    area_dict,
    content_of,
    skill_dict,
    version_of,
)
from tayyari.contract.card import (
    CARD_AREAS_MAX,
    CARD_LINE_MAX,
    CARD_SKILLS_OFFERED,
    SLUG_LEN,
    CardArea,
    CardBlocked,
    CardNewLink,
    CardRole,
    CardSettings,
    CardSkill,
    CardTheme,
    CardUpdate,
    CardView,
)
from tayyari.contract.models import Profile
from tayyari.core.clock import Clock
from tayyari.core.errors import AppError, BadRequest, NotFound
from tayyari.core.logging import get_logger
from tayyari.core.text import clip_words
from tayyari.storage.ports import CardRepo, ProfileRepo, StoredCardSnapshot, UserRepo

log = get_logger(__name__)

SLUG_ALPHABET: Final = string.ascii_lowercase + string.digits
SLUG_RE: Final = re.compile(rf"^[a-z0-9]{{{SLUG_LEN}}}$")
SHORT_MAX: Final = 14
DEFAULT_THEME: Final[CardTheme] = "light"

PROFILE_NOT_FOUND: Final = "Profile not found"
BLOCKED_SAMPLE: Final = "Sharing needs your own graph. This one is the sample everyone sees."
BLOCKED_ANONYMOUS: Final = "Sign in first, so the card is yours and you can turn it off later."
NO_GRAPH: Final = "Your account has no graph to draw yet. Analyse your resume first."
SKILL_NOT_OFFERED: Final = "One of those skills can't go on the card. Pick from the skills the sheet offers."
ROLE_NOT_FOUND: Final = "That role isn't on your graph. Open the graph again and pick it from there."
NOT_SHARED: Final = "Turn sharing on first; then there is a link to replace."


class CardProfileNotFound(NotFound):
    code = "profile_not_found"
    user_message = PROFILE_NOT_FOUND


class CardSharingBlocked(AppError):
    """The sample, or a graph no account owns: there is no card to share."""

    status = 403
    code = "card_blocked"
    user_message = BLOCKED_SAMPLE


class NoGraph(NotFound):
    code = "no_graph"
    user_message = NO_GRAPH


class SkillNotOffered(BadRequest):
    code = "skill_not_offered"
    user_message = SKILL_NOT_OFFERED


class CardRoleNotFound(BadRequest):
    code = "role_not_found"
    user_message = ROLE_NOT_FOUND


class NotShared(BadRequest):
    code = "not_shared"
    user_message = NOT_SHARED


def new_slug() -> str:
    """12 random characters of ``[a-z0-9]`` (36^12, about 4.7e18 links: walking them is not a way in)."""
    return "".join(secrets.choice(SLUG_ALPHABET) for _ in range(SLUG_LEN))


def _round(value: float) -> int:
    return math.floor(value + 0.5)


def _score(value: int) -> int:
    return max(0, min(100, int(value)))


def _short(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= SHORT_MAX else text[: SHORT_MAX - 1].rstrip() + "…"


LINE_MIN_CHARS: Final = 12


def offered_lines(profile: Profile) -> list[str]:
    """The lines of the read, each one the card may carry (none for a profile without a read)."""
    out: list[str] = []
    for line in profile.read or []:
        text = clip_words(" ".join(line.text.split()), CARD_LINE_MAX)
        if len(text) < LINE_MIN_CHARS or text in out:
            continue
        out.append(text)
    return out


def clean_line(value: str | None) -> str | None:
    """The owner's line as stored: one space between words, nothing at the ends, None for nothing."""
    if value is None:
        return None
    text = " ".join(value.split())
    return clip_words(text, CARD_LINE_MAX) or None


def offered_skills(profile: Profile) -> list[CardSkill]:
    """The skills a card may name: the strongest eight, ties in the graph's order."""
    ranked = sorted(enumerate(profile.skills), key=lambda pair: (-pair[1].score, pair[0]))[
        :CARD_SKILLS_OFFERED
    ]
    return [CardSkill(id=s.id, name=s.name, score=_score(s.score)) for _, s in ranked]


def drawn_areas(profile: Profile, role_id: str | None) -> list[tuple[str, DrawnArea]]:
    """The graph's areas as the card draws them (id and area), with the role's level per area when a role is named."""
    domains = list(enumerate(profile.domains))
    if len(domains) > CARD_AREAS_MAX:
        kept = sorted(domains, key=lambda pair: (-pair[1].score, pair[0]))[:CARD_AREAS_MAX]
        domains = sorted(kept, key=lambda pair: pair[0])
    role = next((r for r in profile.roles if r.id == role_id), None) if role_id else None
    targets: dict[str, list[int]] = {}
    if role is not None:
        by_id = {s.id: s.domainId for s in profile.skills}
        by_name = {s.name.strip().casefold(): s.domainId for s in profile.skills}
        for req in role.requirements:
            area = by_id.get(req.skillId) if req.skillId else None
            area = area or by_name.get(req.name.strip().casefold())
            if area is not None:
                targets.setdefault(area, []).append(_score(req.target))
    out: list[tuple[str, DrawnArea]] = []
    for _, d in domains:
        score = _score(d.score)
        level: int | None = None
        if role is not None:
            mine = targets.get(d.id)
            level = _round(sum(mine) / len(mine)) if mine else score
        out.append(
            (
                d.id,
                DrawnArea(
                    name=" ".join(d.name.split()),
                    short=_short(d.short or d.name),
                    score=score,
                    role_target=level,
                ),
            )
        )
    return out


class CardService:
    """The owner's card. ``public_url`` is the app's origin the links are made from (``settings.auth.public_url``)."""

    def __init__(
        self,
        *,
        profiles: ProfileRepo,
        users: UserRepo,
        cards: CardRepo,
        clock: Clock,
        public_url: str,
        slugs: Callable[[], str] = new_slug,
    ) -> None:
        self._profiles = profiles
        self._users = users
        self._cards = cards
        self._clock = clock
        self._public = public_url.rstrip("/")
        self._slugs = slugs

    # ───────────────────────── the owner's routes ─────────────────────────

    async def view(self, user_id: str | None, profile_id: str) -> CardView:
        """The share sheet: the card's settings, what it would draw, and its links while shared. The sample and an
        anonymous graph get ``blocked`` (and nothing is stored)."""
        blocked = await self._check(user_id, profile_id)
        if blocked is not None or user_id is None:
            profile = await self._load(profile_id)
            if profile is None:
                raise CardProfileNotFound()
            return self._view(profile, None, blocked=blocked or "anonymous")
        stored = await self.refresh(user_id)
        profile = await self._newest(user_id)
        return self._view(profile, stored)

    async def update(self, user_id: str | None, body: CardUpdate) -> CardView:
        """Save the settings and redraw. Skills must be among those offered and the role on the graph; turning sharing on
        makes a link, turning it off removes it."""
        blocked = await self._check(user_id, body.profileId)
        if blocked is not None or user_id is None:
            raise _blocked(blocked or "anonymous")
        profile = await self._newest(user_id)
        offered = {s.id for s in offered_skills(profile)}
        skills = list(dict.fromkeys(body.skills))
        if any(s not in offered for s in skills):
            raise SkillNotOffered()
        if body.roleId is not None and all(r.id != body.roleId for r in profile.roles):
            raise CardRoleNotFound()
        before = await self._cards.get(user_id)
        if not body.shared:
            slug = None
        elif before is not None and before.shared and before.slug is not None:
            slug = before.slug
        else:
            slug = self._slugs()
        stored = self._snapshot(
            user_id,
            profile,
            before,
            skills,
            body.roleId,
            body.theme,
            body.shared,
            slug,
            clean_line(body.line),
        )
        await self._cards.save(stored)
        if before is None or before.shared != body.shared:
            log.info("card_sharing", shared=body.shared)
        return self._view(profile, stored)

    async def new_link(self, user_id: str | None, body: CardNewLink) -> CardView:
        """A new slug for a shared card; the old link stops at once."""
        blocked = await self._check(user_id, body.profileId)
        if blocked is not None or user_id is None:
            raise _blocked(blocked or "anonymous")
        before = await self.refresh(user_id)
        if before is None or not before.shared:
            raise NotShared()
        stored = replace(before, slug=self._slugs())
        await self._cards.save(stored)
        log.info("card_new_link")
        return self._view(await self._newest(user_id), stored)

    # ───────────────────────── keeping it current ─────────────────────────

    async def refresh(self, user_id: str) -> StoredCardSnapshot | None:
        """Redraw the account's card from its newest profile, keeping its settings (a skill or role the new graph no longer
        has is dropped). Writes only when something changed; None when the account has no card yet or no profile."""
        before = await self._cards.get(user_id)
        if before is None:
            return None
        owned = await self._users.list_profiles(user_id, 1)
        if not owned:
            return before
        profile = await self._load(owned[0].id)
        if profile is None:
            return before
        offered = {s.id for s in offered_skills(profile)}
        skills = [str(s.get("id")) for s in before.skills if s.get("id") in offered]
        role_id = before.role_id if any(r.id == before.role_id for r in profile.roles) else None
        after = self._snapshot(
            user_id,
            profile,
            before,
            skills,
            role_id,
            _theme(before.theme),
            before.shared,
            before.slug,
            before.line,
        )
        if after == before:
            return before
        await self._cards.save(after)
        log.info(
            "card_refreshed",
            new_profile=after.profile_id != before.profile_id,
            redrawn=after.version != before.version,
        )
        return after

    async def refresh_all(self, *, page: int = 200) -> int:
        """Walk every card and redraw those whose account has a newer profile than the one drawn (the periodic step that
        keeps a card within a day of the graph). Returns how many moved to a newer profile."""
        moved = 0
        after: str | None = None
        while True:
            cards = await self._cards.list_page(after, page)
            if not cards:
                return moved
            for card in cards:
                owned = await self._users.list_profiles(card.user_id, 1)
                if owned and owned[0].id != card.profile_id:
                    redrawn = await self.refresh(card.user_id)
                    if redrawn is not None and redrawn.profile_id != card.profile_id:
                        moved += 1
            after = cards[-1].user_id

    # ───────────────────────── the public side (read by the routes) ─────────────────────────

    async def shared(self, slug: str) -> StoredCardSnapshot | None:
        """The shared card behind a link, or None for a slug of the wrong shape, an unknown one or one turned off."""
        if not SLUG_RE.fullmatch(slug):
            return None
        return await self._cards.get_by_slug(slug)

    def content(self, stored: StoredCardSnapshot) -> CardContent:
        return content_of(stored)

    # ───────────────────────── inside ─────────────────────────

    async def _check(self, user_id: str | None, profile_id: str) -> CardBlocked | None:
        """None when ``user_id`` owns the profile; the reason when there is nothing to share; `CardProfileNotFound` for an
        unknown profile or another account's (the two cannot be told apart)."""
        access = await self._profiles.get_access(profile_id)
        if access is None:
            raise CardProfileNotFound()
        if access.is_sample:
            return "sample"
        if access.user_id is None:
            return "anonymous"
        if access.user_id != user_id:
            raise CardProfileNotFound()
        return None

    async def _load(self, profile_id: str) -> Profile | None:
        doc = await self._profiles.get_profile(profile_id)
        if doc is None:
            return None
        try:
            return Profile.model_validate(doc)
        except ValidationError:
            log.warning("card_profile_unreadable")
            return None

    async def _newest(self, user_id: str) -> Profile:
        owned = await self._users.list_profiles(user_id, 1)
        profile = await self._load(owned[0].id) if owned else None
        if profile is None:
            raise NoGraph()
        return profile

    def _snapshot(
        self,
        user_id: str,
        profile: Profile,
        before: StoredCardSnapshot | None,
        skill_ids: Sequence[str],
        role_id: str | None,
        theme: CardTheme,
        shared: bool,
        slug: str | None,
        line: str | None = None,
    ) -> StoredCardSnapshot:
        areas = drawn_areas(profile, role_id)
        by_id = {s.id: s for s in profile.skills}
        skills = [
            (sid, DrawnSkill(name=" ".join(by_id[sid].name.split()), score=_score(by_id[sid].score)))
            for sid in skill_ids
        ]
        role = next((r for r in profile.roles if r.id == role_id), None) if role_id else None
        role_title = " ".join(role.title.split()) if role is not None else None
        version = version_of([a for _, a in areas], [s for _, s in skills], role_title, line)
        now = self._clock.now()
        changed = before is None or before.version != version
        return StoredCardSnapshot(
            user_id=user_id,
            profile_id=profile.id,
            slug=slug if shared else None,
            shared=shared,
            theme=theme,
            areas=[area_dict(aid, a) for aid, a in areas],
            skills=[skill_dict(sid, s) for sid, s in skills],
            role_id=role.id if role is not None else None,
            role_title=role_title,
            version=version,
            created_at=before.created_at if before is not None else now,
            updated_at=now if changed or before is None else before.updated_at,
            line=line,
        )

    def _view(
        self, profile: Profile, stored: StoredCardSnapshot | None, *, blocked: CardBlocked | None = None
    ) -> CardView:
        if stored is None:
            settings = CardSettings(shared=False, skills=[], roleId=None, theme=DEFAULT_THEME)
            areas = [_card_area(aid, a) for aid, a in drawn_areas(profile, None)]
        else:
            settings = CardSettings(
                shared=stored.shared,
                skills=[str(s.get("id")) for s in stored.skills],
                roleId=stored.role_id,
                theme=_theme(stored.theme),
                line=stored.line or None,
            )
            areas = [
                CardArea(
                    id=str(a.get("id")),
                    name=str(a.get("name")),
                    short=str(a.get("short")),
                    score=int(a.get("score") or 0),
                    roleTarget=None if a.get("roleTarget") is None else int(a["roleTarget"]),
                )
                for a in stored.areas
            ]
        view = CardView(
            settings=settings,
            areas=areas,
            skillsOffered=offered_skills(profile),
            rolesOffered=[CardRole(id=r.id, title=r.title) for r in profile.roles],
            linesOffered=offered_lines(profile),
            blocked=blocked,
        )
        if stored is None or not stored.shared or stored.slug is None:
            return view
        links = self.links(stored)
        return view.model_copy(
            update={**links, "slug": stored.slug, "updatedAt": stored.updated_at.isoformat()}
        )

    def links(self, stored: StoredCardSnapshot) -> dict[str, str]:
        """``pageUrl``, ``svgUrl``, ``ogUrl`` and the README ``markdown`` of a shared card."""
        slug = stored.slug
        if slug is None:
            raise ValueError("a card that is not shared has no links")
        page = f"{self._public}/u/{slug}"
        base = f"{self._public}/r/{slug}.svg"
        compare = f"compare={quote(stored.role_id, safe='')}" if stored.role_id else ""
        theme = _theme(stored.theme)
        if theme in ("dark", "transparent"):
            query = "?" + "&".join(part for part in (f"theme={theme}", compare) if part)
        else:
            query = f"?{compare}" if compare else ""
        svg = base + query
        if theme == "match":
            dark = base + "?theme=dark" + (f"&{compare}" if compare else "")
            markdown = (
                f'<a href="{page}">\n  <picture>\n    <source media="(prefers-color-scheme: dark)" srcset="{dark}">\n'
                f'    <img alt="Skill shape" src="{svg}">\n  </picture>\n</a>'
            )
        else:
            markdown = f"[![Skill shape]({svg})]({page})"
        return {
            "pageUrl": page,
            "svgUrl": svg,
            "ogUrl": f"{self._public}/og/{slug}-{stored.version}.png",
            "markdown": markdown,
        }


def _card_area(area_id: str, area: DrawnArea) -> CardArea:
    return CardArea(
        id=area_id, name=area.name, short=area.short, score=area.score, roleTarget=area.role_target
    )


def _theme(value: str) -> CardTheme:
    return value if value in ("light", "dark", "transparent", "match") else DEFAULT_THEME  # type: ignore[return-value]


def _blocked(reason: CardBlocked) -> CardSharingBlocked:
    return CardSharingBlocked(BLOCKED_ANONYMOUS if reason == "anonymous" else BLOCKED_SAMPLE)


__all__ = [
    "BLOCKED_ANONYMOUS",
    "BLOCKED_SAMPLE",
    "DEFAULT_THEME",
    "SLUG_ALPHABET",
    "SLUG_RE",
    "CardProfileNotFound",
    "CardRoleNotFound",
    "CardService",
    "CardSharingBlocked",
    "NoGraph",
    "NotShared",
    "SkillNotOffered",
    "clean_line",
    "drawn_areas",
    "new_slug",
    "offered_lines",
    "offered_skills",
]
