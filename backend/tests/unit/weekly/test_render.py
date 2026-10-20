"""The email of The Leafout Weekly: table layout, inline styles, escaped values, the links it must carry, the one-click
unsubscribe headers, a plain-text twin, and the small unsubscribe pages."""

from __future__ import annotations

import re

from tayyari.contract.weekly import (
    WeeklyCite,
    WeeklyEdition,
    WeeklyJob,
    WeeklyMet,
    WeeklyNews,
    WeeklyProgress,
)
from tayyari.weekly.render import (
    Links,
    links_for,
    list_unsubscribe_headers,
    long_date,
    render_email,
    short_date,
    unknown_link_page,
    unsubscribe_confirm_page,
    unsubscribed_page,
)

APP = "https://app.leafout.in"
EID = "e" * 32
TOKEN = "tok_abc-123"
LINKS = links_for(APP, EID, TOKEN)


def edition(**over: object) -> WeeklyEdition:
    values: dict[str, object] = {
        "id": EID,
        "issue": 12,
        "weekOf": "2026-10-19",
        "headline": "Two openings fit your graph this week",
        "reader": "Riya Kapoor",
        "place": "Bengaluru, India",
        "jobs": [
            WeeklyJob(
                listingId="1" * 32,
                employer="Kestrel Pay",
                title="Frontend Engineer",
                place="Bengaluru",
                applyUrl="https://job-boards.greenhouse.io/kestrelpay/jobs/1",
                fit="looks_close",
                met=[
                    WeeklyMet(
                        ask="Strong experience with React and TypeScript in production",
                        cite=WeeklyCite(source="Resume", text="Rebuilt Kitebird checkout in React"),
                    )
                ],
                notShown=["Experience with GraphQL APIs and Apollo Client"],
                note="You meet 1 of 2 asks.",
            ),
            WeeklyJob(
                listingId="2" * 32,
                employer="Nilgiri Mills",
                title="Design Systems Engineer",
                place="Remote",
                applyUrl="https://jobs.lever.co/nilgiri/2",
                fit="stretch",
                met=[],
                notShown=["Kubernetes"],
                note="You show 1 of the 4 skills it names.",
            ),
        ],
        "progress": WeeklyProgress(
            planRole="AI Product Engineer",
            planWeek=2,
            planWeeks=4,
            ticksThisWeek=1,
            ticksPlanned=3,
            closestRole="Design Engineer",
            closestEta="~2 months",
            lastPractice="2026-10-15",
            nudge="Tick off 2 more steps of your AI Product Engineer plan this week.",
        ),
        "news": [
            WeeklyNews(
                headline="Design systems meet AI components",
                publication="TechCrunch",
                date="2026-10-15",
                url="https://techcrunch.com/2026/10/15/ai-design/",
                why="Component libraries are changing.",
                skill="Design systems",
            )
        ],
        "tip": "Keep a file of what you finished.",
        "quote": "Good work, kept in a list, becomes a career.",
        "createdAt": "2026-10-19T01:00:00+00:00",
    }
    values.update(over)
    return WeeklyEdition.model_validate(values)


def test_the_subject_names_the_paper_the_issue_and_the_headline() -> None:
    mail = render_email(edition(), LINKS)
    assert mail.subject == "The Leafout Weekly No. 12: Two openings fit your graph this week"


def test_the_html_is_a_600px_table_with_inline_styles_and_no_images() -> None:
    html = render_email(edition(), LINKS).html
    assert html.startswith("<!doctype html>") and 'width="600"' in html and "max-width:600px" in html
    assert "<table" in html and "<img" not in html and "<script" not in html and "<link" not in html
    assert "<style" not in html  # every style is inline, the way mail clients keep them
    assert "Georgia" in html and "Times" in html
    assert "The Leafout Weekly" in html


def test_every_listing_links_to_the_employers_own_board() -> None:
    html = render_email(edition(), LINKS).html
    assert (
        html.count("https://job-boards.greenhouse.io/kestrelpay/jobs/1") >= 2
    )  # the title and the "see it" line
    assert "https://jobs.lever.co/nilgiri/2" in html
    assert "Kestrel Pay&rsquo;s board" in html


def test_the_footer_carries_manage_the_edition_and_a_one_click_unsubscribe() -> None:
    html = render_email(edition(), LINKS).html
    assert f"{APP}/weekly?e={EID}" in html
    assert f'href="{APP}/weekly"' in html
    assert f"{APP}/api/weekly/unsubscribe?token={TOKEN}" in html
    assert LINKS == Links(
        edition=f"{APP}/weekly?e={EID}",
        manage=f"{APP}/weekly",
        unsubscribe=f"{APP}/api/weekly/unsubscribe?token={TOKEN}",
    )


