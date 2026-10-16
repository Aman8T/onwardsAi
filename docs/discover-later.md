# Discover: what waits for permission, legal review or infrastructure

> Since 2026-10-16 (D3 in `docs/roadmap.md`) Discover is Search with AI only: the list-first screen, lenses, lanes, groups and companies lane described below were removed. The items that wait still apply to the sources and features the search would add.

Discover v1 ships only what needs nobody's permission and no legal review: Greenhouse and Lever job boards from a curated employer registry, a list-first screen without a map, straight-line distance by locality, lenses, lanes, groups, a code-only quick fit, labelled apply links, the companies lane, click-through to the existing match, and save and track in the browser. The rest needs a yes from a source or partner, a legal read, infrastructure we have not built, or data we have not yet collected, so it waits. The full design, with placeholder data, is on the canvas at https://claude.ai/artifact/8A5u18LpAhxB7fGfAinhmv. Section numbers below refer to `docs/research/job-discovery.md`.

## What waits

The Why column uses four labels. **Permission** means someone must say yes. **Legal** means counsel must review it. **Infrastructure** means something must be built or hosted. **Data** means something must be collected first.

| Item | What it adds | Why it waits | What unblocks it | Source in the research doc |
|---|---|---|---|---|
| Ashby job boards | Employers on Ashby's public posting API | Permission | Ashby's written OK for third-party use. The user asks by email | §2.1, §8.1 |
| SmartRecruiters | Employers on its posting API | Permission | Whether its API needs a key, and its terms (single source). The user checks, then requests a key if needed | §2.1, §8.5 |
| Workday career sites | Employers on Workday | Legal, Data | Undocumented endpoints and terms. Counsel reads the terms first | Not in the research doc |
| Careers pages with schema.org JobPosting | Employers' own pages, read from markup | Permission, Legal | Robots.txt and each host's terms. The user approves an allowlist, and counsel reviews the approach | §2.1, §2.2 |
| Adzuna India | Aggregator listings, labelled "via Adzuna" | Permission, Legal | A commercial licence (cost unknown), attribution and redirect-link rules. The user decides cost and terms | §2.1, §2.2, §8.3 |
| Jooble, Careerjet, Jobicy, LinkUp, Cutshort | Partner and remote-board listings | Permission | Partner keys and terms the research has not read in full. The user reads them and requests keys | §2.1, §8.7 |
| Himalayas and Remotive | Remote India-eligible roles, link-out only | Permission | Credit, a mandatory "via" link and no redistribution. The user confirms the terms and whether a key is needed | §2.1, §2.2 |
| Naukri, foundit, Instahyre, Shine, TimesJobs | Portal listings | Permission | Partnerships only, since most terms forbid automation. A person reads Naukri's live terms, which returned 403 to our fetcher | §2.1, §8.2 (Shine and TimesJobs: not in the research doc) |
| iimjobs and hirist | Portal listings | Permission | Terms forbid linking past the home page. Only a deep-link agreement changes that | §2.1 |
| Apna and WorkIndia | Blue- and grey-collar listings | Permission | Partnership track. The user opens talks | §2.1, §2.2 |
| Internshala and Unstop | Fresher and campus listings | Permission | Internshala's terms forbid automation, and Unstop's are unread. The user reads both and asks for a partnership if the terms allow one | §2.1, §8.7 |
| National Career Service and other government portals | Government listings | Permission | A data-sharing request to the relevant ministry. The user sends it | §2.1, §2.2 |
| City map | Pins on a city map | Legal, Infrastructure | As the research reads it, the Survey of India rule covers political maps of any scale. Counsel reads it first. Then engineering adds MapLibre, self-hosted Protomaps tiles and a CSP `worker-src` change | §4.1, §4.2, §8.4 |
| Commute time | Travel time, labelled "estimate" | Infrastructure, Data | Engineering hosts Valhalla or GraphHopper. Someone checks two-wheeler isochrones and transit feeds for Indian cities | §4.3, §8.6, §8.11 |
| Email alerts for new fits | Opt-in emails when new roles fit | Infrastructure, Legal | An email provider and sender address (the user creates them). Explicit consent, one-click off, a DPDP notice, and no alerts for under-18s. Counsel signs off the consent text | §6, §6.1 |
| Saved roles and tracker synced to an account | The same tracker on any device | Infrastructure | Sign-in on production. The user sets the sign-in secrets on FastAPI Cloud | Not in the research doc |
| "Hires often, nothing open now" | A companies-lane reason from 90 days of history | Data | Our own feed history for 90 days. It builds up once the feed runs | §3 (honest freshness) |
| Minors' consent and inferred-location notice | Consent flow for under-18s, and a notice that location comes from the resume | Legal | DPDP Rules: duties from about May 2027, date to confirm. Counsel reads the notified Rules | §6, §8.10 |
| EU users and the EU AI Act | Any employer-facing ranking | Legal | Annex III reading, and a December 2027 date from a single news source. Rankings stay candidate-side until counsel clears them | §6, §8.9 |
| Contest route for ranked orders | A way to contest an ordering under GDPR Article 22 | Legal | Counsel defines what the route must do. Engineering builds it | §6, §8.12 |
| Model reading postings at ingest | Richer skill reading than the code-only quick fit | Data | Cost and evaluation, not permission. Measure the quick fit first, and price a model only if the quick fit proves too coarse | §5.1, §5.2 |
| LinkedIn | Nothing | Legal | Never. CLAUDE.md rule 4 bans scraping it. Listed so nobody adds it | §2.1 |
| Search with AI: Ashby boards | The pool where the Opus baseline found 4 of its 6 roles (remote-India startups) | Permission | Ashby's written OK (as above); then `harvest.py` accepts `jobs.ashbyhq.com` and a board reader for its posting API | `docs/research/jobsearch-baseline.md` |
| Search with AI: "worldwide" remote roles | Roles like Canonical's "home-based worldwide", open to India | Data | A rule in `discover/parse.py` for remote roles that say worldwide, anywhere or global, checked against real postings for hidden limits (time zones, payroll countries) | `docs/research/jobsearch-baseline.md` |
| Search with AI: keep the employers it finds | A registry that grows from what the scouts find | Infrastructure | A table of found boards (token, name, first found, roles in India), reviewed before Discover reads them on its own schedule | `docs/research/jobsearch-baseline.md` |
| Search with AI: evergreen postings | Postings first published over a year ago ranked lower, or labelled | Data | Measure how many such postings still hire; then a rule in `narrow.py` | `docs/research/jobsearch-baseline.md` |
| Search with AI: a replay eval | Tuning the narrowing without paying for searches | Infrastructure | Store a few runs' search results and board reads as fixtures, and score the narrowing against the baseline's picks | `docs/research/jobsearch-baseline.md` |

