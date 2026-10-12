"""The posting match in the composition root and the app: the container builds the service on the guarded Claude client
with the models and timeout from the settings and the stored postings and extracted facts behind it, what is injected
is used as given, and the match routes are registered ahead of the web app's catch-all."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from tayyari.api.app import create_app
from tayyari.container import Container, ContainerOverrides
from tayyari.core.settings import MatchSettings, Settings
from tayyari.llm.guarded import GuardedClaudeClient
from tayyari.match.service import MatchService
from tayyari.storage.repos.match import SqlPostingRepo, SqlProfileFactsRepo
from tests.unit.wiring.support import FakeAnalysis, FakeChat

MakeSettings = Callable[..., Settings]


async def build(settings: Settings, **overrides: Any) -> Container:
    values: dict[str, Any] = {"analysis": FakeAnalysis(), "chat": FakeChat(), **overrides}
    return await Container.build(settings, overrides=ContainerOverrides(**values))


async def test_the_service_is_built_on_the_guarded_client_and_the_stored_caches(
    make_settings: MakeSettings,
) -> None:
    container = await build(make_settings(reader_model="claude-haiku-5-5"))
    try:
        service = container.match
        assert isinstance(service, MatchService)
        assert service._claude is container.claude and isinstance(service._claude, GuardedClaudeClient)
        assert service._postings is container.repos.postings and isinstance(service._postings, SqlPostingRepo)
        assert service._facts_cache is container.repos.profile_facts
        assert isinstance(service._facts_cache, SqlProfileFactsRepo)
        # nothing configured: the reader model reads, and the same one writes
        assert (service._model, service._writer) == ("claude-haiku-5-5", "claude-haiku-5-5")
        assert service._timeout == 90.0
    finally:
        await container.aclose()


async def test_the_models_and_the_timeout_come_from_the_match_settings(make_settings: MakeSettings) -> None:
    settings = make_settings(
        reader_model="claude-haiku-4-5",
        match=MatchSettings(model="claude-haiku-5-5", writer_model="claude-sonnet-5-5", call_timeout_s=30),
    )
    container = await build(settings)
    try:
        service = container.match
        assert (service._model, service._writer, service._timeout) == (
            "claude-haiku-5-5",
            "claude-sonnet-5-5",
            30.0,
        )
    finally:
        await container.aclose()


async def test_an_injected_service_is_used_as_given(make_settings: MakeSettings) -> None:
    first = await build(make_settings())
    try:
        own = await build(make_settings(), match=first.match)
        try:
            assert own.match is first.match
        finally:
            await own.aclose()
    finally:
        await first.aclose()


async def test_the_match_routes_are_registered_before_the_web_apps_catch_all(
    make_settings: MakeSettings,
) -> None:
    app = create_app(make_settings())

    def flatten(routes: Any) -> list[tuple[str, tuple[str, ...]]]:
        out: list[tuple[str, tuple[str, ...]]] = []
        for route in routes:
            inner = getattr(getattr(route, "original_router", None), "routes", None)
            if inner is not None:
                out.extend(flatten(inner))
            else:
                out.append((route.path, tuple(sorted(route.methods or ()))))
        return out

    ordered = flatten(app.router.routes)
    expected = [
        ("/api/match", ("POST",)),
        ("/api/matches", ("GET",)),
        ("/api/match/{match_id}", ("GET",)),
        ("/api/match/{match_id}/answers", ("POST",)),
        ("/api/match/{match_id}/resume", ("POST",)),
        ("/api/match/{match_id}/resume", ("GET",)),
        ("/api/match/{match_id}/resume", ("PATCH",)),
        ("/api/match/{match_id}/resume.pdf", ("GET",)),
        ("/api/match/{match_id}/resume.docx", ("GET",)),
        ("/api/match/{match_id}/cover", ("POST",)),
        ("/api/match/{match_id}/cover", ("GET",)),
        ("/api/match/{match_id}/cover.pdf", ("GET",)),
    ]
    catch_all = ordered.index(("/{path:path}", ("GET", "HEAD")))
    api_catch_all = ordered.index(
        ("/api/{_path:path}", ("DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT"))
    )
    for entry in expected:
        assert entry in ordered, entry
        assert ordered.index(entry) < min(
            catch_all, api_catch_all
        )  # or the 404 / SPA fallback would answer first
