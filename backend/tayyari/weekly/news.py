"""The news section: web searches in code's hands, a model that only fills typed slots, and a verifier that keeps what holds.

    1. topics     in code: the reader's two strongest skill areas, then their goal (else the closest role of the graph, else the
                  current role). The searcher sees a topic and nothing else about the person.
    2. searches   up to ``news_searches`` single-search Haiku calls at once (purpose ``weekly.news``, low effort), each restricted
                  to the allow-list of news sites in the settings. Only the result links the search tool returned are used,
                  never the model's own words.
    3. read       ONE structured Haiku call (purpose ``weekly.news.read``, no thinking) puts the results into slots: url,
                  headline, publication, date, skill, why. The results are untrusted data; a result whose title carries text
                  aimed at an AI is not shown to it at all (logged as an anomaly).
    4. verify     `ground.check_news` drops any item whose url is not among the results or whose skill is not on the graph.

Any failure at any step (an error, a refusal, the spend ceiling) is a quiet section, never a failed edition.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import date
from typing import Any, Final

from tayyari.contract.models import Profile
from tayyari.core.logging import get_logger
from tayyari.core.settings import BLOCKED_NEWS_DOMAINS, WeeklySettings
from tayyari.core.text import clip_words, tidy
from tayyari.jobsearch.judge import has_injection
from tayyari.llm.claude import ClaudeClient
from tayyari.llm.types import CallContext, TextBlock
from tayyari.prompts.weekly import (
    NewsResult,
    build_news_read_prompt,
    build_news_search_prompt,
    news_read_system,
    news_search_system,
)

from .ground import NewsCheck, SearchedPage, check_news, host_of, normalise_url, on_allow_list

log = get_logger(__name__)


def readable_domains(domains: Sequence[str]) -> list[str]:
    """``domains`` without the sites Anthropic's search crawler cannot read (`BLOCKED_NEWS_DOMAINS`, and their
    subdomains): naming one in ``allowed_domains`` makes the API refuse the whole search, so one bad line in
    ``TAYYARI_WEEKLY__NEWS_DOMAINS`` would silently end every edition's news. What is left out is logged."""
    kept = [d for d in domains if not on_allow_list(d.removeprefix("www."), BLOCKED_NEWS_DOMAINS)]
    if len(kept) < len(domains):
        log.warning("weekly_news_domains_unreadable", left_out=[d for d in domains if d not in kept])
    return kept


SEARCH_MAX_TOKENS: Final = 800
READ_MAX_TOKENS: Final = 1600
MAX_RESULTS: Final = 24
"""Results shown to the reading call (a search returns about five each)."""
MAX_SKILLS_SHOWN: Final = 20
TOPIC_CHARS: Final = 90
NEWS_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["items"],
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["url", "headline", "publication", "date", "skill", "why"],
                "properties": {
                    "url": {"type": "string"},
                    "headline": {"type": "string"},
                    "publication": {"type": "string"},
                    "date": {"type": "string"},
                    "skill": {"type": "string"},
                    "why": {"type": "string"},
                },
            },
        }
    },
}
"""The citation (the article's address) first and the skill it bears on before the sentence that says why."""


@dataclass(slots=True)
class NewsOutcome:
    items: list[Any] = field(default_factory=list)
    searches: int = 0
    pages: int = 0
    dropped: dict[str, int] = field(default_factory=dict)


def topics_for(profile: Profile, closest_role: str | None, limit: int) -> list[str]:
    """What to search for, in order: the two strongest skill areas, then the goal (else the closest role, else the role)."""
    areas = [tidy(d.name) for d in sorted(profile.domains, key=lambda d: -d.score) if tidy(d.name)][:2]
    last = tidy(profile.goal) or tidy(closest_role) or tidy(profile.person.currentRole)
    out = [*areas, clip_words(last, TOPIC_CHARS)] if last else areas
    return list(dict.fromkeys(topic for topic in out if topic))[:limit]


