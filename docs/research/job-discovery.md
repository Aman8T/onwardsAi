# Job discovery: sources, geography, matching and rules (research, 2026-10-16)

Discover is the planned feature that shows a person roles near them that fit their resume and skill graph, each with a direct apply link to the employer. It is design only so far: a canvas with placeholder data, and no code in the app changes. This note records the research behind it. It covers which job sources we may use and on what terms, where map and distance data can come from, how to match and group postings without percentage scores, and which privacy and employment rules shape the screens. Seven research tracks were run by Haiku researchers, and a Sonnet reviewer cross-checked the claims against primary sources. Anything that was not confirmed is marked. This is research, not legal advice. Counsel should review the map, the regulation notes and every partnership before build.

## 1. The bottom line

- No Indian job portal gives us a legal listing feed. Naukri, foundit, Instahyre, Internshala, Apna, Indeed and the National Career Service have no listing API, and most portal terms forbid automation. We reach those portals only through partnerships, or not at all.
- Employer applicant-tracking feeds are the spine of the product. Greenhouse and Lever are public and need no key. Ashby can join once it gives written permission. These feeds carry clean apply links that go straight to the employer's form.
- No directory of employer feeds exists, so we keep our own employer registry.
- Licensed aggregators and remote boards (Adzuna, Himalayas, Remotive) add reach, but each sets its own rules for the apply link, such as a "via" label and a credit line. Those rules live in each source's policy record, so the interface does not change when a source is added or removed.
- Launch coverage is decent for tech, startup and remote roles, and thin for nurses, accountants, teachers and freshers. The design says so, and offers "paste a posting you found" for the gap.
- Matching is mostly code. A shortlist is computed from title family, skill names, years, freshness and source confidence. The existing posting match runs only when a person opens a role. Cards show words and counts, never a percentage.
- The map stays at city zoom with no boundary layers, uses self-hosted tiles, and shows straight-line distance at launch. Counsel must review any national view before it ships.

## 2. Job sources

Verdict key. **USE**: we may use it under the source's own terms. **LICENSE**: a licence or written permission is needed first. **PARTNER**: a partnership agreement is needed. **LINK-OUT ONLY**: we may show a link to the source and nothing more. **AVOID**: we do not use it. Rows marked "provisional" have not had their terms read in full, so the verdict is a holding position.

### 2.1 Verdicts

| Source | Access | Apply link | Terms that matter | Verdict |
|---|---|---|---|---|
| Greenhouse | Public job board API, no key | Employer's ATS form | Public board data, no key needed | USE |
| Lever | Public postings API, no key | Employer's Lever form | Public postings, no key needed | USE |
| Ashby | Public job posting API | Employer's Ashby form | Needs Ashby's written OK before we switch it on | USE once written OK |
| SmartRecruiters | Posting API | Employer's SmartRecruiters page (to confirm) | Unclear whether a key is needed (single source) | LICENSE (provisional) |
| schema.org JobPosting on careers pages | Markup on employer pages, read lazily | Employer's own page | Allowlisted hosts only; 24 hour cache; Google's markup guidance | USE (later wave) |
| Adzuna | API with app id and key | Adzuna landing page, labelled "via Adzuna" | Licence needed; cost unknown; probably covers India (single source) | LICENSE |
| Jooble | REST API | Not confirmed | Terms not read (provisional) | LICENSE (provisional) |
| Careerjet | Partner API | Not confirmed | Terms not read (provisional) | LICENSE (provisional) |
| Himalayas | Public remote jobs API | Source-mandated "via" link | Free for remote roles; credit and own "via" link required | LINK-OUT ONLY |
| Remotive | Public remote jobs API | Source-mandated "via" link | Free for remote roles; credit and own "via" link required | LINK-OUT ONLY |
| Jobicy | RSS feed | Source link (to confirm) | Terms not read (provisional) | LINK-OUT ONLY (provisional) |
| Indeed | Publisher API closed to new use; the Job Sync API is a separate feed (to confirm) | Not usable as a source | Terms ban scraping | AVOID |
| Glassdoor | Closed to new use (single source) | Not usable as a source | Not assessed further | AVOID |
| Google for Jobs | No search API | Not a source; we read employer markup instead | Nothing to query | AVOID |
| SerpApi | Paid API over Google results | Not usable | Scrapes Google | AVOID |
| JSearch | API over Google results | Not usable | Scrapes Google | AVOID |
| TheirStack | Paid job data API | Not usable | Built on LinkedIn data | AVOID |
| Fantastic.jobs | Paid job data API | Not usable | Built on LinkedIn data | AVOID |
| LinkUp | Paid raw data product | Not assessed | Not in the synthesis (provisional) | AVOID (provisional) |
| Naukri | No listing API | Only per agreement | Live terms blocked to our fetcher (403); a person must read them | PARTNER |
| iimjobs | No listing API | Home page only | Forbids linking past the home page | AVOID |
| hirist | No listing API | Home page only | Forbids linking past the home page | AVOID |
| foundit | No listing API | Only per agreement | Most terms forbid automation | AVOID (partner only) |
| Instahyre | No listing API | Only per agreement | Most terms forbid automation | AVOID (partner only) |
| Cutshort | No listing API known | Not assessed | Terms page cited, not summarised (provisional) | AVOID (provisional) |
| Apna | No listing API | Only per agreement | Partnership track (tier E) | PARTNER |
| WorkIndia | Partnership track (tier E) | Per agreement | Partnership track | PARTNER |
| Internshala | No listing API | Only per agreement | Most terms forbid automation | AVOID (partner only) |
| Unstop | Not in the synthesis | Not assessed | Terms not read (provisional) | AVOID (provisional) |
| National Career Service | Government portal, no listing API | Per partnership | Partnership track (tier E) | PARTNER |
| data.gov.in | Open datasets | Not applicable | Government Open Data licence; reference datasets, not listings | USE (datasets only) |
| LinkedIn | None | None | Never | AVOID (never) |

