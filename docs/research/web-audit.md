# Web audit: leafout.in and app.leafout.in (2026-11-04)

**Scope:** launch readiness, SEO, AEO (being quoted by ChatGPT, Perplexity, Google AI Overviews and Claude), mobile, and willingness to pay as the pages present it.

**Method:** five auditors, each tracing their findings to evidence:
- site SEO/AEO;
- app SEO/AEO;
- mobile, checked live in the browser at 360, 375 and 768 px;
- a curl crawl of both hosts;
- pricing and trust.

A skeptic re-checked every finding: 89 survived and 2 were refuted. Six fixers then worked on separate sets of files, followed by a reviewer and a full gate per repo. All of this is uncommitted.

## What changed

### leafout.in (site repo `tayyari-career-intelligence`)

**Search and answer engines:**
- **Titles and the home page:**
  - The home title names the category.
  - A plain sentence under the h1 says what Leafout is.
  - A cost line ("free to start") links to the app's pricing.
- **New pages:**
  - `/faq` has 10 answer-first questions, with FAQPage data built from the same text.
  - `/llms.txt` is generated from the posts and the page list.
- **robots.txt:** it names the AI crawlers. It no longer disallows `/signin` or `/account`, so their noindex can be read.
- **Structured data:**
  - One Organization (`https://leafout.in/#organization`) that the app also references.
  - SoftwareApplication with a free offer on the home page.
  - BlogPosting and breadcrumbs on posts.
- **Sitemap:** lastmod dates, and thin tag pages are now noindex.
- **Head tags:** `lang="en-IN"`, `og:locale`, theme-color, a web manifest and `favicon.ico`.
- **404s:** they have their own head instead of a Lovable image.
- **/about:** it no longer says "Leafout means preparation".

**Trust and payments:**
- **New pages:**
  - `/refunds` covers refunds, cancellation and delivery: a full refund within 7 days if no paid posting was used, an unreadable posting's credit comes back by itself, nothing renews, and delivery is digital.
  - `/contact` gives aman@leafout.in and "Leafout is run by Aman Sharma, India."
- **Privacy and terms:** they now cover the app too, including processors and retention.
- **Footer:** Product, Leafout and Legal columns, with the operator line.

**Mobile:**
- **Overflow:** nothing scrolls sideways at 360 px. The radar's screen-reader tables are contained, and the match card was fixed for tablets.
- **Header:** two rows, 92 px at 360. It was three rows and 140 px.
- **Inputs and targets:** inputs are 16 px (no iOS zoom), and tag chips are 44 px.
- **Weekly form:** it asked for an email it never used. It now opens the Weekly in the app.
- **Broken link:** the landing's `/prepare` link went to a 404 and is fixed.

**Share image:** the same layout with the ring-and-dot mark, now reading "Leafout" (`og-default.png?v=2`).

### app.leafout.in (this repo)

**Search and answer engines:**
- **Pricing for crawlers:** `/pricing` has a crawlable body and a FAQ, with `{{price:pack3}}` and `{{price:pass30}}` filled in by `api/static.py` from the live settings. A price of 0 shows "Coming soon".
- **Home:** it has readable text without JavaScript.
- **Share tags and icons:**
  - Pages now have `og:image` and `summary_large_image`.
  - There is a new `og-default.png` (1200×630).
  - New icons: favicon.ico/svg, apple-touch-icon and a manifest.
- **Crawler files:** `llms.txt` is new, and `robots.txt` names the AI crawlers.
- **Entity and links:** app pages reference the site's Organization, and the footer links to leafout.in's privacy, terms, refunds and contact pages.
- **Index hygiene:**
  - Private routes drop the canonical.
  - Unknown `/p/` slugs return 404.
  - Unshared card images are noindex.
  - The old host's moving page is limited to browsers.
- **Caching:** hashed `assets/` are `immutable` for a year; HTML stays no-cache.
- **The stale check:** `scripts/build-check.ts` no longer expects a bare `<html lang="en">`, so `npm run check` passes again.

