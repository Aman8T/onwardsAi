"""The page a shared report lives on (``/next/{slug}``): the question, the roles the person can credibly move to with the
lines behind them, their shape, and two ways out.

A string template, rendered on the server with every value escaped; no script (its CSP says ``script-src 'none'``) and no
inline style (it uses the content pages' stylesheet, ``/site/site.css``, which carries the app's tokens and a few report
rules). The shape is a ``data:`` image in a light and a dark copy. The share tags make link previews show the large picture.
``noindex``.

What the page says is only what `ReportContent` holds, which is already redacted by the owner's choices. Its own words never
score anyone (no percentage, rank or percentile) and never say why the person is looking.
"""

from __future__ import annotations

from typing import Final
from urllib.parse import quote

from tayyari.card.page import FONTS_HREF, ICON_LINKS, PAGE_CSP, SITE_NAME, THEME_COLORS, esc
from tayyari.contract.report import ReportContent, ReportRole
from tayyari.report.svg import data_uri, headline, shape_svg
from tayyari.storage.ports import StoredReportSnapshot

DESCRIPTION: Final = (
    "Roles this person can credibly move to, each backed by lines of their own resume. Made with Leafout."
)
CTA: Final = "Where could you go next?"


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
        f'<main id="main" class="site-main" tabindex="-1"><div class="column next-column">{main}</div></main>'
        "</div>"
    )
    return f'<!doctype html>\n<html lang="en">\n  <head>\n{head}\n  </head>\n  <body>\n    {body}\n  </body>\n</html>\n'


def title_of(content: ReportContent) -> str:
    """The h1: how many roles, in words, with the name only while the owner shows it."""
    who = content.person.name or "This person"
    n = len(content.roles)
    if n == 0:
        return f"{who}'s resume doesn't back a move yet."
    if n == 1:
        return f"{who}'s resume backs one role today."
    words = {2: "two", 3: "three"}
    return f"{who} can credibly move to {words.get(n, str(n))} roles."


def _pips(role: ReportRole) -> str:
    dots = "".join(
        f'<span class="next-pip{"" if i < role.have else " is-open"}" aria-hidden="true"></span>'
        for i in range(role.total)
    )
    return f'<p class="next-count">{dots}<span class="next-count-words">{role.have} of {role.total} asks shown</span></p>'


def _role(role: ReportRole, offer_url: str | None) -> str:
    when = (
        '<span class="next-stamp">Ready now</span>'
        if role.ready
        else (f'<span class="next-eta">{esc(role.eta)}</span>' if role.eta else "")
    )
    quotes = ""
    if role.quotes:
        items = "".join(
            f'<blockquote class="next-quote"><p>“{esc(q.text)}”</p><cite>{esc(q.source)}</cite></blockquote>'
            for q in role.quotes
        )
        quotes = f'<div class="next-why"><p class="next-label">Why them</p>{items}</div>'
    missing = ""
    if role.missing:
        missing = (
            '<div class="next-gap"><p class="next-label">Not shown yet</p>'
            f"<p>{' · '.join(esc(m) for m in role.missing)}</p></div>"
        )
    offer = ""
    if offer_url:
        href = f"{offer_url}{'&' if '?' in offer_url else '?'}role={quote(role.title, safe='')}"
        offer = (
            f'<p class="next-offer"><a href="{esc(href)}" rel="noopener">Get Day One for this role</a></p>'
        )
    return (
        f'<article class="panel next-role"><header class="next-role-head"><h2>{esc(role.title)}</h2>{when}</header>'
        f"{_pips(role)}{quotes}{missing}{offer}</article>"
    )