### 2.2 Source tiers

The tiers set the apply-link preference and the confidence label a person sees.

| Tier | Sources | Apply link | Confidence label |
|---|---|---|---|
| A employer-direct | Greenhouse, Lever (Ashby after written OK), later careers pages with JobPosting markup on allowlisted hosts | Employer page or ATS form | "Employer's own listing, checked today" |
| B licensed aggregator | Adzuna India (after licence) | Aggregator landing page, labelled "via Adzuna" | "From a partner, employer not confirmed" |
| C remote partners | Himalayas, Remotive | Source-mandated, "via" | Same as B |
| D person-pasted | Paste a posting | The person's own link | "Added by you" (private) |
| E partnerships | NCS, Apna, WorkIndia, Naukri | Per agreement | Set by each agreement |
| Never | LinkedIn, scrapers, vendors built on LinkedIn data | None | None |

### 2.3 Coverage by segment

Coverage is a judgement from the source survey, not a measured figure. It should be measured once feeds are live.

| Segment | Launch coverage | Why | What the design does |
|---|---|---|---|
| Tech and startups | Decent | Greenhouse and Lever feeds, Ashby after permission | Leads with employer listings |
| Remote | Decent | Himalayas and Remotive, both link-out only | Its own lane; "region not stated" is shown, not counted |
| Non-tech professions (nurses, accountants, teachers) | Thin | Few public employer feeds; portals need partnerships | Says so; offers paste; companies lane |
| Freshers | Thin | Internshala, Apna, NCS and Unstop need partnerships or are not yet assessed | Honest coverage line; early-career first impression |

## 3. Product landscape

The table sets out what to borrow from other products, what to avoid, and what makes Discover different. Where the research notes do not name a product, the origin is the rule itself.

