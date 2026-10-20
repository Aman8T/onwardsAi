"""The email of The Leafout Weekly, and the small page the unsubscribe link opens.

The email is an old-style newspaper in a table: one 600 px column, inline styles only (mail clients drop the rest), Georgia and
Times as fallbacks, no images (the nameplate is set in type), a plain-text twin. Every line of it comes from the stored
`WeeklyEdition`; nothing is added here, and every value is escaped. Every opening links to the employer's own board, the whole
paper to the reader's edition in the app (``<app_url>/weekly?e=<id>``), and the footer carries Manage and a one-click
unsubscribe (the same address as the ``List-Unsubscribe`` header, `list_unsubscribe_headers`).
"""

from __future__ import annotations

import html
from dataclasses import dataclass
from datetime import date
from typing import Final
from urllib.parse import quote

from tayyari.contract.weekly import WeeklyEdition, WeeklyFit, WeeklyJob, WeeklyNews, WeeklyProgress

BRAND: Final = "The Leafout Weekly"
PAPER: Final = "#f6f1e4"
SHEET: Final = "#fbf8ef"
INK: Final = "#1c1a17"
MUTED: Final = "#6b645a"
RULE: Final = "#1c1a17"
FAINT: Final = "#cfc7b4"
LINK: Final = "#7a1f1f"
SERIF: Final = "Georgia, 'Times New Roman', Times, serif"
NAMEPLATE: Final = "'Old English Text MT', 'Goudy Text MT', 'Times New Roman', Georgia, serif"

FIT_WORDS: Final[dict[WeeklyFit, str]] = {
    "looks_close": "Looks close",
    "worth_a_look": "Worth a look",
    "stretch": "A stretch",
    "cant_tell": "Can't tell yet",
}
_WEEKDAYS: Final = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
_MONTHS: Final = (
    "January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November",
    "December",
)  # fmt: skip


@dataclass(frozen=True, slots=True)
class Links:
    """The three addresses an email carries."""

    edition: str
    manage: str
    unsubscribe: str


@dataclass(frozen=True, slots=True)
class Rendered:
    subject: str
    html: str
    text: str


def links_for(app_url: str, edition_id: str, token: str) -> Links:
    base = app_url.rstrip("/")
    return Links(
        edition=f"{base}/weekly?e={quote(edition_id, safe='')}",
        manage=f"{base}/weekly",
        unsubscribe=f"{base}/api/weekly/unsubscribe?token={quote(token, safe='')}",
    )


def list_unsubscribe_headers(unsubscribe_url: str, mailto: str | None) -> dict[str, str]:
    """``List-Unsubscribe`` (the https address, and a ``mailto:`` only when one is configured) and the one-click marker (RFC 8058)."""
    targets = [f"<{unsubscribe_url}>"]
    if mailto:
        address = mailto.removeprefix("mailto:").strip()
        if address:
            targets.append(f"<mailto:{address}?subject=unsubscribe>")
    return {
        "List-Unsubscribe": ", ".join(targets),
        "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
    }


def long_date(iso: str) -> str:
    """ "Monday, 19 October 2026" from an ISO date (the text itself when it is not one)."""
    try:
        d = date.fromisoformat(iso)
    except ValueError:
        return iso
    return f"{_WEEKDAYS[d.weekday()]}, {d.day} {_MONTHS[d.month - 1]} {d.year}"


def short_date(iso: str) -> str:
    try:
        d = date.fromisoformat(iso)
    except ValueError:
        return iso
    return f"{d.day} {_MONTHS[d.month - 1][:3]} {d.year}"


def subject_of(edition: WeeklyEdition) -> str:
    return f"{BRAND} No. {edition.issue}: {edition.headline}"


def _e(text: str) -> str:
    return html.escape(text, quote=True)


def _row(inner: str, *, padding: str = "0 28px") -> str:
    return f'<tr><td style="padding:{padding};">{inner}</td></tr>'


