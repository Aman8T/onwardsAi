"""The report's link preview as a PNG (1200 x 630): `svg.og_svg` drawn by the radar card's resvg renderer with the bundled
Geist, off the event loop, kept in memory by link and version (a version changes only when the public page does)."""

from __future__ import annotations

import asyncio
from collections import OrderedDict

from tayyari.card.png import render_png
from tayyari.contract.report import ReportContent
from tayyari.report.svg import og_notice_svg, og_svg


class ReportPictures:
    """Link previews, drawn in a worker thread and kept by ``(slug, version)`` (least recently used dropped first)."""

    def __init__(self, cache_size: int = 256) -> None:
        self._size = cache_size
        self._cache: OrderedDict[tuple[str, str], bytes] = OrderedDict()
        self._notice: bytes | None = None

    async def picture(self, slug: str, version: str, content: ReportContent) -> bytes:
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

    async def notice(self) -> bytes:
        if self._notice is None:
            self._notice = await asyncio.to_thread(render_png, og_notice_svg())
        return self._notice

    def __len__(self) -> int:
        return len(self._cache)


__all__ = ["ReportPictures"]
