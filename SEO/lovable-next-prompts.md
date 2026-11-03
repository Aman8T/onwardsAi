# Lovable: remaining prompts for the Tayyari front door

- **Project:** Tayyari Career Intelligence (`44f9ca21-c891-4bfd-b4e1-3685b8da886b`)
- **Editor:** https://lovable.dev/projects/44f9ca21-c891-4bfd-b4e1-3685b8da886b
- **Preview:** https://id-preview--44f9ca21-c891-4bfd-b4e1-3685b8da886b.lovable.app

**Done so far:**
- design-system port
- cursor light
- shell, routes and `appLink`/`track`
- living skill graph with the intent picker
- site accounts (Email, Google, Lovable; Phone is waiting for an SMS provider)

**Update, 2026-10-18:** prompts 3 and 4 below were built directly in code instead of through Lovable chat. The work is commit `60f9efa` on `main` in https://github.com/Aman8T/tayyari-career-intelligence, and it isn't pushed yet. Pushing syncs it into the Lovable project and needs no Lovable credits.

After the push:
1. Apply `drizzle/migrations/0001_feedback_and_events.sql`. This creates the `feedback` and `events` tables.
2. Confirm the published subdomain. `CANONICAL_ORIGIN` in `src/config/site.ts` assumes `tayyari-career-intelligence.lovable.app`.

The prompts below are kept only for reference.

---

## Prompt 3: rest of Home, live demos, motion

Accounts look good. Now build the rest of **Home** below the hero, with live demos driven by `src/data/tayyari-sample-data.json`. Every number comes from that file. Label each demo "Sample profile · fictional".

**1. Posting match playground** (`id="match"`)
- Serif section line: "See every job requirement against what you've actually done."
- Tabs switch between two sample postings:
  - `meeraMatch`: "Senior Growth Marketing Manager", Lumen Retail (a marketer)
  - `riyaMatch`: "Senior Frontend Engineer, Design Systems", Northwind Commerce (an engineer)

  Having both shows that Tayyari works for every profession.
- **Left column: requirements.** Each row shows:
  - a status taken from `result.coverage[].status`:
    - covered: solid dot
    - partial: half dot
    - missing_gap, missing_have_it or unknown: hollow dashed dot, labelled "gap" or "to confirm"
  - the requirement text
  - a "must" or "nice" tier chip

  Rows reveal one at a time, with a 40ms stagger, once.
- **Opening a row** (click, hover or focus) shows its evidence:
  - The cited quotes come from `facts`, via `coverage.factIds` → `facts[].refs[].quote`, each with a source chip.
  - Show them in a popover on desktop and in a bottom sheet under 760px.
  - **Never show `coverage.note`.**
- **"Gaps only" toggle.**
- **Right column: an SVG radar** from `result.radar.axes`:
  - "You" is a solid polygon and "Role" a light fill.
  - Labels come from `domain`.
  - It draws once on scroll-in and morphs over 500ms when the tab changes.
  - `outside` requirements are listed as "Also asked: …". There is never an "Other" axis.
- **Counts line** from `result.counts`, for example "7 covered · 1 partial · 5 gaps". No percentages.
- **CTA:** "Try it with a real posting →", using `appLink('demo-match')`.

**2. Claim vs proof toggle**
- A single "Typical resume" / "Tayyari" switch.
- Typical: "Results-driven marketer with proven track record of growth."
- Tayyari: `meeraMatch.variant.summary.text`, followed by its cited fact quote and a source chip.
- Crossfade over 250ms.
- Kicker: "Every sentence points to something you did."

**3. How it works** (`id="how"`, sticky scroll story)
- On desktop, four steps scroll on the left while a pinned figure on the right morphs between four states.
  - Use Motion `useScroll`/`useTransform`.
  - Animate opacity and transform only.
- The four steps:
  1. **Connect** (the sources)
  2. **Your graph** (a mini radar of `sampleProfile.domains`)
  3. **Match a posting** (three requirement rows)
  4. **Practise** (four chips: This role, Defend your resume, Behavioural stories, Work sample)
- No invented transcript.
- On mobile, show a vertical list.