## Order we'd suggest

Cheapest unblock first. The first item needs no decision, so it should start now.

1. Keep the 90-day history running from launch. It needs time, not a decision.
2. Email Ashby for written permission. Ashby stays off until the reply.
3. Read the six provisional terms in §8.7 and Naukri's live terms.
4. Check whether SmartRecruiters needs a key.
5. Set up sign-in on production, then the synced tracker, then email alerts.
6. Add careers pages with JobPosting markup, host by host, after reading each host's terms and robots.txt.
7. Counsel reads the Survey of India rule before the city map is built.
8. Adzuna licence and any remote-board keys.
9. Commute time, once routing is hosted and transit and two-wheeler coverage are checked.
10. Partnerships and the government data request, as counsel clears each one.
11. A model at ingest, only if the quick fit proves too coarse.

## Who does what

- **The user:** emails Ashby, Adzuna, Jooble, Careerjet, the portals and counsel; reads the unread terms in full; sends the government data request; creates the sender address and the Google sign-in app; sets secrets with `fastapi cloud env set --secret NAME`.
- **Counsel:** the boundary rule and Survey of India terms; DPDP Rules and the minors' flow; the EU AI Act reading; the GDPR contest route; the alert consent text; every partnership before build.
- **Engineering:** the city map (MapLibre, Protomaps tiles, the CSP change); routing (Valhalla or GraphHopper); alerts (opt-in, one-click off, never for under-18s); the careers-page reader; the Ashby and SmartRecruiters adapters once permitted; the synced tracker.
