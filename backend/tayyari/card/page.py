"""The page a shared card links to (``/u/{slug}``): the card, its areas as a table, how it was made, and two ways out.

A string template, rendered on the server with every value escaped; no script at all (its own CSP says ``script-src 'none'``).
It links the content pages' stylesheet (``/site/site.css``, built by ``scripts/build-pages.tsx``) and the same Google Fonts the
site loads. The share tags make LinkedIn and others show the large picture: ``og:title`` "Strongest in {area}", ``og:image``
the versioned PNG, ``twitter:card`` ``summary_large_image``. The page is ``noindex`` for now (spec R1: indexing comes later).

Never on it: the person's name, email, username or profile id, evidence, a rank, a percentile or a match percentage. The one
line of prose is the owner's own choice (a sentence of their read, or their own words), and it is also the link preview's text.
"""

from __future__ import annotations

from typing import Final
from urllib.parse import quote
from xml.sax.saxutils import escape

from tayyari.card.content import CardContent
from tayyari.card.text import clean
from tayyari.storage.ports import StoredCardSnapshot

FONTS_HREF: Final = "https://fonts.googleapis.com/css2?family=Geist:wght@400;500;600&family=Instrument+Serif:ital@0;1&display=swap"
PAGE_CSP: Final = (
    "default-src 'none'; "
    "style-src 'self' https://fonts.googleapis.com; "
    "font-src https://fonts.gstatic.com; "
    "img-src 'self' data:; "
    "script-src 'none'; "
    "base-uri 'none'; "
    "form-action 'none'; "
    "frame-ancestors 'none'"
)
"""The page's own policy: no script, its own images and stylesheet, the fonts. The middleware keeps a route's header."""
ICON_LINKS: Final = (
    '<link rel="icon" href="/favicon.svg" type="image/svg+xml" />',
    '<link rel="icon" href="/favicon.ico" sizes="32x32" />',
    '<link rel="apple-touch-icon" href="/apple-touch-icon.png" />',
)
"""The ring-and-dot mark as files the web build emits at the site root (the app's index and content pages link the same ones)."""
THEME_COLORS: Final = (("#e1ebf5", "light"), ("#0b1015", "dark"))
"""The quiet look's page background (``--bg`` in ``src/theme/tokens.css``), for the browser's own chrome."""
SITE_NAME: Final = "Leafout"


def esc(text: str) -> str:
    """Text for an HTML text node or a double-quoted attribute."""
    return escape(clean(text), {'"': "&quot;", "'": "&#x27;"})


def headline(content: CardContent) -> str:
    top = content.strongest()
    return f"Strongest in {top.name}" if top is not None else "Skill shape"


def _head(title: str, description: str, extra: list[str]) -> str:
    lines = [
        '<meta charset="UTF-8" />',
        '<meta name="viewport" content="width=device-width, initial-scale=1.0" />',
        '<meta name="color-scheme" content="light dark" />',
        f"<title>{esc(title)}</title>",
        f'<meta name="description" content="{esc(description)}" />',
        '<meta name="robots" content="noindex" />',
        *extra,
        *ICON_LINKS,
        *(
            f'<meta name="theme-color" content="{color}" media="(prefers-color-scheme: {scheme})" />'
            for color, scheme in THEME_COLORS
        ),
        '<link rel="preconnect" href="https://fonts.googleapis.com" />',
        '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />',
        f'<link href="{FONTS_HREF}" rel="stylesheet" />',
        '<link rel="stylesheet" href="/site/site.css" />',
    ]
    return "\n".join(f"    {line}" for line in lines)


def _document(head: str, main: str) -> str:
    brand = (
        '<a class="site-brand" href="/"><svg viewBox="0 0 20 20" width="20" height="20" aria-hidden="true" focusable="false">'
        '<circle class="mark-ring" cx="10" cy="10" r="7.5"></circle><circle class="mark-dot" cx="10" cy="10" r="1.25"></circle>'
        f'</svg><span class="serif site-brand-word">{SITE_NAME}</span></a>'
    )
    body = (
        '<div class="site"><a class="skip" href="#main">Skip to content</a>'
        f'<header class="site-header">{brand}</header>'
        f'<main id="main" class="site-main" tabindex="-1"><div class="column">{main}</div></main>'
        "</div>"
    )
    return f'<!doctype html>\n<html lang="en">\n  <head>\n{head}\n  </head>\n  <body>\n    {body}\n  </body>\n</html>\n'