**Rename:** "Tayyari" was replaced by "Leafout" in user-visible strings:
- the Razorpay checkout name and order descriptions;
- the sign-in email;
- error messages and export names;
- the coach persona;
- the calendar PRODID;
- the crawler User-Agent.

The package, the `TAYYARI_*` settings, tables and storage keys keep their names.

**Pricing page:**
- **Features:** Practice, Prepare and the radar card are listed as live and free. The "public portfolio page" is no longer sold.
- **Free tier:** the free posting goes all the way to a resume and cover note, and it is described one way everywhere ("one posting a month").
- **Plan wording:** prices say "once", "no renewal" and the per-posting cost. The pass is no longer called "Monthly".
- **Notes:** students, payment by UPI/cards through Razorpay, and refunds and delivery.

**Mobile:**
- **Inputs and targets:** fields are 16 px, and the brand, Atmosphere, radar axis, chips and sheet close buttons are 44 px.
- **Headers:** the Atmosphere control is hidden in sub-screen headers on phones.
- **Phone details:** sheets use `dvh`, and the "press F" hint is gone on touch screens.

## Verified

**Gates:**
- **App:** `npm run check:all` exit 0 (typecheck, palette, restraint, build + build-check, the three parities, pytest, ruff, mypy).
- **Site:**
  - `tsc --noEmit` clean, vitest 10/10 and `vite build` OK.
  - ESLint: about 8,300 messages, nearly all CRLF line endings from the Windows checkout. The other 19 are in files no fix touched.

**Local runs:**
- **App** (`npm run build:static`, served on :8787):
  - `/pricing` shows ₹149/₹399 from settings.
  - `llms.txt`, robots, sitemap, the manifest and icons return 200.
  - An unknown path returns 404.
  - Assets are immutable.
  - At 375 px nothing overflows and there are no targets under 44 px.
- **Site** (dev on :5181):
  - At 360 px nothing overflows, inputs are 16 px and the header is 92 px.
  - At 900 px nothing overflows.
  - `/faq`, `/refunds`, `/contact`, `/llms.txt`, robots, sitemap, the manifest and `favicon.ico` return 200.
  - An unknown path returns 404.

## Not done (needs a design canvas or a decision)

- **Graph header on phones:** about 259 px tall, with wrapped text buttons. This is a redesign, so it starts on a design canvas.
- **Speed:**
  - Self-hosting the app's fonts needs a CSP change.
  - The site loads Supabase on every page (about 226 KB gz). Moving that off the critical path touches the shared sign-in.
  - App modulepreload.
- **Per-post share images and new India-specific blog posts.** These are content and design work.
- **Paid offers in JSON-LD:** the prices are runtime settings, so the static data names none.
- **`account.tsx`:** it still says "the app has its own sign-in", which may be wrong since Leafout sign-in (L1).
- **Fixed colour literals:** the manifest and theme-color repeat `--bg` as fixed values, because meta tags can't read CSS variables.

## Founder actions

1. **Publish:** publish the site in Lovable, then redeploy the app (`npm run deploy`).
2. **Legal pages:** read `/privacy`, `/terms` and `/refunds`, then remove their "Draft, under review" chip.
3. **Search engines:**
   - Add both hosts to Google Search Console and Bing Webmaster and submit both sitemaps.
   - Re-scrape the share image in LinkedIn Post Inspector and the WhatsApp/Facebook debugger.
4. **Cloudflare:**
   - Check that "Block AI bots" and AI Crawl Control aren't overriding robots.txt.
   - Set Browser Cache TTL to "Respect existing headers".
   - Turn the `www` redirect into a 301.
5. **Razorpay:**
   - Set the business name to Leafout.
   - Turn on payment receipt emails if you want them (the code sends none).
6. **Email:** verify a leafout.in sender domain for Resend; the sender is still `onboarding@resend.dev`.
7. **Profiles:** add `sameAs` profiles (LinkedIn page, X, GitHub) once they exist; none were invented.
8. **Copy that will go stale:**
   - When USD prices go live, update the pricing copy that says dollars aren't on sale.
   - Update the GST wording if you register.
9. **Coach persona:** it now says "You are Leafout". Run `npm run smoke` before you deploy.
