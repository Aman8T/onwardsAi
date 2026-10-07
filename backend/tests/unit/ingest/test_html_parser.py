"""The linear HTML tokenizer and the SPA bundle analysis: what they extract, and that hostile input is cheap.

The wall-time limits are loose on purpose (a normal run takes well under two seconds for 2 MB): they exist to
catch an accidentally quadratic pattern, which would take minutes, not to benchmark the machine.
"""

from __future__ import annotations

import time

import httpx
import pytest

from tayyari.core.limits import WEB_MAX_PAGE_LINKS, WEB_MAX_SCRIPT_URLS, WEB_MAX_STRUCTURED
from tayyari.ingest.web import (
    MAX_BUNDLES,
    MAX_TOKENS,
    analyze_bundle,
    ingest_web,
    parse_page,
    strip_html_comments,
)
from tests.unit.ingest.helpers import PUBLIC_IP, PUBLIC_IP_2, ChunkedStream, FakeDns, FakeWeb

URL = "https://example.com/portfolio/"
MB2 = 2 * 1024 * 1024
TIME_LIMIT_S = 8.0


# ----------------------------------------------------------------------------- what the parser reads


def test_title_meta_headings_links_and_text() -> None:
    info = parse_page(
        """<!doctype html><html><head>
        <title> Riya Kapoor  | Design </title>
        <meta name="description" content="Frontend engineer &amp; designer">
        <meta property="og:title" content="Riya Kapoor, Design">
        <meta property="og:description" content="Case studies">
        <meta property="og:site_name" content="Riya">
        <meta name="author" content="Riya Kapoor">
        <meta name="generator" content="Astro v4">
        </head><body>
        <h1>Hello, I am Riya</h1>
        <p>I build <b>fast</b> interfaces.</p>
        <h2>Checkout, rebuilt</h2>
        <ul><li>One</li><li>Two</li></ul>
        <a href="/work">Work</a> <a href="https://github.com/riya">GitHub</a>
        <a href="mailto:r@example.com">mail</a> <a href="javascript:void(0)">js</a> <a href="#top">top</a>
        <a href="tel:+100">call</a>
        </body></html>""",
        URL,
    )
    assert info.title == "Riya Kapoor | Design"
    assert info.description == "Frontend engineer & designer"
    assert info.og_title == "Riya Kapoor, Design"
    assert info.og_description == "Case studies"
    assert info.site_name == "Riya"
    assert info.author == "Riya Kapoor"
    assert info.generator == "Astro v4"
    assert info.headings == [(1, "Hello, I am Riya"), (2, "Checkout, rebuilt")]
    assert "I build fast interfaces." in info.text
    assert "- One" in info.text
    assert "- Two" in info.text
    assert info.links == [
        "https://example.com/work",
        "https://github.com/riya",
    ]  # no mailto, tel, javascript or #


def test_script_style_comments_and_hidden_blocks_are_not_text() -> None:
    info = parse_page(
        """<html><body>
        <script>var secret = "SCRIPT-TEXT";</script>
        <style>.x { content: "STYLE-TEXT" }</style>
        <!-- COMMENT-TEXT -->
        <noscript>NOSCRIPT-TEXT</noscript>
        <svg><text>SVG-TEXT</text></svg>
        <template>TEMPLATE-TEXT</template>
        <iframe>IFRAME-TEXT</iframe>
        <p>Visible text &mdash; with an entity &amp; a number &#35;1.</p>
        </body></html>""",
        URL,
    )
    for hidden in (
        "SCRIPT-TEXT",
        "STYLE-TEXT",
        "COMMENT-TEXT",
        "NOSCRIPT-TEXT",
        "SVG-TEXT",
        "TEMPLATE-TEXT",
        "IFRAME-TEXT",
    ):
        assert hidden not in info.text
    assert info.text == "Visible text — with an entity & a number #1."


def test_site_chrome_is_dropped_when_there_is_enough_other_text() -> None:
    body = "<p>" + "Real content about the work. " * 30 + "</p>"
    info = parse_page(
        f"<html><body><nav>MENU-ITEMS</nav>{body}<footer>COPYRIGHT-LINE</footer></body></html>", URL
    )
    assert "MENU-ITEMS" not in info.text
    assert "COPYRIGHT-LINE" not in info.text
    assert "Real content" in info.text


def test_a_page_that_is_all_header_falls_back_to_the_lenient_text() -> None:
    info = parse_page(
        "<html><body><header><h1>Only a header</h1><p>With a bit of text.</p></header></body></html>", URL
    )
    assert "Only a header" in info.text
    assert "With a bit of text." in info.text


