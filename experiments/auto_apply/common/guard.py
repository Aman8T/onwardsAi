"""The submit guard: nothing an experiment run does can send an application.

Layer 1, every arm: a Manifest V3 extension (declarativeNetRequest) loaded into the Browser Use browser. It blocks
  - every non-GET request (POST, PUT, PATCH, DELETE, other) for every resource type, including top-level form posts,
    fetch/XHR, beacons and pings, so no form, autosave or resume upload can leave the browser;
  - every request to CAPTCHA and bot-challenge providers, so a CAPTCHA is never loaded and therefore never solved
    (Browser Use's automatic solver cannot be switched off for V4 agent runs). A form that needs one fails, as the
    brief requires.
Layer 2, arms B and C (we drive Playwright): a `context.route` handler with the same rules.
`probe()` proves layer 1 is live in a page: a no-cors POST to example.com must fail.
"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from typing import Any
from urllib.parse import urlsplit

ALL_TYPES = ["main_frame", "sub_frame", "stylesheet", "script", "image", "font", "object", "xmlhttprequest", "ping",
             "csp_report", "media", "websocket", "webtransport", "webbundle", "other"]  # fmt: skip
CAPTCHA_DOMAINS = ["recaptcha.net", "hcaptcha.com", "challenges.cloudflare.com", "arkoselabs.com", "funcaptcha.com",
                   "geetest.com", "captcha-delivery.com", "mtcaptcha.com", "friendlycaptcha.com", "frcapi.com"]  # fmt: skip
CAPTCHA_PATHS = ["||google.com/recaptcha", "||gstatic.com/recaptcha", "||www.google.com/recaptcha"]
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

# Read-only POSTs a form needs just to render, allowed by exact operation name after inspection (inspect_page.py
# --posts). Ashby loads the posting and its form through GraphQL POSTs. Submit/upload operations are NOT listed and
# stay blocked.
READ_ONLY_POSTS = [
    r"^https://jobs\.ashbyhq\.com/api/non-user-graphql\?op=(ApiJobPosting|ApiOrganizationFromHostedJobsPageName)$",
]

MANIFEST = {
    "manifest_version": 3,
    "name": "Onwards experiment submit guard",
    "version": "1.0.0",
    "description": "Blocks every non-GET request and CAPTCHA providers. Test runs only: nothing can be submitted.",
    "permissions": ["declarativeNetRequest"],
    "declarative_net_request": {"rule_resources": [{"id": "guard", "enabled": True, "path": "rules.json"}]},
}


def rules() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = [
        {"id": 1, "priority": 1, "action": {"type": "block"},
         "condition": {"requestMethods": ["post", "put", "patch", "delete", "other"], "resourceTypes": ALL_TYPES}},
        {"id": 2, "priority": 1, "action": {"type": "block"},
         "condition": {"requestDomains": CAPTCHA_DOMAINS, "resourceTypes": ALL_TYPES}},
    ]  # fmt: skip
    for number, path in enumerate(CAPTCHA_PATHS, start=3):
        out.append({"id": number, "priority": 1, "action": {"type": "block"},
                    "condition": {"urlFilter": path, "resourceTypes": ALL_TYPES}})  # fmt: skip
    for number, regex in enumerate(READ_ONLY_POSTS, start=50):
        out.append({"id": number, "priority": 2, "action": {"type": "allow"},
                    "condition": {"regexFilter": regex, "requestMethods": ["post"],
                                  "resourceTypes": ["xmlhttprequest", "other"]}})  # fmt: skip
    return out


def read_only_post(url: str) -> bool:
    import re

    return any(re.match(rx, url) for rx in READ_ONLY_POSTS)


def extension_zip() -> tuple[bytes, str]:
    """The extension as a ZIP (manifest.json at its root) and a short hash of its content."""
    manifest = json.dumps(MANIFEST, indent=2).encode()
    rule_file = json.dumps(rules(), indent=2).encode()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", manifest)
        zf.writestr("rules.json", rule_file)
    digest = hashlib.sha256(manifest + rule_file).hexdigest()[:16]
    return buf.getvalue(), digest


def is_captcha_url(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    if any(host == d or host.endswith("." + d) for d in CAPTCHA_DOMAINS):
        return True
    return "/recaptcha/" in url and (host.endswith("google.com") or host.endswith("gstatic.com"))


def install_route_guard(context: Any, blocked: list[dict[str, str]]) -> None:
    """Layer 2 (Playwright): abort non-GET requests and CAPTCHA hosts; record what was blocked (method + host only)."""

    def handler(route: Any, request: Any) -> None:
        method, url = request.method.upper(), request.url
        if (method not in SAFE_METHODS and not (method == "POST" and read_only_post(url))) or is_captcha_url(url):
            blocked.append({"method": method, "host": urlsplit(url).hostname or "", "layer": "route"})
            route.abort("blockedbyclient")
            return
        route.continue_()

    context.route("**/*", handler)


PROBE_JS = """async () => {
  try { await fetch('https://example.com/onwards-guard-probe', {method: 'POST', body: 'probe', mode: 'no-cors'});
        return 'SENT'; }
  catch (e) { return 'BLOCKED'; }
}"""


def probe(page: Any) -> str:
    """'BLOCKED' when the extension stops a POST from this page; 'SENT' would mean the guard is not active."""
    try:
        return str(page.evaluate(PROBE_JS))
    except Exception as exc:  # the page may be navigating or closed
        return f"ERROR:{type(exc).__name__}"
