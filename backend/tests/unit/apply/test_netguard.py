"""The guard's pure parts: the extension's rules (both variants), the ZIP, and layer 2's `decide` table."""

from __future__ import annotations

import io
import json
import re
import zipfile
from typing import Any

import pytest

from tayyari.apply import netguard
from tayyari.apply.netguard import (
    CAPTCHA_DOMAINS,
    PROBE_URL,
    decide,
    extension_zip,
    is_captcha_url,
    read_only_post,
    rules,
    site_of,
)

GH = "https://job-boards.greenhouse.io/acme/jobs/123"
GH_SITE = "greenhouse.io"
WRITE_METHODS = ("POST", "PUT", "PATCH", "DELETE", "CONNECT", "TRACE", "PROPFIND", "post")
ASHBY_GQL = "https://jobs.ashbyhq.com/api/non-user-graphql?op="


def by_id(found: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    return {rule["id"]: rule for rule in found}


# ── the extension's rules ──


def test_rule_ids_and_priorities() -> None:
    found = by_id(rules())
    assert found[1]["action"] == {"type": "block"} and found[1]["priority"] == netguard.PRIORITY_WRITES == 1
    assert found[2]["condition"]["requestDomains"] == list(CAPTCHA_DOMAINS) and found[2]["priority"] == 3
    paths = [found[i]["condition"]["urlFilter"] for i in (3, 4, 5)]
    assert paths == list(netguard.CAPTCHA_PATHS) and all(found[i]["priority"] == 3 for i in (3, 4, 5))
    assert found[50]["action"] == {"type": "allow"} and found[50]["priority"] == 2
    assert found[50]["condition"]["requestMethods"] == ["post"]
    # CAPTCHA blocks outrank the read-only allowances, which outrank the write block
    assert netguard.PRIORITY_CAPTCHA > netguard.PRIORITY_READ_ONLY > netguard.PRIORITY_WRITES
    assert len({rule["id"] for rule in rules()}) == len(rules())


def test_preview_blocks_every_write_for_every_resource_type() -> None:
    writes = by_id(rules())[1]["condition"]
    assert writes["excludedRequestMethods"] == [
        "get",
        "head",
        "options",
    ]  # so CONNECT, TRACE and "other" are blocked
    assert "requestMethods" not in writes and "excludedRequestDomains" not in writes
    assert set(writes["resourceTypes"]) >= {
        "main_frame",
        "sub_frame",
        "xmlhttprequest",
        "ping",
        "other",
        "websocket",
    }


def test_submit_variant_allows_writes_to_its_hosts_only_and_still_blocks_captcha() -> None:
    found = by_id(
        rules(allow_write_hosts=["Boards.Greenhouse.io", "s3.amazonaws.com", "boards.greenhouse.io"])
    )
    assert found[1]["condition"]["excludedRequestDomains"] == ["boards.greenhouse.io", "s3.amazonaws.com"]
    assert found[1]["action"] == {"type": "block"}
    assert found[2] == by_id(rules())[2]  # the CAPTCHA block is the same in both variants


@pytest.mark.parametrize(
    "bad", ["hcaptcha.com", "x.recaptcha.net", "example.com", "https://acme.com", "a b", ""]
)
def test_a_write_host_is_never_a_captcha_provider_the_probe_host_or_not_a_host(bad: str) -> None:
    with pytest.raises(ValueError):
        rules(allow_write_hosts=[bad])


def test_ashby_read_only_operations_are_allowed_and_submit_and_upload_are_not() -> None:
    for op in ("ApiJobPosting", "ApiOrganizationFromHostedJobsPageName"):
        assert read_only_post(ASHBY_GQL + op)
    for op in ("ApiSubmitSingleApplicationFormAction", "ApiCreateFileUploadHandle", "ApiJobPostingX", ""):
        assert not read_only_post(ASHBY_GQL + op)
    assert not read_only_post("https://evil.example/api/non-user-graphql?op=ApiJobPosting")
    regex = by_id(rules())[50]["condition"]["regexFilter"]
    assert re.match(regex, ASHBY_GQL + "ApiJobPosting") and not re.match(
        regex, ASHBY_GQL + "ApiSubmitSingleApplicationFormAction"
    )
    assert read_only_post("https://acme.test/form?op=Load", extra=[r"^https://acme\.test/form\?op=Load$"])


def test_the_zip_holds_the_manifest_and_rules_and_is_deterministic() -> None:
    data, digest = extension_zip()
    again, same = extension_zip()
    submit, other = extension_zip(allow_write_hosts=["boards.greenhouse.io"])
    assert data == again and digest == same and len(digest) == 16
    assert other != digest and submit != data
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        assert sorted(zf.namelist()) == ["manifest.json", "rules.json"]
        manifest = json.loads(zf.read("manifest.json"))
        assert manifest["name"] == "Tayyari guard" and manifest["manifest_version"] == 3
        assert manifest["permissions"] == ["declarativeNetRequest"]
        assert json.loads(zf.read("rules.json")) == json.loads(json.dumps(rules()))


# ── CAPTCHA and sites ──


@pytest.mark.parametrize(
    ("url", "captcha"),
    [
        ("https://www.google.com/recaptcha/api.js", True),
        ("https://www.gstatic.com/recaptcha/releases/x/recaptcha__en.js", True),
        ("https://js.hcaptcha.com/1/api.js", True),
        ("https://challenges.cloudflare.com/turnstile/v0/api.js", True),
        ("https://client-api.arkoselabs.com/v2/x", True),
        ("https://www.google.com/search?q=recaptcha", False),
        ("https://boards.greenhouse.io/recaptcha-team/jobs/1", False),
        ("https://notgstatic.com/recaptcha/x", False),
    ],
)
def test_is_captcha_url(url: str, captcha: bool) -> None:
    assert is_captcha_url(url) is captcha


def test_site_of() -> None:
    assert site_of("https://job-boards.greenhouse.io/acme/jobs/1") == "greenhouse.io"
    assert site_of("boards.greenhouse.io") == "greenhouse.io"
    assert site_of("https://careers.acme.co.in/apply") == "acme.co.in"
    assert site_of("https://jobs.lever.co/acme/1/apply") == "lever.co"
    assert site_of("lever.co") == "lever.co"


# ── decide ──


def ask(
    method: str, url: str, *, nav: bool = False, hosts: tuple[str, ...] = (), site: str | None = GH_SITE
) -> str:
    return decide(method, url, is_navigation=nav, site=site, write_hosts=hosts)


@pytest.mark.parametrize("method", WRITE_METHODS)
def test_preview_blocks_every_write_method_anywhere(method: str) -> None:
    for url in (
        GH,
        "https://boards-api.greenhouse.io/v1/applications",
        "https://www.google-analytics.com/collect",
    ):
        assert ask(method, url) == "block"
        assert ask(method, url, site=None) == "block"


@pytest.mark.parametrize("method", ["GET", "HEAD", "OPTIONS", "get"])
def test_reads_pass(method: str) -> None:
    assert ask(method, GH) == "allow"
    assert ask(method, "https://cdn.example.net/app.js") == "allow"


def test_captcha_is_blocked_in_both_variants_even_for_a_write_host() -> None:
    for hosts in ((), ("www.google.com", "hcaptcha.com")):
        assert ask("GET", "https://www.google.com/recaptcha/api.js", hosts=hosts) == "block"
        assert ask("GET", "https://js.hcaptcha.com/1/api.js", hosts=hosts) == "block"
        assert ask("POST", "https://api.hcaptcha.com/checksiteconfig", hosts=hosts) == "block"


def test_writes_go_only_to_the_write_hosts() -> None:
    hosts = ("boards.greenhouse.io",)
    assert ask("POST", "https://boards.greenhouse.io/acme/jobs/1/applications", hosts=hosts) == "allow"
    assert (
        ask("PUT", "https://uploads.boards.greenhouse.io/x", hosts=hosts) == "allow"
    )  # subdomains, as DNR does
    assert ask("POST", "https://www.google-analytics.com/collect", hosts=hosts) == "block"
    assert ask("POST", "https://greenhouse.io.evil.example/x", hosts=hosts) == "block"
    assert ask("POST", "https://notboards.greenhouse.io/x", hosts=hosts) == "block"


def test_off_site_main_frame_navigation_is_blocked() -> None:
    assert ask("GET", "https://evil.example/phish", nav=True) == "block"
    assert (
        ask("GET", "https://boards.greenhouse.io/acme/jobs/1", nav=True) == "allow"
    )  # same site, other host
    assert ask("GET", "https://evil.example/pixel.gif") == "allow"  # a sub-resource, not a navigation
    assert (
        ask("GET", "https://evil.example/phish", nav=True, site=None) == "allow"
    )  # before the code opened a page
    # a write host counts as on-site for the submit's own navigation
    assert ask("POST", "https://submit.ats.example/x", nav=True, hosts=("submit.ats.example",)) == "allow"


def test_the_probe_passes_layer_two_and_nothing_else_does() -> None:
    assert ask("POST", PROBE_URL) == "allow"
    assert ask("GET", PROBE_URL) == "allow"
    assert ask("PUT", PROBE_URL) == "block"
    assert ask("POST", PROBE_URL + "?x=1") == "block"
    assert ask("POST", "https://example.com/other") == "block"


def test_read_only_posts_pass_and_other_posts_on_the_same_host_do_not() -> None:
    assert ask("POST", ASHBY_GQL + "ApiJobPosting", site="ashbyhq.com") == "allow"
    assert ask("POST", ASHBY_GQL + "ApiSubmitSingleApplicationFormAction", site="ashbyhq.com") == "block"
    assert ask("PUT", ASHBY_GQL + "ApiJobPosting", site="ashbyhq.com") == "block"
    extra = (r"^https://acme\.test/load$",)
    assert decide(
        "POST", "https://acme.test/load", is_navigation=False, site=None, write_hosts=(), read_only=extra
    ) == ("allow")


@pytest.mark.parametrize(
    ("method", "url", "verdict"),
    [
        ("GET", "data:text/plain,hi", "allow"),
        ("GET", "blob:https://x/1", "allow"),
        ("POST", "data:text/plain,hi", "block"),
        ("GET", "ftp://files.example/x", "block"),
        ("GET", "file:///etc/passwd", "block"),
        ("GET", "chrome://settings", "block"),
        ("GET", "not a url", "block"),
    ],
)
def test_odd_schemes(method: str, url: str, verdict: str) -> None:
    assert ask(method, url) == verdict


# ── sockets and write hosts ──


def test_sockets_are_blocked_for_every_method_in_both_variants() -> None:
    preview = by_id(rules())[netguard.RULE_SOCKETS]
    assert preview["action"] == {"type": "block"} and preview["priority"] == netguard.PRIORITY_CAPTCHA
    assert preview["condition"] == {"resourceTypes": ["websocket", "webtransport"]}  # no method condition
    submit = by_id(rules(allow_write_hosts=["boards.greenhouse.io"]))[netguard.RULE_SOCKETS]
    assert submit["condition"]["excludedRequestDomains"] == ["boards.greenhouse.io"]


@pytest.mark.parametrize(
    ("url", "hosts", "verdict"),
    [
        ("wss://ws.hotjar.com/api/v2/client/ws", (), "block"),
        ("wss://boards.greenhouse.io/socket", (), "block"),
        ("wss://boards.greenhouse.io/socket", ("boards.greenhouse.io",), "allow"),
        ("wss://ws.hotjar.com/api", ("boards.greenhouse.io",), "block"),
        ("wss://x.hcaptcha.com/ws", ("hcaptcha.com",), "block"),
        ("https://boards.greenhouse.io/socket", ("boards.greenhouse.io",), "block"),
        ("not a url", ("boards.greenhouse.io",), "block"),
    ],
)
def test_socket_decision(url: str, hosts: tuple[str, ...], verdict: str) -> None:
    assert netguard.socket_decision(url, write_hosts=hosts) == verdict


@pytest.mark.parametrize("bad", ["co.uk", "com", "hcaptcha.com", "example.com", "https://acme.com", "a b"])
def test_norm_write_hosts_refuses_suffixes_and_non_hosts(bad: str) -> None:
    with pytest.raises(ValueError):
        netguard.norm_write_hosts([bad])
    assert netguard.norm_write_hosts(["Boards.Greenhouse.io."]) == ("boards.greenhouse.io",)