| Idea | Kind | Origin | Note |
|---|---|---|---|
| Split list and map, with labelled pins for the best dozen or so and dots for the rest | Borrow | Airbnb's split search (airbnb.tech/?p=683) | Labels only the strongest few, so the map does not overwhelm |
| Count circles that open into pins on zoom | Borrow | Airbnb (same source) | Keeps a 500-result map readable |
| Soft toggles that reorder rather than hide | Borrow | Citymapper (no link in our list; single source) | Basis for "Why this order" and the unranked switch |
| Commute as a time budget | Borrow, later phase | Google's commute search documentation | Shipped only as a labelled estimate |
| Expiry signals (validThrough, 404 and 410 responses) | Borrow | Google's JobPosting guidance | Feeds the closed-role state |
| Percentage or paywalled scores | Avoid | Our rule (CLAUDE.md, no fake scores) | Replaced by words and named counts |
| Pins presented as proof of commute | Avoid | Our rule | Distance is labelled as straight line |
| The word "ghost" | Avoid | Term used by Kickresume's ghost jobs study | We state facts instead: "open 7 weeks", "reposted 3 times" |
| Predicted salary | Avoid | Our rule | Salary shown only when the employer states it |
| Auto-apply | Borrow, with guard rails | Decided 2026-11-06 (CLAUDE.md rule 9, `docs/auto-apply.md`); tested in `experiments/auto_apply/` | Only on the employer's own ATS form, only for roles the match says fit, and the person approves every submit. Never on boards whose terms forbid automation |
| AI edits that add skills | Avoid | Our rule; docs/research/open-source-resume-tools.md, section 6 | Discover never edits a resume |
| Links to portal search pages | Avoid | Our apply-link rule | Only the posting's own link |
| Names, college or photo as ranking inputs | Avoid | Embedding bias study (arXiv 2407.20371) | Excluded from ranking and listed as excluded |
| Named, counted gaps on each card | Different | Our design | Read from skill names only; full evidence on click |
| Clusters from the person's own next roles and strongest skills | Different | Our design | Each cluster cites a fact |
| Labelled, employer-first apply destinations | Different | Our design | The label names the host |
| Location as a stated, correctable constraint, with separate remote and relocation lanes | Different | Our design | Relocation figures in section 7 |
| Honest freshness | Different | Our design | Counted from our own first-seen date |

Other references. Jobright's AI job match page, Rezi's map post, Indeed's account of how it uses AI for matching, and Greenhouse's 2024 state of job hunting report were read as references. The notes do not record which feature of each one informed the design, so none is credited with a specific idea above. Each is a single source for its own description.

## 4. Geography

### 4.1 Map and tiles

| Option | Role | Source | Position |
|---|---|---|---|
| MapLibre GL JS | Map renderer | Migration guide, v5 to v6 | Chosen; pin a version at build time |
| Protomaps basemap downloads | Self-hosted vector tiles | Protomaps documentation | Chosen; keeps the page's Content Security Policy at 'self' |
| OpenFreeMap | Hosted tiles | Site and terms of service | Prototypes only |
| MapTiler Cloud | Hosted tiles and APIs | Pricing page | Cost reference, not chosen |
| Stadia Maps | Hosted tiles and APIs | Pricing page | Cost reference, not chosen |
| Google Maps Platform | Maps, routes, isochrones | Price list, March 2025 | Cost reference; isochrone use is open (section 8) |
| Ola Krutrim Maps | Indian hosted maps and APIs | Pricing page | Cost reference; licence not evaluated |
| Geoapify | Geocoding and routing | Pricing page | Cost reference |
| GraphHopper | Routing, hosted or self-hosted | Pricing page | Phase 2 alternative to Valhalla |
| Valhalla | Self-hosted routing | GitHub repository | Phase 2 candidate for travel time |
| GeoNames | Locality gazetteer | Exports page | Candidate for locality names; licence to check |
| Nominatim (public service) | Geocoding | OpenStreetMap Foundation usage policy | Never called from the app |

### 4.2 Boundaries

India requires Survey of India boundaries on political maps of any scale, as the research reads it. The map therefore stays at city zoom with no boundary layers. Counsel must read the Survey of India's terms for foreign entities, the Government of India's OpenStreetMap guidance, and the boundary question (section 8) before any national view.

### 4.3 Distance and travel time

- Phase 1: straight-line kilometres, labelled "straight line", plus named localities the person picks. The anchor comes from the resume at city level, and no distance appears until a locality is picked. There is no GPS and no IP lookup.
- Phase 2: travel time by two-wheeler, transit or car, from self-hosted Valhalla or GraphHopper, labelled "estimate". Google's isochrones are a candidate. Whether they cover two-wheelers in India is unverified.
- A pin is never presented as proof of commute.
- Transit feed (GTFS) availability for Indian cities has not been checked.

### 4.4 Geocoding

- Geocode each listing at ingest to locality level. Listings are not geocoded to street addresses.
- Never call public Nominatim from the app; its usage policy rules that out. Which geocoder replaces it is open.

## 5. Matching, deduplication and clustering

### 5.1 Shortlist (code only)