def _heading(label: str) -> str:
    return (
        f'<p style="margin:26px 0 8px 0;padding:6px 0 0 0;border-top:3px double {RULE};font:bold 12px {SERIF};'
        f'letter-spacing:2px;text-transform:uppercase;color:{INK};">{_e(label)}</p>'
    )


def _job_html(job: WeeklyJob) -> str:
    parts = [
        f'<p style="margin:14px 0 2px 0;font:bold 19px/1.25 {SERIF};color:{INK};">'
        f'<a href="{_e(job.applyUrl)}" style="color:{INK};text-decoration:underline;">{_e(job.title)}</a></p>',
        f'<p style="margin:0 0 4px 0;font:15px/1.4 {SERIF};color:{MUTED};">{_e(job.employer)} &middot; {_e(job.place)}'
        f' &middot; <span style="font-style:italic;">{_e(FIT_WORDS[job.fit])}</span></p>',
    ]
    if job.note:
        parts.append(f'<p style="margin:0 0 6px 0;font:15px/1.45 {SERIF};color:{INK};">{_e(job.note)}</p>')
    for met in job.met:
        parts.append(
            f'<p style="margin:0 0 6px 0;padding-left:12px;border-left:2px solid {FAINT};font:14px/1.45 {SERIF};color:{INK};">'
            f"<b>You meet</b> &ldquo;{_e(met.ask)}&rdquo;<br>"
            f'<span style="color:{MUTED};font-style:italic;">From {_e(met.cite.source)}: {_e(met.cite.text)}</span></p>'
        )
    if job.notShown:
        shown = "; ".join(_e(item) for item in job.notShown)
        parts.append(
            f'<p style="margin:0 0 6px 0;font:14px/1.45 {SERIF};color:{MUTED};"><b>Nothing you shared shows yet:</b> {shown}</p>'
        )
    parts.append(
        f'<p style="margin:0 0 10px 0;font:14px {SERIF};"><a href="{_e(job.applyUrl)}" style="color:{LINK};">'
        f"See it on {_e(job.employer)}&rsquo;s board &rarr;</a></p>"
    )
    return "".join(parts)


def _progress_lines(progress: WeeklyProgress) -> list[str]:
    lines: list[str] = []
    if progress.planRole is not None and progress.planWeek is not None and progress.planWeeks is not None:
        line = f"Your {progress.planRole} plan: week {progress.planWeek} of {progress.planWeeks}"
        if progress.ticksPlanned:
            line += f", {progress.ticksThisWeek or 0} of {progress.ticksPlanned} steps ticked this week"
        lines.append(f"{line}.")
    if progress.closestRole is not None:
        eta = f" ({progress.closestEta})" if progress.closestEta else ""
        lines.append(f"Closest role on your graph: {progress.closestRole}{eta}.")
    if progress.lastPractice is not None:
        lines.append(f"Last practice interview: {short_date(progress.lastPractice)}.")
    return lines


def _news_html(item: WeeklyNews) -> str:
    when = f", {_e(short_date(item.date))}" if item.date else ""
    return (
        f'<p style="margin:12px 0 2px 0;font:bold 17px/1.3 {SERIF};color:{INK};">'
        f'<a href="{_e(item.url)}" style="color:{INK};text-decoration:underline;">{_e(item.headline)}</a></p>'
        f'<p style="margin:0 0 2px 0;font:14px {SERIF};color:{MUTED};font-style:italic;">{_e(item.publication)}{when}</p>'
        f'<p style="margin:0 0 8px 0;font:15px/1.45 {SERIF};color:{INK};">{_e(item.why)} '
        f'<span style="color:{MUTED};">(Your skill: {_e(item.skill)}.)</span></p>'
    )


