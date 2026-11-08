# SEO: crawlable pages, built at build time

Written 2026-10-19 from `SEO/SeoP1.md` (the finding: one HTML shell for every URL, one title, no canonical, no share tags, no sitemap, text only after JavaScript). The fix is multi-page output generated during the build, so a crawler gets real text, real titles and real links without running the app. Nothing here calls a model or the network, and nothing replaces an app route.

## What is generated

`npm run build`, `npm run build:static` and `npm run check:build` run `tsx scripts/build-pages.tsx` right after `vite build` (Vite empties `dist/` on every build, so the pages are rewritten each time). It writes, into `dist/`:

| File | What |
|---|---|
| `<slug>/index.html` for each `page` of `src/site/links.ts` | A full static HTML page: `lang="en-IN"`, title `"<Page title> · Leafout"`, meta description of at most 160 characters, `rel=canonical`, Open Graph (with the share picture) and Twitter `summary_large_image` tags, `theme-color` for light and dark, JSON-LD (the company's Organization, this host's WebSite, a WebPage with `dateModified`, a BreadcrumbList, a FAQPage, and SoftwareApplication on `/how-it-works`), the icon, apple-touch-icon and manifest links of `index.html`, and the Google Fonts link. A visible "Updated <date>" line. The footer links to every page and to the company site. It loads no app script, only `/site/site.css`. |
| `404.html` | The same frame with "That page does not exist" and a link to every page; `noindex`, no canonical. |
| `index.html` | The app shell for Home, with its title, description, canonical, share tags and JSON-LD replaced, plus a `<noscript>` body: the Home seed (`src/site/seeds/HomeSeed.tsx`: h1, what it is, who for, the flow, India first, what it costs, three questions, 150+ words) and a list of the site's pages and the company's. The app's script and stylesheet tags and the inline hue boot script are untouched. The seed is in `<noscript>`, not in `#root`, on purpose: this same file opens every route of the app (`/match`, `/practice`...), and nothing may flash there before the app mounts. |
| `pricing/index.html` | A copy of the app shell with the Pricing head and the Pricing seed (`seeds/PricingSeed.tsx`: free, the pack, the 30-day pass, what never costs, payment, refunds, delivery, five questions with FAQPage data). The app still runs there (its own router shows the pricing screen). **Prices are not in the file**: the seed holds the placeholders `{{price:pack3}}` and `{{price:pass30}}`, which `backend/tayyari/api/static.py` replaces on every response with the formatted rupee price, or "Coming soon" when none is set. |
| `app-shell.html` | The same app shell with no canonical, no structured data, no share tags and `<meta name="robots" content="noindex">`: what the private routes (`APP_ROUTES` with `private=True`) should be answered with, so they stop naming Home as their canonical. Until `static.py` serves it, nothing uses it. |
| `llms.txt` | What Leafout is, who it is for, a link and a line for every page and for the company site. Generated from `SITE_PAGES` and the pages' `blurb`s; no prices. |
| `site/site.css` | `src/theme/tokens.css`, `src/styles/global.css` and `src/site/site.css` joined into one file, comments removed, **not content-hashed**. Every colour is a token, so light and dark follow the tokens' own media queries. |
| `sitemap.xml` | Every `SITE_PAGES` path as an absolute URL, each with a `lastmod` when one is known: the date of the last commit that touched the page's own files (`PAGE_FILES` in `build-pages.tsx`), or the day the file was last written when it has uncommitted changes. No git, no date: a date is never made up. |
| `robots.txt` | Allow all, `Disallow: /api/`, `Disallow: /dev/`, the same rules repeated for a named group of AI crawlers (GPTBot, OAI-SearchBot, ChatGPT-User, ClaudeBot, Claude-SearchBot, Claude-User, PerplexityBot, Google-Extended, Applebot-Extended), and the `Sitemap:` line. `/p/` is deliberately not disallowed: a crawler must be able to fetch a share link to see its `X-Robots-Tag: noindex`. Cloudflare's "Block AI bots" setting overrides this file: keep it off. |

