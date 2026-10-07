"""Link parsing and classification, the `SourceDoc` model, and what the package exports."""

from __future__ import annotations

import time

import pytest

from tayyari.core import limits, text
from tayyari.ingest import classify
from tayyari.ingest.classify import (
    MAX_LINKS,
    ClassifiedUrl,
    SourceDoc,
    classify_link,
    classify_url,
    is_known_platform,
    normalize_url,
    plural,
    usable_links,
)

# ----------------------------------------------------------------------------- classify_url


@pytest.mark.parametrize(
    ("raw", "kind", "label", "kind_label", "url"),
    [
        # GitHub
        ("https://github.com/octocat", "github", "GitHub", "GitHub profile", "https://github.com/octocat"),
        ("github.com/octocat", "github", "GitHub", "GitHub profile", "https://github.com/octocat"),
        (
            "https://github.com/octocat/hello-world",
            "github",
            "GitHub",
            "GitHub repo",
            "https://github.com/octocat/hello-world",
        ),
        (
            "https://github.com/octocat/hello-world/issues/1",
            "github",
            "GitHub",
            "GitHub repo",
            "https://github.com/octocat/hello-world/issues/1",
        ),
        (
            "https://www.github.com/octocat/",
            "github",
            "GitHub",
            "GitHub profile",
            "https://www.github.com/octocat/",
        ),
        ("HTTPS://GitHub.COM/Octocat", "github", "GitHub", "GitHub profile", "https://github.com/Octocat"),
        (
            "https://github.com/orgs/acme",
            "github",
            "GitHub",
            "GitHub profile",
            "https://github.com/orgs/acme",
        ),
        ("https://github.com", "github", "GitHub", "GitHub profile", "https://github.com"),
        # Lovable
        (
            "https://recipe-genie.lovable.app/",
            "lovable",
            "Lovable",
            "Lovable app",
            "https://recipe-genie.lovable.app",
        ),
        (
            "https://lovable.dev/projects/abc-123",
            "lovable",
            "Lovable",
            "Lovable app",
            "https://lovable.dev/projects/abc-123",
        ),
        (
            "https://abc.lovableproject.com",
            "lovable",
            "Lovable",
            "Lovable app",
            "https://abc.lovableproject.com",
        ),
        # LinkedIn
        (
            "https://www.linkedin.com/in/someone",
            "linkedin",
            "LinkedIn",
            "LinkedIn profile",
            "https://www.linkedin.com/in/someone",
        ),
        (
            "linkedin.com/in/someone/",
            "linkedin",
            "LinkedIn",
            "LinkedIn profile",
            "https://linkedin.com/in/someone/",
        ),
        (
            "https://in.linkedin.com/in/someone",
            "linkedin",
            "LinkedIn",
            "LinkedIn profile",
            "https://in.linkedin.com/in/someone",
        ),
        ("https://lnkd.in/abc", "linkedin", "LinkedIn", "LinkedIn profile", "https://lnkd.in/abc"),
        # portfolios: by platform, by name in the host, by name in the path
        (
            "https://www.behance.net/riya",
            "portfolio",
            "Portfolio",
            "Portfolio site",
            "https://www.behance.net/riya",
        ),
        (
            "https://dribbble.com/riya",
            "portfolio",
            "Portfolio",
            "Portfolio site",
            "https://dribbble.com/riya",
        ),
        ("https://riya.github.io", "portfolio", "Portfolio", "Portfolio site", "https://riya.github.io"),
        ("https://riya.vercel.app/", "portfolio", "Portfolio", "Portfolio site", "https://riya.vercel.app"),
        ("https://riya.netlify.app", "portfolio", "Portfolio", "Portfolio site", "https://riya.netlify.app"),
        (
            "https://riya.notion.site/Me-123",
            "portfolio",
            "Portfolio",
            "Portfolio site",
            "https://riya.notion.site/Me-123",
        ),
        (
            "https://my-portfolio.example.com",
            "portfolio",
            "Portfolio",
            "Portfolio site",
            "https://my-portfolio.example.com",
        ),
        (
            "https://example.com/Portfolio/work",
            "portfolio",
            "Portfolio",
            "Portfolio site",
            "https://example.com/Portfolio/work",
        ),
        # everything else is a web page labelled by the site
        ("https://www.kaggle.com/someone", "link", "Kaggle", "Web page", "https://www.kaggle.com/someone"),
        ("https://docs.kaggle.com/x", "link", "Kaggle", "Web page", "https://docs.kaggle.com/x"),
        ("https://leetcode.com/u/someone", "link", "LeetCode", "Web page", "https://leetcode.com/u/someone"),
        (
            "https://stackoverflow.com/users/1/x",
            "link",
            "Stack Overflow",
            "Web page",
            "https://stackoverflow.com/users/1/x",
        ),
        ("https://medium.com/@someone", "link", "Blog", "Web page", "https://medium.com/@someone"),
        ("https://someone.medium.com", "link", "Blog", "Web page", "https://someone.medium.com"),
        ("https://twitter.com/someone", "link", "X", "Web page", "https://twitter.com/someone"),
        ("https://x.com/someone", "link", "X", "Web page", "https://x.com/someone"),
        ("https://youtu.be/abc", "link", "YouTube", "Web page", "https://youtu.be/abc"),
        (
            "https://huggingface.co/someone",
            "link",
            "Hugging Face",
            "Web page",
            "https://huggingface.co/someone",
        ),
        ("https://gist.github.com/x", "link", "Github", "Web page", "https://gist.github.com/x"),
        ("https://overreacted.io", "link", "Overreacted", "Web page", "https://overreacted.io"),
        ("https://my-site.co.uk/", "link", "My-site", "Web page", "https://my-site.co.uk"),
        ("https://blog.example.co.in/post", "link", "Example", "Web page", "https://blog.example.co.in/post"),
        ("http://93.184.216.34/page", "link", "93.184.216.34", "Web page", "http://93.184.216.34/page"),
        ("http://[2606:4700::1111]/", "link", "[2606:4700::1111]", "Web page", "http://[2606:4700::1111]"),
        # a query keeps the slash it follows
        ("https://example.com/?ref=cv", "link", "Example", "Web page", "https://example.com/?ref=cv"),
    ],
)
def test_classify_url_table(raw: str, kind: str, label: str, kind_label: str, url: str) -> None:
    assert classify_url(raw) == ClassifiedUrl(url, kind, label, kind_label)


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "mailto:riya@example.com",
        "MAILTO:riya@example.com",
        "tel:+15551234567",
        "sms:+15551234567",
        "javascript:alert(1)",
        "JavaScript:alert(1)",
        "data:text/html,<h1>hi</h1>",
        "blob:https://example.com/abc",
        "file:///etc/passwd",
        "ftp://example.com/file",
        "ssh://git@github.com/octocat/x.git",
        "gopher://example.com/",
        "git@github.com:octocat/x.git",
        "https://user:pass@example.com",
        "https://user@example.com",
        "http://user:pass@127.0.0.1/",
        "localhost",
        "http://localhost:3000",
        "hello",
        "not a url",
        "http://",
        "https://.com",
        "https://-bad.example.com",
        "https://bad-.example.com",
        "https://exa mple.com",
        "https://example..com",
        "https://" + "a" * 64 + ".com",
        "https://" + ".".join(["a" * 60] * 5) + ".com",
        "https://example.com:99999",
        "https://example.com:abc",
        "http://[::1",
        "http://[zzz]/",
        "2130706433",
    ],
)
def test_classify_url_rejects_non_web_schemes_credentials_and_malformed_hosts(raw: str) -> None:
    assert classify_url(raw) is None
    assert normalize_url(raw) is None


