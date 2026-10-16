# Search with AI: the baseline and the Haiku runs (2026-10-16)

The question: given a resume, "find jobs in Bengaluru". Two larger models did it by hand first, with web search and web
fetch, and reported how they narrowed things down. Their method became the design of Search with AI, which runs on
Claude Haiku 5.5 only (the user's decision). Input in every run: the sample resume (`backend/tayyari/sample/resume.md`,
a frontend engineer, 2.4 years, Bengaluru).

## The baseline runs

Rules for both runs:
- At most 25 searches and 25 fetches.
- Never fetch LinkedIn, Naukri or the other portals.
- An apply link must be the employer's own page, open today.

| | Opus | Sonnet |
|---|---|---|
| Searches / fetches used | 17 / 25 | 22 / 25 |
| Specific job links opened | 23 | 19 |
| Of those, closed although search still showed them | 11 | 14 (8 returned 404, 4 redirected to the board, 1 had expired, 3 were unconfirmable) |
| Confirmed open and fitting | 6 | 4 (+2 partly confirmed) |
| Time | ~9 min | ~7 min |

Opus found AiPrise, Soulside AI, Outmarket AI, SigNoz (all four on Ashby), Headout and Canonical (both Greenhouse).

Sonnet found Headout, Amazon, HighLevel (Lever) and Particle41 (Greenhouse).

What both runs agreed on:

- **Search finds employers, not open jobs.** About two in three job links from search were already closed. Greenhouse
  snippets carry no date.
- **An employer's own board is the reliable signal.** Greenhouse redirects a closed job to the board index; Lever answers
  404. The best fetch is a board index or its JSON, then single postings only for matches.
- **Narrow in this order:** place (the city, or remote open to India), kind of role (title and core skills), then level
  (years asked against 2.4; Senior with no years stated is a stretch; Staff, Principal and Lead are out). Only then read
  the requirements against resume lines.
- **Ashby was the most productive pool for remote-India startups.** Opus's advice: "use search only to find companies;
  pull the Greenhouse, Lever and Ashby JSON in code; give the model only the 5 to 10 survivors."
- **Supply is thin.** Few Bengaluru product companies list web-frontend roles at about two years on their own boards.
  Much of that demand sits on the portals we do not touch.

## The design that follows (Haiku 5.5 only)

1. **Scouts.** Three Haiku calls with only the web search tool, restricted to Greenhouse and Lever. Code takes the board
   links from the tool's results, never from the model's words.
2. **Boards.** Code reads each board's public JSON through Discover's fetcher and parser. Every role shown is listed
   today.
3. **Narrowing.** Code narrows by place, then kind, then level.
4. **Reading.** Haiku reads the closest 12 postings against the person's evidence lines.
5. **Checks.** Code checks every quote and works out the fit word.

Details are in `docs/roadmap.md` (D2) and `docs/backend.md` (Search with AI).

Two findings from building it on the API:
- **Haiku 5.5 accepts both web-search tool versions.** The dynamic-filtering one is `web_search_20260209`.
- **Structured output and server tools together make Haiku 5.5 skip the tools.** With `output_config.format` set, Haiku
  answered at once with a placeholder link and ran no search. So the scouts run in plain mode and only the reads use
  structured output.

## The Haiku runs

Four runs on 2026-10-16, each with three scouts (nine searches).

| Run | What changed before it | Boards read | Shortlist | Shown (strong / good / stretch) | Time | Cost |
|---|---|---|---|---|---|---|
| 1 | none | 14 of 16 | 10 | 4 (0 / 2 / 2) | 19 s | ~$0.11 |
| 2 | looser injection check fixed; years no longer met by a line that holds a number | 29 of 30 | 10 | 4 (0 / 2 / 2) | 18 s | ~$0.11 |
| 3 | short, plain scout queries; left-out roles logged | 27 of 30 | 12 | 6 (1 / 2 / 3) | 20 s | $0.106 |
| 4 (in the app) | other-field titles excluded; Senior with no years is a step up | — | 12 | 9 (0 / 4 / 5) | ~25 s | ~$0.11 |

Run 4 showed these good fits:
- **Headout, Frontend Engineer, Bengaluru, "1-3 years".** Both baselines picked it.
- **Particle41, Frontend Developer, remote India.** Sonnet's pick.
- **SonicWall, Front End Engineer, Vue3, remote India.**
- **Drivetrain, Frontend Engineer, remote India, "1 to 3 years".**

The stretches were Neuron7 (Angular), GitLab Senior Frontend, OpenFX Senior Frontend (L5), SonicWall Product Design
Engineer (4+ years) and GitLab People Systems Engineer. That last one was a false match, and it is now fixed.

**Compared with the baseline:**
- About 25 times faster and about $0.11 a run, against hand runs of 7 to 9 minutes.
- The same honest thinness.
- It finds the Greenhouse and Lever roles the baselines found, when the searches surface them. Search results vary
  between runs.
- It cannot find the Ashby roles (4 of Opus's 6) until Ashby gives written permission.
- It cannot find Canonical's "home-based worldwide" roles, which Discover's parser does not count as open to India.
- It cannot find Amazon's own site.

Every job it shows is listed on the employer's board at the moment of the search, which neither baseline could
guarantee for all of theirs.

## What would close the gap

All of these are recorded in `docs/discover-later.md`:
- Ashby's written permission.
- Reading "worldwide" remote roles as open to India.
- Keeping the employers the scouts find, so the registry grows.
- Ranking evergreen postings (first published over a year ago) lower.
- A small eval that replays stored search results, so the narrowing can be tuned without paying for searches.