def test_json_ld_people_and_sites_become_structured_lines() -> None:
    info = parse_page(
        """<html><head>
        <script type="application/ld+json">
        {"@context":"https://schema.org","@graph":[
          {"@type":"Person","name":"Riya Kapoor","jobTitle":"Frontend Engineer","url":"https://riya.dev",
           "sameAs":["https://github.com/riya","https://linkedin.com/in/riya"]},
          {"@type":"BreadcrumbList","name":"ignored"}]}
        </script>
        <script type="application/ld+json">{ this is not json</script>
        </head><body>x</body></html>""",
        URL,
    )
    assert len(info.structured) == 1
    line = info.structured[0]
    assert line.startswith("Person: name: Riya Kapoor | jobTitle: Frontend Engineer")
    assert "sameAs: https://github.com/riya, https://linkedin.com/in/riya" in line


def test_structured_data_is_capped() -> None:
    block = '<script type="application/ld+json">{"@type":"Person","name":"N"}</script>'
    info = parse_page(f"<html><head>{block * 40}</head><body>x</body></html>", URL)
    assert len(info.structured) == WEB_MAX_STRUCTURED


def test_scripts_are_collected_by_kind_and_preloads_are_added() -> None:
    info = parse_page(
        """<html><head>
        <script type="module" src="/assets/index-abc123.js"></script>
        <script src="/vendor.js"></script>
        <script type="text/javascript" src="/legacy.js"></script>
        <script type="application/json" src="/data.json"></script>
        <link rel="modulepreload" href="/assets/chunk-1.js">
        </head><body></body></html>""",
        URL,
    )
    assert info.module_scripts == ["/assets/index-abc123.js", "/assets/chunk-1.js"]
    assert info.plain_scripts == ["/vendor.js", "/legacy.js"]


def test_an_unterminated_title_stays_page_text_and_a_stray_lt_is_text() -> None:
    info = parse_page("<html><head><title>Never closed</head><body>a < b and c</body></html>", URL)
    assert info.title == ""
    assert "a < b and c" in info.text


def test_relative_links_are_resolved_and_only_http_survives() -> None:
    info = parse_page(
        '<a href="../up">1</a><a href="sub/page?x=1#frag">2</a><a href="//cdn.example.org/x">3</a>'
        '<a href="ftp://example.org/x">4</a><a href="file:///etc/passwd">5</a>',
        "https://example.com/a/b/",
    )
    assert info.links == [
        "https://example.com/a/up",
        "https://example.com/a/b/sub/page?x=1",
        "https://cdn.example.org/x",
    ]


def test_the_number_of_collected_links_and_scripts_is_capped() -> None:
    html = '<a href="/x">x</a>' * (WEB_MAX_PAGE_LINKS + 500) + '<script src="/a.js"></script>' * (
        WEB_MAX_SCRIPT_URLS + 50
    )
    info = parse_page(html, URL)
    assert len(info.links) == WEB_MAX_PAGE_LINKS
    assert len(info.plain_scripts) == WEB_MAX_SCRIPT_URLS


def test_strip_html_comments() -> None:
    assert strip_html_comments("a<!-- one -->b<!--two-->c") == "abc"
    assert strip_html_comments("a<!-- never closed") == "a<!-- never closed"
    assert strip_html_comments("<!--a--><!--b-->") == ""
    assert strip_html_comments("no comments") == "no comments"
    assert strip_html_comments("-->stray<!-- x -->end") == "-->strayend"


# ----------------------------------------------------------------------------- hostile pages stay cheap


def _hostile_pages() -> dict[str, str]:
    return {
        "deep nesting": "<div>" * 500_000 + "x" + "</div>" * 500_000,
        "deep nesting of skipped blocks": "<svg>" * 400_000 + "text",
        "unbalanced chrome": "<nav>" * 300_000 + "text",
        "unterminated tags": "<a " * 700_000,
        "unterminated comments": "<!--" * 500_000,
        "comment closers only": "-->" * 700_000,
        "400k+ tokens": "<b>" * 600_000,
        "open and close pairs": "<i></i>" * 300_000,
        "text and tag alternating": "<p>x" * 500_000,
        "unclosed attribute quote": '<a href="' + "x" * MB2,
        "many quote openers": '<a x="' * 300_000,
        "quote openers inside tags": '<a "' * 500_000,
        "attribute flood": "<a " + "x=1 " * 400_000 + ">",
        "unterminated script": "<script>" + "a" * MB2,
        "unterminated title": "<title>" + "a" * MB2,
        "script pairs": "<script></script>" * 100_000,
        "scripts with src": '<script src="a.js"></script>' * 60_000,
        "entity flood": "&amp;" * 400_000,
        "ampersand flood": "&" * MB2,
        "lone lt": "<" * MB2,
        "lt and letters": "<a" * 1_000_000,
        "end tags only": "</a" * 600_000,
        "declarations": "<!x" * 600_000,
        "processing instructions": "<?x" * 600_000,
        "headings": "<h1>t</h1>" * 100_000,
        "nested anchors": "<a href='/x'>" * 300_000,
        "table cells": "<td>x" * 400_000,
        "deep json-ld": '<script type="application/ld+json">' + "[" * 1_000_000 + "</script>",
    }