The first stage runs in code and needs no model call per person. Its inputs are the title family (through an ESCO, ISCO or NCO table), skill-name overlap (through `match/aliases.py`), the years gap, the next roles in `Profile.roles` and their requirements, distance, freshness and source confidence. The result is an internal sort key that the person never sees. The interface shows the reasons instead. Name, college, age, gender and photo are not inputs.

### 5.2 Second stage

The second stage is the existing posting match, run only when a person opens a role. The posting read is user-independent and happens once per listing at ingest, reusing the posting cache through `postingHash`. Browsing, hover cards and named gaps cost nothing. A full match uses the credits the match screen uses today.

### 5.3 Taxonomies and licences

- **ESCO** is free for any purpose with the attribution sentence, and modified versions must say so (research survey, section 3.12). It is the source for title families and skill names.
- **O*NET** database is CC BY 4.0 and covers the United States. Its web services API has separate terms (research survey, section 3.12). It is not a dependency for Indian roles.
- **Lightcast** is gated. The earlier survey found its API on contract and not free for us, so it is not used.
- **NCO 2015, Volume I** is India's National Classification of Occupations, the classification used for Indian government roles. A crosswalk through ISCO-08 is planned in wave X1.
- **JobBERT** (arXiv 2109.09605) is a job-title embedding model. It is a candidate for title matching if the table misses a title. It is not adopted.

### 5.4 Deduplication

Key on employer, title and city, then compare the text of the postings. Prefer two similar openings over a wrong merge: an uncertain pair stays as two listings. A confirmed duplicate becomes one role with "Also listed via Adzuna". The Textkernel article in our list covers near-duplicate detection (single source; not re-read for this note).

### 5.5 Clustering

The default grouping is why a role fits, with place as a lens. A switch offers By place and By company. Each role has one home, chosen by precedence: each of the top three next roles, current work, one skill away, first steps (under two years, or unknown), then other close roles. Faceted browsing (Flamenco, 2002) is the basis for grouping by facet. The treemap paper in our list is cited for grouped display; the design does not use treemaps so far.

## 6. Privacy and regulation (not legal advice)

| Regime | What it says (as recorded) | Date | Design consequence |
|---|---|---|---|
| India: DPDP Act 2023 and DPDP Rules | Notice, consent and children's duties | Rules notified November 2025 (PIB release); duties apply from about May 2027 (to confirm against the notified rules) | Say where location comes from; make it editable; keep city level only |
| India: DPDP Act, section 9 (children) | Under-18s need verifiable parental consent, and no tracking or targeting | Applies with the duties | No alerts for under-18s; no tracking or targeting of them |
| EU AI Act, Annex III (employment heading) | AI used for recruitment or selection, such as filtering applications and evaluating candidates, is high-risk | Obligations in force in stages | The Commission's guidance is read as placing candidate-side tools, whose output stays with the candidate, outside the category (single source for that reading). Sharing rankings with employers would change that |
| EU AI Act timing | High-risk obligations reported to move to December 2027 under the 2026 omnibus | December 2027 (reported) | Single source, a news summary; verify against the Official Journal before relying on it |
| GDPR Article 22 and Articles 13 to 15 | Limits on solely automated decisions with significant effects; a duty to explain the logic involved | In force | "Why this job" explanation; unranked view one tap away. The safeguards include a right to contest, which the design does not yet offer |
| EU Digital Services Act, Article 27 | Online platforms state the main parameters of their recommender systems in their terms | In force | Micro and small platforms are generally exempt from this section; likely exempt for us while small (to confirm) |
| New York City, automated employment decision tools | Employer-side rules for bias audits and notices | Per the city's page | Applies to employers; applies to us only if rankings reach employers (to confirm) |
| Colorado SB 26-189 | Bill page cited; employer-side as the research read it | Status to confirm | Monitor |
| California automated decision-making regulations | Rules for businesses using automated decision-making technology | Per the CPPA updates page | Status and scope to confirm; no design change until counsel reads them |

### 6.1 Interface requirements these imply

- Tell people that location is inferred from the resume, show where it came from, let them correct it, and store city and chosen locality only.
- Explain each ordering: the signals used, the signals never used (name, college, age, gender, photo), and a one-tap unranked view ("Show in posted order").
- Nothing is filtered silently. Each reason cites a fact the person can open.
- No personal data in outbound URLs. Apply links carry no added tracking and no referral.
- No alerts for under-18s. Alerts are explicit opt-in with one-click off.
- Rankings stay with the person. Sharing them with employers is a separate decision for counsel.

