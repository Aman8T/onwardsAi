"""The LinkedIn picture as a PNG (1200 x 627): `svg.og_svg` drawn by resvg with the bundled Geist, nothing else.

resvg is told to skip the system's fonts, so the picture is the same on every server (FastAPI Cloud's image has no fonts to
speak of) and on a developer's machine; the three Geist weights the drawing uses sit in ``fonts/`` with their licence (SIL
OFL 1.1). Drawing one takes a few hundred milliseconds of CPU, so it runs in a worker thread and the result is kept in memory
by link and version: a version only changes when the shape does, so a cached picture is never stale.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from pathlib import Path
from typing import Final

import resvg_py

from tayyari.card.content import CardContent
from tayyari.card.svg import Notice, og_notice_svg, og_svg

FONTS: Final = Path(__file__).resolve().parent / "fonts"
FONT_FAMILY: Final = "Geist"


def render_png(svg: str) -> bytes:
    """``svg`` as PNG bytes, drawn with the bundled fonts only."""
    return bytes(
        resvg_py.svg_to_bytes(
            svg_string=svg,
            skip_system_fonts=True,
            font_dirs=[str(FONTS)],
            font_family=FONT_FAMILY,
            sans_serif_family=FONT_FAMILY,
        )
    )


class CardPictures:
    """LinkedIn pictures, drawn off the event loop and kept by ``(slug, version)`` (least recently used dropped first)."""

    def __init__(self, cache_size: int = 256) -> None:
        self._size = cache_size
        self._cache: OrderedDict[tuple[str, str], bytes] = OrderedDict()
        self._notices: dict[Notice, bytes] = {}

    async def picture(self, slug: str, version: str, content: CardContent) -> bytes:
        key = (slug, version)
        found = self._cache.get(key)
        if found is not None:
            self._cache.move_to_end(key)
            return found
        png = await asyncio.to_thread(render_png, og_svg(content))
        if self._size > 0:
            self._cache[key] = png
            while len(self._cache) > self._size:
                self._cache.popitem(last=False)
        return png

    async def notice(self, kind: Notice) -> bytes:
        found = self._notices.get(kind)
        if found is None:
            found = await asyncio.to_thread(render_png, og_notice_svg(kind))
            self._notices[kind] = found
        return found

    def __len__(self) -> int:
        return len(self._cache)


__all__ = ["FONTS", "CardPictures", "render_png"]
