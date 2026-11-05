"""Debug helper: open a job's form in a guarded browser and print how file inputs, comboboxes and option lists are built.
Read-only, never submits. Usage: uv run python inspect_page.py <label>"""

from __future__ import annotations

import json
import sys

from playwright.sync_api import sync_playwright

from common import bu, spend
from common.env import load_env

JS = r"""() => {
  const files = [...document.querySelectorAll('input[type=file]')].map(f => ({id: f.id, name: f.name,
     accept: f.accept, hidden: getComputedStyle(f).display, parent: (f.parentElement.outerHTML || '').slice(0, 300)}));
  const resumeBlock = [...document.querySelectorAll('*')].find(e => /^resume\/?cv/i.test((e.innerText||'').trim()) && e.children.length < 4);
  const combos = [...document.querySelectorAll('[role=combobox]')].slice(0, 2).map(c => (c.closest('[class*=container]') || c.parentElement).outerHTML.slice(0, 700));
  const hiddenReq = [...document.querySelectorAll('input[aria-hidden=true], input[tabindex="-1"]')].slice(0, 2).map(i => i.outerHTML.slice(0, 250));
  return {files, resumeBlock: resumeBlock ? resumeBlock.parentElement.outerHTML.slice(0, 1500) : null, combos, hiddenReq,
          shadowHosts: [...document.querySelectorAll('*')].filter(e => e.shadowRoot).length};
}"""


def main() -> int:
    load_env()
    spend.check(bu_estimate=0.002)
    label = sys.argv[1]
    job = {j["label"]: j for j in json.load(open("jobs.json", encoding="utf-8"))}[label]
    c = bu.client()
    ext = bu.ensure_guard_extension(c)
    bid = None
    with bu.standalone_browser(c, ext, minutes=3, label=f"inspect-{label}") as b:
        bid = str(b.id)
        with sync_playwright() as p:
            browser = bu.connect(p, b.cdp_url)
            page = browser.contexts[0].pages[0]
            posts: list[str] = []
            if "--posts" in sys.argv:
                from urllib.parse import parse_qs, urlsplit

                cdp = browser.contexts[0].new_cdp_session(page)

                def on_req(params: dict) -> None:
                    req = params["request"]
                    if req["method"] != "GET":
                        u = urlsplit(req["url"])
                        op = parse_qs(u.query).get("op", [""])[0]
                        body_op = ""
                        if req.get("postData") and '"operationName"' in req["postData"]:
                            try:
                                body_op = json.loads(req["postData"]).get("operationName", "")
                            except ValueError:
                                body_op = "?"
                        posts.append(f"{req['method']} {u.hostname}{u.path} op={op or body_op}")

                cdp.on("Network.requestWillBeSent", on_req)
                cdp.send("Network.enable")
            page.goto(job["apply_url"], wait_until="domcontentloaded", timeout=45_000)
            page.wait_for_timeout(6000)
            if "--posts" in sys.argv:
                print("non-GET requests the page attempted (the guard blocks them):")
                for line in sorted(set(posts)):
                    print("  ", line)
                print("page text:", page.inner_text("body")[:300].replace("\n", " "))
            elif "--upload" in sys.argv:
                from common.guard import install_route_guard
                from common.profile import RESUME_NAME
                from common.resume import build_resume

                blocked: list[dict[str, str]] = []
                install_route_guard(browser.contexts[0], blocked)
                sel = sys.argv[3]
                page.locator(sel).set_input_files(files=[{"name": RESUME_NAME, "mimeType": "application/pdf",
                                                          "buffer": build_resume().read_bytes()}])  # fmt: skip
                page.wait_for_timeout(4000)
                html = page.evaluate("""(s) => { const el = document.querySelector(s);
                    const box = (el || document.querySelector('[class*=file-upload], [class*=upload]'));
                    let p = el ? el.parentElement : null; for (let i = 0; i < 4 && p; i++) p = p.parentElement;
                    return {input_still_there: !!el, files: el && el.files ? el.files.length : null,
                            block: (p || box || document.body).outerHTML.slice(0, 1800)}; }""", sel)
                print(json.dumps(html, indent=1))
                print("blocked requests:", sorted({f"{r['method']} {r['host']}" for r in blocked}))
            elif "--scan" in sys.argv:
                from common.formscan import scan_page

                got = scan_page(page)
                print("frames:", [f.url[:60] for f in page.frames])
                for f in got["fields"]:
                    print(f"  [{f['type']}] req={f['required']} {f['label'][:50]!r} sel={f['selector'][:40]}")
                raw = page.evaluate("() => [...document.querySelectorAll('input[type=file]')].map(e => e.id)")
                print("raw file inputs:", raw)
                combo = page.locator(sys.argv[3]) if len(sys.argv) > 3 else None
                if combo is not None and combo.count():
                    combo.first.focus()
                    page.keyboard.type(sys.argv[4] if len(sys.argv) > 4 else "Ind", delay=40)
                    page.wait_for_timeout(900)
                    opts = page.locator('[role=option]')
                    print("options after typing:", opts.count(), opts.all_inner_texts()[:5])
            else:
                print(json.dumps(page.evaluate(JS), indent=1)[:6000])
            browser.close()
    cost = bu.browser_cost(c, bid)
    spend.record({"kind": "inspect", "label": label, "browser_use_usd": cost["browser"] + cost["proxy"], "anthropic_usd": 0})
    print("cost", cost)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