`public/` (copied into `dist/` by `vite build`) holds `og-default.png` (1200 x 630, the share picture: the quiet look's light field, the ring-and-dot mark and "Leafout", the tagline "See where you stand. Then where you could go." in Instrument Serif with the second sentence italic in accent ink as on Home, and "Career intelligence, built from your evidence" in Geist under it; no gradient, no shadow), `favicon.svg`, `favicon.ico`, `favicon.png`, `apple-touch-icon.png` (180), `icon-192.png`, `icon-512.png` and `site.webmanifest`. The icons are drawn with `resvg-py` and the bundled Geist. The share picture was redrawn 2026-11-08 with Pillow from the bundled Geist (`backend/tayyari/card/fonts/`) and the company site's Instrument Serif files (`public/fonts/` there, converted from WOFF2); the company site still serves the earlier picture with the old line until this file is copied over. `theme-color` comes from the quiet look's `--bg` in `tokens.css` (light and dark).

The address in canonical links, share tags, structured data and the sitemap is the `SITE_URL` environment variable when it is an http(s) address, else `DEFAULT_SITE_URL` in `links.ts` (`https://app.leafout.in`). Set it when building for another host.

**Two properties, one company.** leafout.in (the company site) owns the one Organization: `@id` `https://leafout.in/#organization`, with name, url, logo and email (`ORG_ID` and `organizationLd()` in `seo.ts`). Each host keeps its own WebSite, published by that `@id`. Every page links to the company site (`COMPANY_LINKS` in `links.ts`): the website, blog, privacy, terms, refunds and contact pages live there, not in the app. The support address and the operator line are in `links.ts` too.

What the script adds to the two app shells sits between `<!--seo:begin-->` and `<!--seo:end-->` (head) and `<!--seo:noscript:begin-->` and `<!--seo:noscript:end-->` (body). Running it again replaces the blocks instead of stacking them.

## Where the code is

```
src/site/links.ts        the page list: path, label, kind (page | app), inNav
src/site/types.ts        ContentPage
src/site/seo.ts          site line, Home and Pricing heads, JSON-LD builders, SITE_URL resolution
src/site/Layout.tsx      ambient field, skip link, header, footer (lists every page)
src/site/Article.tsx     breadcrumb, h1, lede, the one call to action, sections, FAQ, three related pages
src/site/NotFound.tsx    the 404 body
src/site/pages/*.tsx     one module per content page; pages/index.ts lists them
src/site/site.css        the page styles (tokens only; checked by check:palette and check:restraint)
scripts/build-pages.tsx  the generator
scripts/build-check.ts   fails the build when any of the above is missing or wrong
```

The components render with `renderToStaticMarkup`: no store, no browser APIs, no effects.

## Adding a page

1. Add `{ path, label, kind: 'page', inNav }` to `SITE_PAGES` in `src/site/links.ts`. Paths are permanent URLs: do not rename one without a redirect.
2. Write `src/site/pages/<Name>.tsx` exporting a `ContentPage`: a short title (the brand is appended), a description of at most 160 characters that no other page uses, an h1, a lede, a `blurb` (shown on other pages' Related cards), one call to action into the app, three to five FAQ entries, exactly three related paths, and a `Body` of `<section>` elements that each open with an h2. Aim for 500 to 900 words of plain, specific text.
3. Add it to `CONTENT_PAGES` in `src/site/pages/index.ts`, and its module to `PAGE_FILES` in `scripts/build-pages.tsx` (that is where its `lastmod` comes from).
4. Run `npm run check`. The generator refuses a build where `links.ts` and the modules disagree, a description is too long or repeated, a related path is wrong, or the FAQ has fewer than three or more than five entries; `build-check` then verifies the file.

Rules for the text, which are the product's rules (`project-soul.md`): every fact comes from the docs in this repository or from the app itself; no invented numbers, user counts, testimonials, company names or outcomes; no percentage "match" or "ATS" claim; no hype words. The FAQ text is shown on the page and put in the FAQPage data word for word, and `build-check` proves they match.

A page for an app route (like Pricing) is `kind: 'app'`: it gets a head from `seo.ts` and a shell copy in `build-pages.tsx`, not a module.

## What `build-check` proves

For every content page: the file exists; exactly one h1; a canonical link equal to the expected URL; a title; a description of 1 to 160 characters; Open Graph and Twitter tags that agree with them; Organization, WebSite, BreadcrumbList and FAQPage data (SoftwareApplication on `/how-it-works`); every FAQ question and answer present in the visible text; at least 400 words; the footer line and a link to every page; every internal link a known path; no `<script src>`; a title and description no other page shares. Also: the share picture tags (absolute `og:image`, 1200 x 630, alt, `twitter:image`, `summary_large_image`), the icon, apple-touch-icon, manifest and `theme-color` tags, one Organization with the company's `@id` and a WebSite published by it, the footer's company links and operator line, an "Updated" line that matches the WebPage `dateModified`, no price placeholder on a content page, and no text that calls the radar card unbuilt. `404.html`, the Home and Pricing shells (own head, still the app, one copy of each generated block, boot script before the first stylesheet, a no-script seed of 150+ words with one h1, the price placeholders in Pricing only and never in a head tag, the Pricing FAQ data matching its text and naming the 7-day refund and "nothing auto-renews"), `app-shell.html` (noindex, no canonical, no data), the files of `public/` (sizes, manifest), `sitemap.xml` (exactly the `SITE_PAGES` URLs, `lastmod` well-formed), `robots.txt` (with the AI group), `llms.txt`, and `site/site.css` (tokens present, no `box-shadow`).

## What the server must do (not part of this change)

`dist/` is copied to `backend/static/` by `backend/build-static.mjs`. The files above are real files, so `sitemap.xml`, `robots.txt` and `site/site.css` are served as they are. A page is a directory, though: `GET /how-it-works` must be answered with `how-it-works/index.html` (and `/how-it-works/` the same), and any path that is neither a file, a page nor an app route should answer `404.html` with status 404. Until `backend/tayyari/api/static.py` does that, an unknown path still gets the app shell with 200 (the soft-404 problem from `SEO/SeoP1.md`).

The CSP hashes every inline `<script>` of `static/index.html`, computed from the file when the server starts and again when it changes (`PageCsp` in `security.py`), so no hash is kept by hand. A data block (JSON-LD) is skipped. The one inline script is the hue and boot script: `calmBoot()` in `vite.config.ts` minifies it (comments and whitespace out) and puts it right after `<meta charset>`. The content pages have no inline script and need no hash.

**To do in `static.py`** (not part of the build): replace `{{price:pack3}}` and `{{price:pass30}}` in `pricing/index.html` as it is served; answer the private routes with `app-shell.html`; send `X-Robots-Tag: noindex` on `/r/*.svg` and `/og/*.png`.

## Shared radar cards (`/u/<slug>`)

A person who shares their radar card (R1, `docs/backend.md` "The radar card") gets a page at `/u/<slug>`, rendered by the
API (`backend/tayyari/card/page.py`), not by this build. It links `/site/site.css` and the same Google Fonts, carries
`og:title` "Strongest in {area}", `og:image` the versioned 1200 x 627 PNG (`/og/<slug>-<version>.png`) and
`twitter:card summary_large_image`, so a link posted on LinkedIn shows the large picture. It is **not indexed** for now:
`<meta name="robots" content="noindex">` and `X-Robots-Tag: noindex` (the user's decision of 2026-10-23, to revisit once there
are cards to judge by). It is not in the sitemap, and `robots.txt` does not disallow `/u/`, so a crawler can read the
`noindex`. The site's own pages carry the same share picture as everything else (`/og-default.png`).

## Not done yet

- **Per-profile or per-role pages.** Nothing about a person is public except a published portfolio (`/p/*`, `noindex` by design). Pages for roles or skills would need a source of honest text; none exists yet.
- **A blog or guides**, and any page that targets a search phrase with text the product cannot back.
- **Redirects and trailing-slash policy.** Canonicals have no trailing slash; the server decides whether `/how-it-works/` redirects.
- **`hreflang`** (the site is one language, English; content pages say `en-IN`).
- **Offer data for Pricing.** The prices are server settings, so the static data states none; add `Offer` only when the visible text and the data can come from the same source.
- **Search Console and Bing Webmaster verification**, and a check of production headers with a real crawl (`SEO/SeoP1.md` marks this unverified).
- **Content for the app's own routes** (`/match`, `/practice`, `/discover` need a sign-in or a profile, so they stay out of the sitemap).
- **Hue boot script on the static pages.** They hold the tokens' default hue and follow the device's light or dark setting; the Atmosphere choices the app stores in the browser are not read there, because an extra inline script would need a CSP hash on every page.