@pytest.mark.parametrize("raw", [None, 0, [], {}])
def test_classify_url_survives_non_strings_through_the_falsy_path(raw: object) -> None:
    # `normalize_url(raw or "")` is what the public wrappers rely on
    assert normalize_url(raw or "") is None  # type: ignore[arg-type]


# ----------------------------------------------------------------------------- normalisation


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://example.com", "https://example.com/"),
        ("example.com", "https://example.com/"),
        ("//example.com/x", "https://example.com/x"),
        ("  https://example.com/x  ", "https://example.com/x"),
        ("<https://example.com/x>", "https://example.com/x"),
        ('"https://example.com/x"', "https://example.com/x"),
        ("(https://example.com/x)", "https://example.com/x"),
        ("https://example.com/x,", "https://example.com/x"),
        ("https://example.com/x.", "https://example.com/x"),
        ("https://example.com/x;", "https://example.com/x"),
        ("HTTP://EXAMPLE.COM/Path", "http://example.com/Path"),
        ("https://example.com:443/x", "https://example.com/x"),
        ("http://example.com:80/x", "http://example.com/x"),
        ("https://example.com:8443/x", "https://example.com:8443/x"),
        ("http://example.com:443/x", "http://example.com:443/x"),
        ("https://example.com/x#section", "https://example.com/x"),
        ("https://example.com/a/b/../c/./d", "https://example.com/a/c/d"),
        ("https://example.com/a/b/../", "https://example.com/a/"),
        ("https://example.com/a/b/..", "https://example.com/a/b/"),  # trailing dots are pasted punctuation
        ("https://example.com/../../x", "https://example.com/x"),
        ("https://example.com/a%20b", "https://example.com/a%20b"),
        ("https://example.com/café", "https://example.com/caf%C3%A9"),
        ("https://example.com/?q=a b", None),
        ("https://example.com/?q=café&x=1", "https://example.com/?q=caf%C3%A9&x=1"),
        ("https://bücher.example/", "https://xn--bcher-kva.example/"),
        ("https://EXAMPLE.com./", None),
        ("http://[2606:4700:4700::1111]:8080/x", "http://[2606:4700:4700::1111]:8080/x"),
    ],
)
def test_normalize_url(raw: str, expected: str | None) -> None:
    parsed = normalize_url(raw)
    assert (parsed.url if parsed else None) == expected