## 7. India market facts used in the design

| Fact | Figure as recorded | Source | Status | Used for |
|---|---|---|---|---|
| Youth unemployment | 9.9 percent in 2025 | MoSPI, PLFS 2025 press note | Official release | Context for an early-career first impression |
| Fresher hiring | Up, and growing fastest in tier-2 cities | Naukri JobSpeak note | Single source (portal's own data) | Why the design serves tier-2 freshers |
| Coimbatore hiring | Up 28 percent (metric to confirm) | Naukri JobSpeak note | Single source | Example of tier-2 growth |
| White-collar hiring | Up 14 percent in August | Naukri JobSpeak note | Single source | Market context |
| Remote or hybrid mentions | About 9.1 percent of Indian postings, October 2025 | Indeed Hiring Lab, Australian-site page | Single source; check against India data | Sizing the remote lane |
| Employers open to non-local talent | 80 percent | WorkIndia survey | Single source; vendor survey | Relocation lane |
| Candidates willing to relocate | 24 percent | WorkIndia survey | Single source; vendor survey | Relocation lane kept separate |
| Bengaluru travel time | 36 minutes per 10 km; second most congested city in 2025 | TomTom index, as reported by Deccan Chronicle | Single source; news report | Why distance alone misleads; reason for the commute phase |

The first impression is tuned to early-career people near home, and the design serves all five personas in the canvas. A 2025 ETV Bharat report on cyber gangs exploiting job seekers (single source) is context for scam risk. The synthesis does not turn it into a feature; whether to add a fake-job warning is open (section 8).

## 8. Open questions and unverified items

1. **Ashby.** Written permission to use its public posting API. Ashby stays off until it arrives.
2. **Naukri.** Live terms. The page returned 403 to our fetcher. A person must read naukri.com/termsconditions before any Naukri listing or link is used.
3. **Adzuna.** The licence cost, and whether its India coverage and link rules suit the design.
4. **City-scale boundary question.** Whether a city-scale map with no boundary layers is enough for launch, or whether any national view needs data counsel accepts. Counsel decides.
5. **SmartRecruiters.** Whether its posting API needs a key (single source).
6. **Two-wheeler isochrones.** Whether Google's isochrone API covers two-wheelers in India (unverified).
7. **Terms not read in full.** Jooble, Careerjet, Jobicy, LinkUp, Cutshort and Unstop. Their verdicts are provisional.
8. **Indeed Hiring Lab figure.** Taken from the Australian edition page; check the India figure before quoting it.
9. **EU AI Act date.** December 2027 is a single source, a news summary.
10. **DPDP timing.** "About May 2027" to confirm against the notified rules.
11. **GTFS.** Transit feed coverage for Indian cities has not been checked.
12. **Contest route.** GDPR Article 22 safeguards include contesting a decision. The design has no contest route yet.
13. **Scam warnings.** Whether to add a fake-job warning, given the ETV Bharat report.
14. **Coverage.** "Decent" and "thin" are judgements from the source survey, not measurements.

## 9. Sources

### Employer feeds
- https://docs.greenhouse.io/job-board.html
- https://github.com/lever/postings-api
- https://developers.ashbyhq.com/docs/public-job-posting-api
- https://developers.smartrecruiters.com/docs/posting-api
- https://developers.google.com/search/docs/appearance/structured-data/job-posting

### Aggregators and boards
- https://developer.adzuna.com/docs/terms_of_service
- https://developer.adzuna.com/docs/search
- https://help.jooble.org/en/support/solutions/articles/60001448238-rest-api-documentation
- https://www.careerjet.com/partners/api/
- https://himalayas.app/docs/remote-jobs-api
- https://github.com/remotive-com/remote-jobs-api
- https://jobicy.com/jobs-rss-feed
- https://serpapi.com/google-jobs-api
- https://www.openwebninja.com/api/jsearch
- https://theirstack.com/en/docs/data/job
- https://fantastic.jobs/about
- https://www.linkup.com/products/raw
- https://docs.indeed.com/job-sync-api
- https://www.indeed.com/legal

### India portals
- https://www.iimjobs.com/terms/
- https://www.hirist.tech/terms/
- https://cutshort.io/terms
- https://in.indeed.com/legal
- https://internshala.com/terms
- https://www.ncs.gov.in/
- https://data.gov.in/government-open-data-license-india
- https://www.naukri.com/termsconditions (returned 403 to our fetcher; a person must read it)

### Products and patterns
- https://airbnb.tech/?p=683
- https://in.indeed.com/insights/how-indeed-uses-ai-to-provide-better-matching-context-for-jobseekers
- https://jobright.ai/ai-job-match
- https://www.rezi.ai/posts/rezi-map
- https://docs.cloud.google.com/talent-solution/job-search/v3/docs/commute-search
- https://www.greenhouse.com/blog/greenhouse-2024-state-of-job-hunting-report
- https://www.kickresume.com/en/press/ghost-jobs-study/
- https://www.etvbharat.com/en/state/cyber-gangs-exploit-job-market-telangana-police-warn-citizens-enn25102201235

### Market
- https://www.mospi.gov.in/uploads/latestReleases/latest_release_1774607827733_3e8964a9-268b-4cc9-ad65-cfc8a9e32f08_Press_note_AR_PLFS_2025_23032025_V2.1_26032026_final.pdf
- https://www.naukri.com/blog/naukri-jobspeak-white-collar-hiring-grows-14-in-aug-fresher-hiring-ai-roles-lead/
- https://hiringlab.indeed.com/au/blog/2025/12/02/october-2025-in-labour-market-update-the-latest-remote-work-trends/
- https://cxotoday.com/media-coverage/80-of-employers-welcome-non-local-talent-but-only-24-of-candidates-willing-to-relocate-finds-workindia-survey/
- https://deccanchronicle.com/nation/bengaluru-named-worlds-second-most-congested-city-in-2025-1931959

### Matching
- https://arxiv.org/pdf/2109.09605
- https://esco.ec.europa.eu/en/about-esco/faq
- https://services.onetcenter.org/help/license
- https://docs.lightcast.io/lightcast-api/docs/api-access
- https://dge.gov.in/dge/sites/default/files/2022-07/National_Classification_of_Occupations_Vol_I-2015.pdf
- https://arxiv.org/abs/2407.20371
- https://www.textkernel.com/newsroom/online-job-postings-have-many-duplicates-but-how-can-you-detect-them-if-they-are-not-exact-copies-of-each-other/
- https://flamenco.berkeley.edu/papers/flamenco02.pdf
- https://homes.cs.washington.edu/~jheer/files/treemaps.pdf

### Geography
- https://maplibre.org/maplibre-gl-js/docs/guides/v5-to-v6-migration-guide/
- https://openfreemap.org/
- https://openfreemap.org/tos/
- https://docs.protomaps.com/basemaps/downloads
- https://www.maptiler.com/cloud/pricing/
- https://stadiamaps.com/pricing/
- https://developers.google.com/maps/billing-and-pricing/march-2025
- https://maps.olakrutrim.com/pricing
- https://www.geospatial.dst.gov.in/ForeignEntityTermsandCondition.aspx
- https://wiki.openstreetmap.org/wiki/India/Government_of_India
- https://operations.osmfoundation.org/policies/nominatim/
- https://geoapify.com/pricing
- https://github.com/Valhalla/valhalla
- https://www.graphhopper.com/pricing/
- https://developers.google.com/maps/documentation/isochrones
- https://www.geonames.org/export/

### Regulation
- https://www.pib.gov.in/PressReleasePage.aspx?PRID=2190014
- https://www.dpdpa.com/dpdpa2023/chapter-2/section9.html
- https://artificialintelligenceact.eu/annex/3/
- https://ai-act-service-desk.ec.europa.eu/en/employment-0
- https://www.actuia.com/en/news/ai-act-obligations-in-force-and-delays-under-omnibus-2026-1744/
- https://gdpr-info.eu/art-22-gdpr/
- https://www.eu-digital-services-act.com/Digital_Services_Act_Article_27.html
- https://www.nyc.gov/site/dca/about/automated-employment-decision-tools.page
- https://leg.colorado.gov/bills/sb26-189
- https://cppa.ca.gov/regulations/ccpa_updates.html