def render_html(edition: WeeklyEdition, links: Links) -> str:
    body: list[str] = []
    body.append(
        _row(
            f'<p style="margin:26px 0 4px 0;text-align:center;font:44px/1.1 {NAMEPLATE};color:{INK};">{_e(BRAND)}</p>'
            f'<p style="margin:0;padding:6px 0;text-align:center;border-top:1px solid {RULE};border-bottom:1px solid {RULE};'
            f'font:12px {SERIF};letter-spacing:1px;text-transform:uppercase;color:{INK};">'
            f"No. {edition.issue} &middot; {_e(long_date(edition.weekOf))} &middot; "
            f"For {_e(edition.reader)}{', ' + _e(edition.place) if edition.place else ''}</p>"
        )
    )
    body.append(
        _row(
            f'<h1 style="margin:22px 0 4px 0;font:bold 30px/1.2 {SERIF};color:{INK};">{_e(edition.headline)}</h1>'
            f'<p style="margin:0 0 4px 0;font:14px {SERIF};"><a href="{_e(links.edition)}" style="color:{LINK};">'
            "Read this edition in the app &rarr;</a></p>"
        )
    )
    if edition.jobs:
        body.append(_row(_heading("Openings that fit") + "".join(_job_html(j) for j in edition.jobs)))
    if edition.progress is not None:
        lines = "".join(
            f'<p style="margin:0 0 6px 0;font:15px/1.45 {SERIF};color:{INK};">{_e(line)}</p>'
            for line in _progress_lines(edition.progress)
        )
        nudge = (
            f'<p style="margin:8px 0 6px 0;font:italic 15px/1.45 {SERIF};color:{INK};">{_e(edition.progress.nudge)}</p>'
            if edition.progress.nudge
            else ""
        )
        body.append(_row(_heading("Your progress") + lines + nudge))
    if edition.news:
        body.append(_row(_heading("News from your field") + "".join(_news_html(n) for n in edition.news)))
    if edition.tip or edition.quote:
        tip = (
            f'<p style="margin:12px 0 8px 0;font:15px/1.5 {SERIF};color:{INK};"><b>This week, try this.</b> {_e(edition.tip)}</p>'
            if edition.tip
            else ""
        )
        quote_html = (
            f'<p style="margin:10px 0 0 0;text-align:center;font:italic 18px/1.4 {SERIF};color:{INK};">'
            f"&ldquo;{_e(edition.quote)}&rdquo;</p>"
            if edition.quote
            else ""
        )
        body.append(_row(_heading("A tip and a thought") + tip + quote_html))
    body.append(
        _row(
            f'<p style="margin:30px 0 0 0;padding:12px 0 24px 0;border-top:1px solid {RULE};font:12px/1.6 {SERIF};'
            f'color:{MUTED};text-align:center;">You get this because you subscribed to {_e(BRAND)} on Leafout. '
            f"Every line comes from your own graph, an employer&rsquo;s own board or a named article.<br>"
            f'<a href="{_e(links.manage)}" style="color:{MUTED};">Manage</a> &middot; '
            f'<a href="{_e(links.unsubscribe)}" style="color:{MUTED};">Unsubscribe in one click</a></p>'
        )
    )
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{_e(subject_of(edition))}</title></head>"
        f'<body style="margin:0;padding:0;background:{PAPER};">'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:{PAPER};">'
        '<tr><td align="center" style="padding:16px 8px;">'
        f'<table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" '
        f'style="width:100%;max-width:600px;background:{SHEET};border:1px solid {FAINT};">'
        f"{''.join(body)}</table></td></tr></table></body></html>"
    )