def test_a_crlf_in_a_link_can_never_reach_a_request_line_or_header() -> None:
    assert normalize_url("https://example.com/a\r\nX-Evil: 1") is None  # whitespace is refused outright
    parsed = normalize_url("https://example.com/a%0d%0aX-Evil:%201?q=%0d%0a")
    assert parsed is not None
    assert "\r" not in parsed.url and "\n" not in parsed.url
    assert parsed.path == "/a%0d%0aX-Evil:%201"
    assert parsed.query == "?q=%0d%0a"


def test_parsed_url_properties() -> None:
    parsed = normalize_url("https://Example.com:8443/a/b?x=1#frag")
    assert parsed is not None
    assert (parsed.scheme, parsed.hostname, parsed.port, parsed.path, parsed.query) == (
        "https",
        "example.com",
        8443,
        "/a/b",
        "?x=1",
    )
    assert parsed.host == "example.com:8443"
    assert parsed.origin == "https://example.com:8443"
    assert parsed.url == "https://example.com:8443/a/b?x=1"
    plain = normalize_url("http://example.com")
    assert plain is not None
    assert (plain.port, plain.host, plain.origin, plain.path, plain.query) == (
        None,
        "example.com",
        "http://example.com",
        "/",
        "",
    )


def test_normalizing_hostile_input_is_linear() -> None:
    started = time.perf_counter()
    for raw in (
        "(" * 200_000 + "https://example.com" + ")" * 200_000,
        "https://example.com/" + "a/" * 200_000,
        "https://example.com/" + "../" * 200_000,
        "https://" + "a." * 100_000 + "com",
        "https://example.com/" + "%" * 500_000,
        "<" * 300_000,
        "?" * 500_000,
        "https://example.com/?" + "&a=b" * 200_000,
    ):
        normalize_url(raw)
    assert time.perf_counter() - started < 8


# ----------------------------------------------------------------------------- classify_link, usable_links


def test_classify_link_returns_kind_and_url_or_the_input_as_a_plain_link() -> None:
    assert classify_link("github.com/octocat") == ("github", "https://github.com/octocat")
    assert classify_link("  not a url ") == ("link", "not a url")
    assert classify_link("") == ("link", "")
    assert classify_link(None) == ("link", "")  # type: ignore[arg-type]


def test_usable_links_classifies_dedupes_and_drops_invalid_entries() -> None:
    links = [
        "github.com/octocat",
        "https://GitHub.com/octocat/",  # the same link with different case and a trailing slash
        "https://github.com/octocat",
        "mailto:x@y.com",
        "",
        "   ",
        "not a url",
        None,  # type: ignore[list-item]
        42,  # type: ignore[list-item]
        "https://riya.dev",
        "http://riya.dev",  # a different scheme is a different link
        "https://riya.dev/",
    ]
    result = usable_links(links)
    assert [(c.kind, c.url) for c in result] == [
        ("github", "https://github.com/octocat"),
        ("link", "https://riya.dev"),
        ("link", "http://riya.dev"),
    ]


def test_usable_links_keeps_the_first_max_links_valid_links_in_order() -> None:
    links = [f"https://site{i}.example.com" for i in range(20)]
    result = usable_links(["junk", *links])
    assert len(result) == MAX_LINKS == 8
    assert [c.url for c in result] == [f"https://site{i}.example.com" for i in range(8)]


def test_max_links_is_the_core_limit() -> None:
    assert MAX_LINKS == limits.MAX_LINKS == 8
    assert classify.MAX_LINKS is limits.MAX_LINKS or classify.MAX_LINKS == limits.MAX_LINKS