def test_what_a_reader_sees_is_cited_and_worded_from_the_edition() -> None:
    html = render_email(edition(), LINKS).html
    assert "You meet" in html and "Strong experience with React and TypeScript in production" in html
    assert "From Resume: Rebuilt Kitebird checkout in React" in html
    assert "Nothing you shared shows yet" in html and "Experience with GraphQL APIs and Apollo Client" in html
    assert "You meet 1 of 2 asks." in html and "Looks close" in html and "A stretch" in html
    assert "AI Product Engineer plan: week 2 of 4, 1 of 3 steps ticked this week." in html
    assert "Closest role on your graph: Design Engineer (~2 months)." in html
    assert "Last practice interview: 15 Oct 2026." in html
    assert "TechCrunch, 15 Oct 2026" in html and "Your skill: Design systems." in html
    assert "Monday, 19 October 2026" in html and "No. 12" in html and "Bengaluru, India" in html


def test_a_value_with_markup_is_escaped_everywhere() -> None:
    hostile = '<script>alert(1)</script> & "quotes"'
    tampered = edition(
        reader=hostile,
        headline=hostile,
        tip=hostile,
        jobs=[
            WeeklyJob(
                listingId="1" * 32,
                employer=hostile,
                title=hostile,
                place=hostile,
                applyUrl='https://x.example/?a="><script>alert(2)</script>',
                fit="looks_close",
                met=[WeeklyMet(ask=hostile, cite=WeeklyCite(source=hostile, text=hostile))],
                notShown=[hostile],
                note=hostile,
            )
        ],
    )
    html = render_email(tampered, LINKS).html
    assert "<script" not in html and "alert(1)</script>" not in html
    assert '"><script' not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html


def test_sections_the_reader_left_out_are_not_in_the_paper() -> None:
    html = render_email(edition(jobs=[], progress=None, news=[], tip=None, quote=None), LINKS).html
    for heading in ("Openings that fit", "Your progress", "News from your field", "A tip and a thought"):
        assert heading.lower() not in html.lower()


def test_the_plain_text_twin_has_the_same_facts_and_links() -> None:
    text = render_email(edition(), LINKS).text
    assert "<" not in text.replace("<-", "")  # no markup
    assert "THE LEAFOUT WEEKLY" in text and "OPENINGS THAT FIT" in text and "YOUR PROGRESS" in text
    assert "NEWS FROM YOUR FIELD" in text and "THIS WEEK, TRY THIS" in text
    assert "Frontend Engineer - Kestrel Pay, Bengaluru (Looks close)" in text
    assert "https://job-boards.greenhouse.io/kestrelpay/jobs/1" in text
    assert f"Unsubscribe in one click: {LINKS.unsubscribe}" in text and f"Manage: {LINKS.manage}" in text
    assert re.search(r"Read this edition in the app: https://app\.leafout\.in/weekly\?e=e+", text)


def test_unsubscribe_headers_are_one_click_and_a_mailto_only_when_configured() -> None:
    plain = list_unsubscribe_headers(LINKS.unsubscribe, None)
    assert plain == {
        "List-Unsubscribe": f"<{LINKS.unsubscribe}>",
        "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
    }
    with_mailto = list_unsubscribe_headers(LINKS.unsubscribe, "mailto:stop@leafout.in")
    assert (
        with_mailto["List-Unsubscribe"]
        == f"<{LINKS.unsubscribe}>, <mailto:stop@leafout.in?subject=unsubscribe>"
    )
    assert (
        list_unsubscribe_headers(LINKS.unsubscribe, "stop@leafout.in")["List-Unsubscribe"].count("<mailto:")
        == 1
    )
    assert "mailto" not in list_unsubscribe_headers(LINKS.unsubscribe, "  ")["List-Unsubscribe"]


def test_the_token_is_url_encoded_in_the_links() -> None:
    assert links_for(APP + "/", "id", "a b/c").unsubscribe == f"{APP}/api/weekly/unsubscribe?token=a%20b%2Fc"


def test_dates() -> None:
    assert long_date("2026-10-23") == "Friday, 23 October 2026"
    assert short_date("2026-10-05") == "5 Oct 2026"
    assert long_date("garbled") == "garbled" and short_date("garbled") == "garbled"


# ───────────────────────── the unsubscribe pages ─────────────────────────


def test_the_confirm_page_only_posts_when_the_button_is_pressed() -> None:
    page = unsubscribe_confirm_page(TOKEN)
    assert '<form method="post" action="/api/weekly/unsubscribe?token=tok_abc-123">' in page
    assert "<button" in page and 'name="robots" content="noindex"' in page
    assert "<script>" not in unsubscribe_confirm_page('x"><script>')


def test_the_done_and_unknown_pages_point_back_to_the_app() -> None:
    assert f'href="{APP}/weekly"' in unsubscribed_page(APP + "/")
    assert "not recognised" in unknown_link_page(APP) and f'href="{APP}/weekly"' in unknown_link_page(APP)