def report_page(
    stored: StoredReportSnapshot,
    content: ReportContent,
    *,
    public_url: str,
    report_email: str,
    offer_url: str | None,
) -> str:
    """The page of a shared report. ``stored`` must be shared (its slug is the page's address)."""
    slug = stored.slug or ""
    base = public_url.rstrip("/")
    page_url = f"{base}/next/{slug}"
    og_url = f"{base}/og/next/{slug}-{stored.version}.png"
    question = headline(content)
    roles_line = ", ".join(r.title for r in content.roles)
    description = f"{roles_line}. Each backed by lines of their own resume." if roles_line else DESCRIPTION
    alt = f"{question} {roles_line}".strip()
    share = [
        f'<meta property="og:title" content="{esc(question)}" />',
        f'<meta property="og:description" content="{esc(description)}" />',
        '<meta property="og:type" content="website" />',
        f'<meta property="og:url" content="{esc(page_url)}" />',
        f'<meta property="og:image" content="{esc(og_url)}" />',
        '<meta property="og:image:width" content="1200" />',
        '<meta property="og:image:height" content="630" />',
        f'<meta property="og:image:alt" content="{esc(alt)}" />',
        f'<meta property="og:site_name" content="{SITE_NAME}" />',
        '<meta name="twitter:card" content="summary_large_image" />',
        f'<meta name="twitter:title" content="{esc(question)}" />',
        f'<meta name="twitter:description" content="{esc(description)}" />',
        f'<meta name="twitter:image" content="{esc(og_url)}" />',
    ]
    person = content.person
    lede = esc(person.line) if person.line else ""
    open_to_work = '<p class="next-open">Open to work</p>' if content.openToWork else ""
    shape = ""
    if len(content.areas) >= 3:
        light, dark = shape_svg(content.areas, "light"), shape_svg(content.areas, "dark")
        alt_shape = "Skill shape across " + ", ".join(a.name for a in content.areas)
        shape = (
            '<figure class="shape-card next-shape"><picture>'
            f'<source media="(prefers-color-scheme: dark)" srcset="{data_uri(dark)}" />'
            f'<img src="{data_uri(light)}" width="420" height="340" alt="{esc(alt_shape)}" />'
            "</picture><figcaption>Their shape, drawn from cited evidence.</figcaption></figure>"
        )
    roles = "".join(_role(r, offer_url) for r in content.roles)
    roles_block = f'<section class="next-roles" aria-label="Roles">{roles}</section>' if roles else ""
    near = ""
    if content.nearMisses:
        items = "".join(
            f"<li><strong>{esc(n.title)}</strong>"
            + (f"<span>Missing: {' · '.join(esc(m) for m in n.missing)}</span>" if n.missing else "")
            + "</li>"
            for n in content.nearMisses
        )
        near = f'<section class="next-near"><h2>Close, not yet</h2><ul>{items}</ul></section>'
    report = f"mailto:{quote(report_email, safe='@')}?subject={quote(f'Report page {slug}')}"
    main = (
        '<article class="next-report"><header class="doc-head">'
        '<p class="kicker">Where can I go next · beta</p>'
        f'<h1 class="serif">{esc(title_of(content))}</h1>'
        + (f'<p class="lede">{lede}</p>' if lede else "")
        + open_to_work
        + "</header>"
        + shape
        + roles_block
        + near
        + '<section class="callout panel next-cta">'
        f"<h2>{CTA}</h2>"
        "<p>Upload your resume and get your own report in about a minute. Free with a Leafout account. Nothing is added "
        "that your resume doesn't show.</p>"
        '<p class="doc-cta"><a class="btn btn-primary" href="/">Make my report</a></p>'
        "</section>"
        '<p class="next-foot">Every line on this page quotes the person\'s own resume. Leafout never adds a skill they '
        "haven't shown, and never scores them against other people. "
        f'<a href="{esc(report)}">Report this page</a></p>'
        "</article>"
    )
    return _document(_head(f"{question} · {SITE_NAME}", description, share), main)


def missing_page() -> str:
    """The plain 404 page for an unknown, stopped or malformed link: it says nothing about whose report it was."""
    main = (
        '<article><header class="doc-head"><p class="kicker">Where can I go next</p>'
        '<h1 class="serif">This report isn\'t shared any more.</h1>'
        '<p class="lede">The link may have been replaced, or sharing was turned off.</p>'
        f'<p class="doc-cta"><a class="btn btn-primary" href="/">{CTA}</a></p>'
        "</header></article>"
    )
    return _document(_head(f"Report not found · {SITE_NAME}", "This report isn't shared any more.", []), main)


__all__ = ["PAGE_CSP", "missing_page", "report_page", "title_of"]