@pytest.mark.parametrize("name", list(_hostile_pages()))
def test_a_hostile_page_is_parsed_in_linear_time(name: str) -> None:
    html = _hostile_pages()[name]
    started = time.perf_counter()
    info = parse_page(html, URL)
    elapsed = time.perf_counter() - started
    assert elapsed < TIME_LIMIT_S, f"{name}: {elapsed:.1f}s"
    assert len(info.links) <= WEB_MAX_PAGE_LINKS
    assert len(info.module_scripts) + len(info.plain_scripts) <= WEB_MAX_SCRIPT_URLS + 1


def test_the_token_budget_bounds_a_page_that_is_nothing_but_tags() -> None:
    """Content after the budget is not read: a page of 600k tags never reaches its final paragraph."""
    page = "<b>" * (MAX_TOKENS) + "<p>TAIL-OF-THE-PAGE</p>"
    assert "TAIL-OF-THE-PAGE" not in parse_page(page, URL).text
    assert "TAIL-OF-THE-PAGE" in parse_page("<b>" * 1000 + "<p>TAIL-OF-THE-PAGE</p>", URL).text


def test_ordinary_two_megabyte_pages_are_not_cut_short() -> None:
    paragraph = "<p>One sentence of ordinary page text, repeated.</p>\n"
    html = "<html><body>" + paragraph * (MB2 // len(paragraph)) + "</body></html>"
    started = time.perf_counter()
    info = parse_page(html, URL)
    assert time.perf_counter() - started < TIME_LIMIT_S
    assert info.text.count("One sentence") == 1  # consecutive duplicate lines collapse
    assert info.text.startswith("One sentence of ordinary page text, repeated.")


# ----------------------------------------------------------------------------- the SPA bundle analysis


BUNDLE = r"""
import{jsx as e}from"react/jsx-runtime";
const t={title:"Recipe Genie",tagline:"Cook with what you have"},
n=()=>e("h1",{children:"Turn your pantry into dinner"}),
s=()=>e("input",{placeholder:"Search recipes by ingredient"});
const r=[{path:"/",element:1},{path:"/pantry",element:2},{path:"/settings/profile",element:3},{path:"*",element:4}];
supabase.from("recipes").select("*");supabase.from("saved_meals").select("*");
supabase.rpc("suggest_meals",{});
fetch("https://api.recipegenie.dev/v1/suggest");fetch("https://mygraphql.example.org/q");
const x="https://www.w3.org/2000/svg",y="https://reactjs.org/docs/error-decoder.html";
const msg="We could not find any recipes that match your pantry.";
// libraries
const a="@supabase/supabase-js",b="react-dom",c="@radix-ui/react-dialog",d="lucide-react";
"""


def test_a_bundle_yields_ui_copy_routes_backend_hints_and_libraries() -> None:
    f = analyze_bundle(BUNDLE, "")
    assert "Recipe Genie" in f.prop_strings
    assert "Turn your pantry into dinner" in f.prop_strings
    assert "Search recipes by ingredient" in f.prop_strings
    assert any(s == "We could not find any recipes that match your pantry." for s, _ in f.sentences)
    assert f.routes == ["/pantry", "/settings/profile"]  # "/" and "*" are not interesting
    assert f.tables == ["recipes", "saved_meals"]
    assert f.functions == ["suggest_meals"]
    assert [h for h, _ in f.hosts] == [
        "api.recipegenie.dev",
        "mygraphql.example.org",
    ]  # library internals are noise
    assert {"React", "Supabase", "Radix UI", "Lucide"} <= set(f.libraries)


def test_code_looking_strings_are_not_ui_copy() -> None:
    js = 'const a="bg-red-500 px-4 flex items-center",b="useState is not defined",c="a.b.c(d)=>e",d={title:"x"};'
    f = analyze_bundle(js, "")
    assert f.prop_strings == []
    assert f.sentences == []


def _hostile_bundles() -> dict[str, str]:
    bs = chr(92)
    return {
        "escaped quotes": ('"' + bs + '"') * (MB2 // 3),
        "backslashes": bs * MB2,
        "unclosed quote": '"' + "a" * MB2,
        "prop openers": 'title:"' * 300_000,
        "children openers": "children:'" * 200_000,
        "backticks": "`" * MB2,
        "template literals": "`a$b`" * 400_000,
        "many sentences": '"Hello world, this is a sentence."' * 60_000,
        "single quotes": "'" * MB2,
        "scheme floods": "https://" * 250_000,
        "long host-like runs": ("https://" + "a." * 125 + "bb") * 7_000,
        "route openers": 'path:"/' * 300_000,
        "table openers": '.from("' * 300_000,
        "utility classes": "bg-red-500 px-4 " * 130_000,
        "flex flood": "flex " * 400_000,
        "library names": "react-dom " * 200_000,
    }


@pytest.mark.parametrize("name", list(_hostile_bundles()))
def test_a_hostile_bundle_is_analysed_in_linear_time(name: str) -> None:
    js = _hostile_bundles()[name]
    started = time.perf_counter()
    analyze_bundle(js, "")
    elapsed = time.perf_counter() - started
    assert elapsed < TIME_LIMIT_S, f"{name}: {elapsed:.1f}s"


# ----------------------------------------------------------------------------- ingest_web on pages and bundles

SPA_HTML = """<!doctype html><html><head><title>Recipe Genie</title>
<script type="module" crossorigin src="/assets/index-Ab12.js"></script>
<script type="module" src="/assets/vendor-Cd34.js"></script>
<script type="module" src="/assets/extra-Ef56.js"></script>
<script type="module" src="https://cdn.other.test/evil.js"></script>
<meta name="generator" content="Lovable"></head><body><div id="root"></div></body></html>"""


def _serve(web: FakeWeb, routes: dict[str, str], *, status: int = 200) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = routes.get(request.url.path)
        if body is None:
            return httpx.Response(404, text="missing")
        kind = (
            "text/html"
            if request.url.path.endswith("/") or request.url.path == "/"
            else "application/javascript"
        )
        return httpx.Response(status, headers={"content-type": kind}, content=body.encode())

    web.serve(PUBLIC_IP, handler)


async def test_a_client_rendered_app_is_read_through_its_bundle(dns: FakeDns, web: FakeWeb) -> None:
    dns.answer("recipes.lovable.app", [PUBLIC_IP])
    _serve(
        web,
        {
            "/": SPA_HTML,
            "/assets/index-Ab12.js": BUNDLE,
            "/assets/vendor-Cd34.js": "const q=1;",
            "/assets/extra-Ef56.js": "x",
        },
    )
    lines: list[str] = []
    doc = await ingest_web("https://recipes.lovable.app/", "lovable", "Lovable", "Lovable app", lines.append)
    assert doc.ok is True
    assert doc.meta["spa"] is True
    assert doc.meta["builtWithLovable"] is True
    assert "## UI copy found in the app bundle" in doc.text
    assert "- Turn your pantry into dinner" in doc.text
    assert "Routes in the app" in doc.text
    assert "/pantry" in doc.meta["routes"]
    assert "Live app" in doc.quick_signals
    assert lines[1].startswith("SPA detected")
    # Only same-origin bundles, and at most MAX_BUNDLES of them, were downloaded; "index" files come first.
    paths = [r.url.path for r in web.requests]
    assert paths[0] == "/"
    assert len(paths) - 1 == MAX_BUNDLES == 2
    assert paths[1] == "/assets/index-Ab12.js"
    assert all(r.headers["host"] == "recipes.lovable.app" for r in web.requests)


async def test_a_bundle_that_fails_to_load_is_noted_not_fatal(dns: FakeDns, web: FakeWeb) -> None:
    dns.answer("recipes.lovable.app", [PUBLIC_IP])
    _serve(web, {"/": SPA_HTML})  # every bundle is a 404
    doc = await ingest_web(
        "https://recipes.lovable.app/", "lovable", "Lovable", "Lovable app", lambda _: None
    )
    assert doc.ok is True
    assert any("could not be downloaded" in n for n in doc.notes)


async def test_an_oversized_bundle_is_read_partially_and_says_so(dns: FakeDns, web: FakeWeb) -> None:
    dns.answer("recipes.lovable.app", [PUBLIC_IP])
    big = BUNDLE + "/*" + "x" * (3 * 1024 * 1024) + "*/"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/":
            return httpx.Response(200, headers={"content-type": "text/html"}, content=SPA_HTML.encode())
        return httpx.Response(
            200, headers={"content-type": "application/javascript"}, stream=ChunkedStream([big.encode()])
        )

    web.serve(PUBLIC_IP, handler)
    doc = await ingest_web(
        "https://recipes.lovable.app/", "lovable", "Lovable", "Lovable app", lambda _: None
    )
    assert doc.ok is True
    assert any("larger than 2 MB and was read partially" in n for n in doc.notes)


async def test_a_bundle_on_another_origin_is_never_requested(dns: FakeDns, web: FakeWeb) -> None:
    dns.answer("recipes.lovable.app", [PUBLIC_IP])
    web.serve(PUBLIC_IP_2, lambda request: httpx.Response(200, text="LEAK"))  # where cdn.other.test would be
    _serve(web, {"/": SPA_HTML, "/assets/index-Ab12.js": BUNDLE, "/assets/vendor-Cd34.js": "x"})
    await ingest_web("https://recipes.lovable.app/", "lovable", "Lovable", "Lovable app", lambda _: None)
    assert web.to(PUBLIC_IP_2) == []
    assert "cdn.other.test" not in dns.calls


@pytest.mark.parametrize(
    "html",
    [
        "<html><head><title>Just a moment...</title></head><body>Checking your browser</body></html>",
        "<html><head><title>Attention Required! | Cloudflare</title></head><body>x</body></html>"
        + "<!-- cf-chl -->",
        '<html><head><title>Hi</title></head><body><div class="g-recaptcha-response"></div>captcha</body></html>',
    ],
)
async def test_a_bot_wall_is_reported(html: str, dns: FakeDns, web: FakeWeb) -> None:
    dns.answer("example.test", [PUBLIC_IP])
    web.serve(
        PUBLIC_IP,
        lambda request: httpx.Response(200, headers={"content-type": "text/html"}, content=html.encode()),
    )
    doc = await ingest_web("https://example.test/", "link", "Example", "Web page", lambda _: None)
    assert doc.ok is False
    assert doc.error == "That site shows a bot check, so it could not be read."


@pytest.mark.parametrize(
    ("content_type", "body", "error_start"),
    [
        ("application/pdf", b"%PDF-1.4", "That link points to a PDF"),
        ("image/png", b"\x89PNG", "That link is not a web page (image/png)."),
    ],
)
async def test_non_page_content_is_explained(
    content_type: str, body: bytes, error_start: str, dns: FakeDns, web: FakeWeb
) -> None:
    dns.answer("example.test", [PUBLIC_IP])
    web.serve(
        PUBLIC_IP, lambda request: httpx.Response(200, headers={"content-type": content_type}, content=body)
    )
    doc = await ingest_web("https://example.test/file", "link", "Example", "Web page", lambda _: None)
    assert doc.ok is False
    assert doc.error.startswith(error_start)


async def test_plain_text_and_json_are_read_as_text(dns: FakeDns, web: FakeWeb) -> None:
    dns.answer("example.test", [PUBLIC_IP])
    web.serve(
        PUBLIC_IP,
        lambda request: httpx.Response(
            200, headers={"content-type": "text/plain"}, content=b"I know Python and Docker."
        ),
    )
    doc = await ingest_web("https://example.test/about.txt", "link", "Example", "Web page", lambda _: None)
    assert doc.ok is True
    assert doc.text == "# example.test/about.txt\n\nI know Python and Docker."
    assert doc.quick_signals == ["Python", "Docker"]


async def test_an_empty_page_is_reported(dns: FakeDns, web: FakeWeb) -> None:
    dns.answer("example.test", [PUBLIC_IP])
    web.serve(
        PUBLIC_IP,
        lambda request: httpx.Response(
            200, headers={"content-type": "text/html"}, content=b"<html><body></body></html>"
        ),
    )
    doc = await ingest_web("https://example.test/", "link", "Example", "Web page", lambda _: None)
    assert doc.ok is False
    assert doc.error == "No readable content was found on that page."


async def test_the_page_charset_is_honoured(dns: FakeDns, web: FakeWeb) -> None:
    html = "<html><head><title>Café Portfolio</title></head><body><p>Résumé of work.</p></body></html>"
    dns.answer("example.test", [PUBLIC_IP])
    web.serve(
        PUBLIC_IP,
        lambda request: httpx.Response(
            200, headers={"content-type": "text/html; charset=iso-8859-1"}, content=html.encode("latin-1")
        ),
    )
    doc = await ingest_web("https://example.test/", "link", "Example", "Web page", lambda _: None)
    assert doc.meta["title"] == "Café Portfolio"
    assert "Résumé of work." in doc.text