def skills_of(profile: Profile) -> list[str]:
    return [tidy(s.name) for s in profile.skills if tidy(s.name)]


class NewsDesk:
    def __init__(self, claude: ClaudeClient, settings: WeeklySettings) -> None:
        self._claude = claude
        self._settings = settings

    async def gather(
        self, profile: Profile, closest_role: str | None, ctx: CallContext, today: date
    ) -> NewsOutcome:
        """The verified news items for this reader; an empty outcome on any trouble. Never raises (but cancellation)."""
        s = self._settings
        outcome = NewsOutcome()
        topics = topics_for(profile, closest_role, s.news_searches)
        if not s.news or not topics:
            return outcome
        domains = readable_domains(s.news_domains)
        if not domains:
            return outcome
        try:
            pages = await self._search(topics, domains, ctx)
            outcome.searches = len(topics)
            outcome.pages = len(pages)
            if not pages:
                return outcome
            raw = await self._read(profile, pages, ctx)
        except Exception as exc:
            log.warning("weekly_news_failed", kind=type(exc).__name__)
            return outcome
        checked: NewsCheck = check_news(
            raw,
            pages=pages,
            skills=skills_of(profile),
            domains=domains,
            limit=s.news_items,
            today=today,
        )
        outcome.items = checked.items
        outcome.dropped = checked.dropped
        if checked.dropped:
            log.info("weekly_news_dropped", **checked.dropped)
        return outcome

    async def _search(self, topics: list[str], domains: list[str], ctx: CallContext) -> list[SearchedPage]:
        s = self._settings

        async def one(topic: str) -> list[SearchedPage]:
            try:
                run = await self._claude.search_web(
                    model=s.model,
                    system=[TextBlock(news_search_system())],
                    user=[TextBlock(build_news_search_prompt(topic))],
                    max_searches=1,
                    allowed_domains=domains,
                    max_tokens=SEARCH_MAX_TOKENS,
                    effort="low",
                    ctx=replace(ctx, purpose="weekly.news"),
                )
            except Exception as exc:
                log.warning("weekly_news_search_failed", kind=type(exc).__name__)
                return []
            return [
                SearchedPage(url=hit.url, title=tidy(hit.title), age=hit.page_age)
                for call in run.searches
                if call.error is None
                for hit in call.hits
            ]

        found = await asyncio.gather(*(one(topic) for topic in topics))
        pages: list[SearchedPage] = []
        seen: set[str] = set()
        for page in (page for group in found for page in group):
            key = normalise_url(page.url)
            if key is None or key in seen or not page.title:
                continue
            seen.add(key)
            if not on_allow_list(host_of(page.url), domains):
                continue
            if has_injection(page.title):
                log.warning("weekly_anomaly", where="news_result_title")
                continue
            pages.append(page)
        return pages[:MAX_RESULTS]

    async def _read(self, profile: Profile, pages: list[SearchedPage], ctx: CallContext) -> object:
        s = self._settings
        results = [
            NewsResult(id=f"H{i + 1}", title=page.title, url=page.url, age=page.age)
            for i, page in enumerate(pages)
        ]
        shown = [s_.name for s_ in sorted(profile.skills, key=lambda k: -k.score)][:MAX_SKILLS_SHOWN]
        prompt = build_news_read_prompt(skills=shown, goal=tidy(profile.goal) or None, results=results)
        answer = await self._claude.structured(
            model=s.model,
            system=[TextBlock(news_read_system(s.news_items))],
            user=[TextBlock(prompt)],
            schema=NEWS_SCHEMA,
            max_tokens=READ_MAX_TOKENS,
            effort="low",
            ctx=replace(ctx, purpose="weekly.news.read"),
        )
        return answer.data.get("items")


__all__ = ["NEWS_SCHEMA", "NewsDesk", "NewsOutcome", "readable_domains", "skills_of", "topics_for"]