def render_text(edition: WeeklyEdition, links: Links) -> str:
    out: list[str] = [
        BRAND.upper(),
        f"No. {edition.issue} - {long_date(edition.weekOf)} - For {edition.reader}"
        + (f", {edition.place}" if edition.place else ""),
        "",
        edition.headline,
        f"Read this edition in the app: {links.edition}",
    ]
    if edition.jobs:
        out += ["", "OPENINGS THAT FIT"]
        for job in edition.jobs:
            out += ["", f"{job.title} - {job.employer}, {job.place} ({FIT_WORDS[job.fit]})"]
            if job.note:
                out.append(job.note)
            for met in job.met:
                out.append(f'You meet "{met.ask}" (From {met.cite.source}: {met.cite.text})')
            if job.notShown:
                out.append("Nothing you shared shows yet: " + "; ".join(job.notShown))
            out.append(f"On {job.employer}'s board: {job.applyUrl}")
    if edition.progress is not None:
        out += ["", "YOUR PROGRESS", *_progress_lines(edition.progress)]
        if edition.progress.nudge:
            out += ["", edition.progress.nudge]
    if edition.news:
        out += ["", "NEWS FROM YOUR FIELD"]
        for item in edition.news:
            when = f", {short_date(item.date)}" if item.date else ""
            out += [
                "",
                f"{item.headline} ({item.publication}{when})",
                f"{item.why} (Your skill: {item.skill}.)",
                item.url,
            ]
    if edition.tip:
        out += ["", "THIS WEEK, TRY THIS", edition.tip]
    if edition.quote:
        out += ["", f'"{edition.quote}"']
    out += [
        "",
        "--",
        f"You get this because you subscribed to {BRAND} on Leafout.",
        f"Manage: {links.manage}",
        f"Unsubscribe in one click: {links.unsubscribe}",
    ]
    return "\n".join(out)


def render_email(edition: WeeklyEdition, links: Links) -> Rendered:
    return Rendered(
        subject=subject_of(edition), html=render_html(edition, links), text=render_text(edition, links)
    )


# ───────────────────────── the unsubscribe page ─────────────────────────

_PAGE_STYLE: Final = (
    f"body{{margin:0;background:{PAPER};color:{INK};font:17px/1.5 {SERIF};}}"
    "main{max-width:30rem;margin:12vh auto;padding:0 20px;}"
    f"h1{{font:bold 28px/1.2 {NAMEPLATE};margin:0 0 12px 0;}}"
    f"button,a.btn{{display:inline-block;margin-top:12px;padding:12px 20px;min-height:44px;font:16px {SERIF};"
    f"color:{PAPER};background:{INK};border:1px solid {INK};cursor:pointer;text-decoration:none;}}"
    f"a{{color:{LINK};}}"
)


def _page(title: str, inner: str) -> str:
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta name="robots" content="noindex">'
        f"<title>{_e(title)}</title><style>{_PAGE_STYLE}</style></head><body><main>{inner}</main></body></html>"
    )


def unsubscribe_confirm_page(token: str) -> str:
    """The page the link in the email opens: one button, a POST that does it (a link scanner that only fetches the page
    unsubscribes nobody)."""
    action = f"/api/weekly/unsubscribe?token={quote(token, safe='')}"
    return _page(
        f"Unsubscribe from {BRAND}",
        f"<h1>{_e(BRAND)}</h1><p>Stop the weekly paper for this account?</p>"
        f'<form method="post" action="{_e(action)}"><button type="submit">Unsubscribe</button></form>',
    )


def unsubscribed_page(app_url: str) -> str:
    return _page(
        "Unsubscribed",
        f"<h1>{_e(BRAND)}</h1><p>You are unsubscribed. No more editions will be sent.</p>"
        f'<p>Changed your mind? <a href="{_e(app_url.rstrip("/"))}/weekly">Subscribe again in the app</a>.</p>',
    )


def unknown_link_page(app_url: str) -> str:
    return _page(
        "Link not recognised",
        f"<h1>{_e(BRAND)}</h1><p>This unsubscribe link is not recognised. You can manage the paper in the app.</p>"
        f'<p><a class="btn" href="{_e(app_url.rstrip("/"))}/weekly">Open Manage</a></p>',
    )


__all__ = [
    "BRAND",
    "FIT_WORDS",
    "Links",
    "Rendered",
    "links_for",
    "list_unsubscribe_headers",
    "long_date",
    "render_email",
    "render_html",
    "render_text",
    "short_date",
    "subject_of",
    "unknown_link_page",
    "unsubscribe_confirm_page",
    "unsubscribed_page",
]
