# SEO Finding 1: what search engines see today

Date: 2026-10-17. Method: read `index.html` and `backend/tayyari/api/static.py`. No live crawl yet, so treat as unconfirmed against production.

## Finding
Tayyari is a client-rendered SPA. Every path that is not a real file returns the same `index.html` (200, `no-cache`), so there is effectively one indexable page.

- `index.html` has a `<title>` and one `<meta description>` ("See where you stand. Then where you could go."). No canonical, no Open Graph or Twitter tags, no structured data.
- No `robots.txt` or `sitemap.xml` handling found in `static.py`. Unknown paths such as `/robots.txt` return the SPA shell with 200, which crawlers can misread.
- Share links are deliberately `X-Robots-Tag: noindex` (good, keep).
- Unknown paths return 200 with the home shell, so soft-404s are likely.
- Content only exists after JavaScript runs; there are no crawlable text pages about roles, skills or how the product works.

## Impact
No organic discovery beyond the brand name. Every acquisition page we want ("resume for X", "match a job posting") does not exist as a URL.

## Options
1. Build indexable pages inside this app (prerender or static routes). Cost: build tooling, backend route work.
2. Put marketing site and blog on a separate stack (Lovable) at a subdomain or the root domain, and route interested visitors into the app. No change to the SPA's rendering. See discussion.

## Minimum fixes either way
- Serve a real `robots.txt` and `sitemap.xml` (and 404 for unknown paths that are not app routes).
- Add canonical, OG and Twitter tags and a social image to `index.html`.
- Connect Google Search Console and verify the domain.

## Not verified
Production headers and what Googlebot renders. Do a live check (`curl` with a custom User-Agent, Search Console URL inspection) before acting.
