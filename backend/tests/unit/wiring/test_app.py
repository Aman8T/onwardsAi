"""`create_app`: the factory itself, with no lifespan (nothing is built until the server starts)."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
from asgi_lifespan import LifespanManager
from fastapi import FastAPI
from uvicorn.importer import import_from_string

import tayyari
from tayyari.api import app as app_module
from tayyari.api.app import create_app
from tayyari.container import ContainerOverrides
from tayyari.core.settings import Settings
from tests.unit.wiring.support import BASE_URL, FakeAnalysis, FakeChat

MakeSettings = Callable[..., Settings]


def test_the_factory_signature_is_the_agreed_one() -> None:
    parameters = inspect.signature(create_app).parameters
    assert list(parameters) == ["settings", "overrides"]
    assert parameters["settings"].default is None
    assert parameters["overrides"].kind is inspect.Parameter.KEYWORD_ONLY
    assert parameters["overrides"].default is None


def test_uvicorn_can_load_the_factory_by_name() -> None:
    assert import_from_string("tayyari.api.app:create_app") is create_app


def test_create_app_is_cheap_and_builds_nothing(make_settings: MakeSettings) -> None:
    settings = make_settings()
    app = create_app(settings, overrides=ContainerOverrides(analysis=FakeAnalysis(), chat=FakeChat()))
    assert isinstance(app, FastAPI)
    assert app.state.settings is settings  # set at once, so request code works before the lifespan runs
    assert not hasattr(app.state, "container")
    assert not settings.data_dir.exists()  # no database file yet


def test_the_app_is_named_and_has_no_docs_pages(make_settings: MakeSettings) -> None:
    app = create_app(make_settings())
    assert app.title == "Tayyari API"
    assert app.version == tayyari.__version__
    assert app.docs_url is None
    assert app.redoc_url is None
    assert app.openapi_url is None


def test_settings_default_to_the_process_wide_ones(
    make_settings: MakeSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = make_settings()
    monkeypatch.setattr(app_module, "get_settings", lambda: settings)
    assert create_app().state.settings is settings


def test_the_web_app_routes_come_last(make_settings: MakeSettings) -> None:
    """mount_spa's catch-all routes would shadow anything registered after them."""
    app = create_app(make_settings())
    paths = [getattr(route, "path", None) for route in app.router.routes]
    assert paths[-1] == "/{path:path}"
    assert paths[-2] == "/api/{_path:path}"
    assert paths[-3] == "/api"


async def test_the_web_app_is_served_from_the_static_folder(
    make_settings: MakeSettings, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    static = tmp_path / "static"
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_text("<!doctype html><title>Tayyari</title>", encoding="utf-8")
    (static / "assets" / "app.js").write_text("console.log('hi')", encoding="utf-8")
    monkeypatch.setattr(app_module, "STATIC_DIR", static)
    app = create_app(make_settings(), overrides=ContainerOverrides(analysis=FakeAnalysis(), chat=FakeChat()))
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url=BASE_URL) as client:
            index = await client.get("/")
            deep_link = await client.get("/profile/abc")
            asset = await client.get("/assets/app.js")
            health = await client.get("/api/health")
    assert "<title>Tayyari</title>" in index.text
    assert index.headers["cache-control"] == "no-cache"
    assert deep_link.text == index.text  # client-side routes fall back to index.html
    assert asset.text == "console.log('hi')"
    assert asset.headers["cache-control"] == "public, max-age=3600"
    assert health.json()["ok"] is True  # the API routes are not shadowed by the web app