@pytest.mark.parametrize(
    ("host", "known"),
    [
        ("www.linkedin.com", True),
        ("github.com", True),
        ("gist.github.com", True),
        ("medium.com", True),
        ("someone.medium.com", True),
        ("kaggle.com", True),
        ("LEETCODE.com", True),
        ("riya.dev", False),
        ("notmedium.com", False),
        ("medium.com.evil.example", False),
        ("evilgithub.com", False),
    ],
)
def test_is_known_platform(host: str, known: bool) -> None:
    assert is_known_platform(host) is known


# ----------------------------------------------------------------------------- SourceDoc


def test_a_source_doc_with_an_error_is_not_ok_and_shows_a_failure_sub() -> None:
    doc = SourceDoc(id="x", kind="link", label="L", error="boom")
    assert doc.ok is False
    assert doc.sub == "Could not be read"


def test_the_sub_line_is_derived_from_meta_when_not_given() -> None:
    assert (
        SourceDoc(id="g", kind="github", label="GitHub", meta={"repos": 34, "languages": ["a", "b", "c"]}).sub
        == "34 repos · 3 languages"
    )
    assert (
        SourceDoc(id="g", kind="github", label="GitHub", meta={"publicRepos": 1, "languages": ["a"]}).sub
        == "1 repo · 1 language"
    )
    assert SourceDoc(id="r", kind="resume", label="Resume", meta={"pages": 2}).sub == "2 pages"
    assert SourceDoc(id="r", kind="resume", label="Resume", meta={"pages": 1}).sub == "1 page"
    assert SourceDoc(id="p", kind="portfolio", label="P", meta={"caseStudies": 5}).sub == "5 case studies"
    assert SourceDoc(id="p", kind="portfolio", label="P", meta={"caseStudies": 1}).sub == "1 case study"
    assert SourceDoc(id="n", kind="notes", label="Notes", kind_label="Extra context").sub == "Extra context"
    assert SourceDoc(id="n", kind="notes", label="Notes", sub="custom", meta={"pages": 9}).sub == "custom"


def test_a_failure_helper_builds_a_failed_doc() -> None:
    doc = SourceDoc.failure(
        "resume",
        "resume",
        "Resume",
        "Resume · PDF",
        None,
        "cv.pdf",
        "Could not read that PDF.",
        meta={"pages": 3},
    )
    assert doc.ok is False
    assert doc.error == "Could not read that PDF."
    assert doc.src == "cv.pdf"
    assert doc.meta == {"pages": 3}


def test_to_dict_is_json_ready_and_leaves_out_the_pdf_bytes() -> None:
    doc = SourceDoc(
        id="resume",
        kind="resume",
        label="Resume",
        url=None,
        text="hello",
        notes=["n"],
        meta={"pages": 1},
        src="cv.pdf",
        kind_label="Resume · PDF",
        pdf_bytes=b"%PDF",
    )
    data = doc.to_dict()
    assert data == {
        "id": "resume",
        "kind": "resume",
        "label": "Resume",
        "sub": "1 page",
        "ok": True,
        "text": "hello",
        "content": "hello",
        "notes": ["n"],
        "meta": {"pages": 1},
        "src": "cv.pdf",
        "kind_label": "Resume · PDF",
    }
    data["notes"].append("changed")
    assert doc.notes == ["n"]  # the dict holds copies of the lists


def test_to_dict_includes_url_and_error_only_when_present() -> None:
    data = SourceDoc(id="x", kind="link", label="L", url="https://example.com", error="bad").to_dict()
    assert data["url"] == "https://example.com"
    assert data["error"] == "bad"
    assert data["ok"] is False


# ----------------------------------------------------------------------------- shared helpers


def test_plural_is_the_core_helper() -> None:
    assert plural is text.plural
    assert (plural(1, "repo"), plural(2, "repo"), plural(0, "repo")) == ("1 repo", "2 repos", "0 repos")
    assert plural(2, "case study", "case studies") == "2 case studies"
    assert plural(1, "case study", "case studies") == "1 case study"


def test_the_package_exports_the_names_the_legacy_modules_import() -> None:
    import tayyari.ingest as ingest

    for name in (
        "MAX_LINKS",
        "TERM_COUNT",
        "ClassifiedUrl",
        "IngestLog",
        "PlannedSource",
        "ResumeError",
        "SourceDoc",
        "TermHit",
        "classify_link",
        "classify_url",
        "count_terms",
        "find_terms",
        "ingest_github",
        "ingest_notes",
        "ingest_planned",
        "ingest_resume",
        "ingest_sources",
        "ingest_web",
        "is_known_platform",
        "normalize_url",
        "parse_resume",
        "plan_sources",
        "usable_links",
    ):
        assert name in ingest.__all__
        assert hasattr(ingest, name)