**4. What you get** (a bento grid of six tiles)
- Tiles:
  - Skill graph
  - Posting match
  - Resume from cited facts
  - Cover note (`meeraMatch.cover.paragraphs[0].text` plus its cited chip)
  - Practice interview
  - Portfolio page
- Each tile holds a tiny live element.
- **Pointer-tinted 1px borders:** a single pointermove listener writes CSS variables directly, outside React. No glow and no shadow. Static tiles on touch.

**5. What we refuse** (four cards)
- "No fake scores. No '87% ATS match'. Bands you can check, not decimals."
- "No invented numbers. A missing number becomes a question to you."
- "No scraping. Your resume, public pages you choose, official APIs."
- "No dark patterns. Seeing a gap and re-checking are free."

**6. Who it's for**
- Two columns: students in India, and working professionals anywhere in any field.
- CTAs: `appLink('who-student')` and `appLink('who-pro')`.

**7. From the blog:** a placeholder for three post cards.

**8. Closing**
- Serif line: "Your next role, backed by what you've done."
- CTA: `appLink('closing')`.
- Quiet link "Tell us what's missing →" to `/feedback`.

**Motion, site-wide**
- **Smooth scroll:** Lenis, on desktop with fine pointers only. Off on touch and under reduced motion. Anchor links use `scrollTo` with a header offset.
- **Reveals:** opacity plus an 8px rise, 350ms, 60ms stagger, run once.
- **Hero H1:** a word-by-word reveal.
- **Page transitions:** a View Transitions crossfade.
- **Reduced motion:** every element shows its final state.
- **Loading and frames:** demos lazy-load below the fold, and nothing runs a rAF loop while idle.

Keep one serif line per screen.

---

## Prompt 4: blog, feedback, SEO, analytics

Now the blog, feedback, SEO and analytics.

**Blog**
- Posts are Markdown files in `src/content/blog/*.md`. Frontmatter fields: title, description, date, tags[], slug.
- Pages:
  - `/blog`: the index, newest first, with a tag filter
  - `/blog/tag/:tag`
  - `/blog/:slug`: reading time, a 1px reading-progress hairline, BlogPosting JSON-LD, and a "Start here" box. The box matches the intent stored in sessionStorage (student / switching / posting) and ends with `appLink('blog-<slug>')`.
- The Home "From the blog" section shows the three latest posts.
- Write three posts in the brand voice, plain and specific, with **no invented statistics**:
  1. "How to read a job posting, requirement by requirement" (tags: job-search, postings)
  2. "Why Tayyari doesn't show an ATS score" (tags: resumes, honesty)
  3. "Turn a college project into evidence a recruiter can check" (tags: students, portfolio)

  About 700–900 words each.

**Feedback** (`/feedback`)
- Form fields:
  - kind: idea / bug / praise / other
  - message: required, at most 2000 characters
  - email: optional, and pre-filled when signed in
- Store in a `feedback` table with: id, created_at, kind, message, email, user_id (nullable), page, utm fields, and a ua_hash.
- Writes go through a server function that applies:
  - a honeypot field
  - a 3-second minimum before submit is accepted
  - a rate limit of 5 per hour per hashed IP
  - length validation
- RLS: no public select.
- Show success and error states. `track('feedback_submit', {kind})`.

**Events table**
- `track()` also writes `{event, props, path, utm, created_at}` to an `events` table through a server function.
- Batch the writes, fire-and-forget, with no PII.

**SEO, on every page**
- A unique title and description, canonical (from `SITE_URL`), Open Graph and Twitter tags, and a static default OG image in the brand style.
- JSON-LD:
  - Organization and WebSite on Home
  - SoftwareApplication on About
  - BlogPosting on posts
- A generated `sitemap.xml` that includes the blog posts and tag pages and leaves out `/signin` and `/account`.
- `robots.txt` that points to the sitemap.
- Unknown paths return a real 404 status.

**Home meta**
- Title: "Tayyari — See every job requirement against what you've actually done"
- Description: "Tayyari reads your resume and public links, maps your skills with evidence, and shows how you match a job posting — no fake scores, every line cited."

**About** (`/about`)
- Why Tayyari exists, in the tone of `project-soul.md` (it means "preparation"), and the refusals.

**Privacy and terms** (`/privacy`, `/terms`)
- Plain-language drafts, with a visible "Draft — under review" note.