def card_page(stored: StoredCardSnapshot, content: CardContent, *, public_url: str, report_email: str) -> str:
    """The page of a shared card. ``stored`` must be shared (its slug is the page's address)."""
    slug = stored.slug or ""
    base = public_url.rstrip("/")
    page_url = f"{base}/u/{slug}"
    og_url = f"{base}/og/{slug}-{stored.version}.png"
    compare = f"compare={quote(stored.role_id, safe='')}" if stored.role_id and content.role_title else ""
    light = f"/r/{slug}.svg" + (f"?{compare}" if compare else "")
    dark = f"/r/{slug}.svg?theme=dark" + (f"&{compare}" if compare else "")
    title = headline(content)
    # the owner's own line, when they put one on the card, is what a link preview quotes
    description = content.line or "A skill shape from Leafout. Every score rests on cited evidence."
    n = len(content.areas)
    alt = f"Skill shape across {n} areas. {title}." if n else "Skill shape."
    share = [
        f'<meta property="og:title" content="{esc(title)}" />',
        f'<meta property="og:description" content="{esc(description)}" />',
        '<meta property="og:type" content="website" />',
        f'<meta property="og:url" content="{esc(page_url)}" />',
        f'<meta property="og:image" content="{esc(og_url)}" />',
        '<meta property="og:image:width" content="1200" />',
        '<meta property="og:image:height" content="627" />',
        f'<meta property="og:image:alt" content="{esc(alt)}" />',
        f'<meta property="og:site_name" content="{SITE_NAME}" />',
        '<meta name="twitter:card" content="summary_large_image" />',
        f'<meta name="twitter:title" content="{esc(title)}" />',
        f'<meta name="twitter:description" content="{esc(description)}" />',
        f'<meta name="twitter:image" content="{esc(og_url)}" />',
    ]
    role = content.role_title
    head_row = (
        '<tr><th scope="col">Area</th><th scope="col">Score</th>'
        + (f'<th scope="col">{esc(role)} expects</th>' if role else "")
        + "</tr>"
    )
    rows = "".join(
        f'<tr><th scope="row">{esc(a.name)}</th><td class="numeral">{a.score}</td>'
        + (f'<td class="numeral">{a.score if a.role_target is None else a.role_target}</td>' if role else "")
        + "</tr>"
        for a in content.areas
    )
    skills = ""
    if content.skills:
        chips = "".join(
            f'<li class="chip">{esc(s.name)} <span class="numeral">{s.score}</span></li>'
            for s in content.skills
        )
        skills = f'<h2>Named skills</h2><ul class="shape-skills">{chips}</ul>'
    updated = f"{content.updated.day} {content.updated:%B %Y}"
    report = f"mailto:{quote(report_email, safe='@')}?subject={quote(f'Report card {slug}')}"
    main = (
        '<article><header class="doc-head">'
        '<p class="kicker">Skill shape</p>'
        f'<h1 class="serif">{esc(title)}</h1>'
        + (f'<p class="lede serif">“{esc(content.line)}”</p>' if content.line else "")
        + f'<p class="lede">Every score on this card rests on cited evidence. Updated {esc(updated)}.</p>'
        "</header>"
        '<figure class="shape-card"><picture>'
        f'<source media="(prefers-color-scheme: dark)" srcset="{esc(dark)}" />'
        f'<img src="{esc(light)}" width="480" height="330" alt="{esc(alt)}" />'
        "</picture></figure>"
        '<div class="prose">'
        f'<h2>The areas</h2><table class="shape-table"><thead>{head_row}</thead><tbody>{rows}</tbody></table>'
        f"{skills}"
        "<h2>How this was made</h2>"
        "<p>Leafout read this person's resume and public links and built a skill graph in which every skill cites the line it "
        "came from. Each area's score is worked out in code from those skills, not written by a model, and the evidence itself "
        "stays private: this card shows only the shape. It is not a ranking against other people.</p>"
        + (
            f"<p>The dashed outline is what {esc(role)} expects in each area, from that role's requirements on the same graph.</p>"
            if role
            else ""
        )
        + "</div>"
        '<p class="doc-cta">'
        '<a class="btn btn-primary" href="/">See your own skill shape</a> '
        f'<a class="btn btn-quiet" href="{esc(report)}">Report this card</a>'
        "</p></article>"
    )
    return _document(_head(f"{title} · Skill shape · {SITE_NAME}", description, share), main)


def missing_page() -> str:
    """The plain 404 page for an unknown, stopped or malformed link: it says nothing about whose card it was."""
    main = (
        '<article><header class="doc-head"><p class="kicker">Skill shape</p>'
        '<h1 class="serif">This card isn\'t shared any more.</h1>'
        '<p class="lede">The link may have been replaced, or sharing was turned off.</p>'
        '<p class="doc-cta"><a class="btn btn-primary" href="/">See your own skill shape</a></p>'
        "</header></article>"
    )
    return _document(_head(f"Card not found · {SITE_NAME}", "This card isn't shared any more.", []), main)


__all__ = ["PAGE_CSP", "card_page", "esc", "headline", "missing_page"]
