# R2 · /resume: one set of blocks, many resumes

Decided with the user on 2026-11-10. Canvas: https://claude.ai/artifact/BCWRN3S1E3ue6w9jEY566c (last row: the page,
Finalise, your blocks). This file is the spec of the first version and the list of what comes after it.

## Why

Other tools rewrite the resume for every role. Copies multiply, a fix made in one is lost in the next, facts drift
between copies, and nobody knows which copy went to which job. Leafout already holds everything a person has done as
cited facts (`match/facts.py`). A resume is one arrangement of those facts.

## The model

- **Block.** One `Fact` of the person's `FactBook`: a job, project or degree (an entry), or one bullet under it. The
  fact's quote is the words of its source and is never edited.
- **Resume.** An arrangement: which blocks are in, in what order, in which words. Stored as a `ResumeVariant`, so
  every line carries the `factIds` it rests on.
- **Draft and final.** A draft is edited freely. Finalise runs the checks and stores a finalised copy. Only a
  finalised resume can be downloaded.
- **Default.** Exactly one resume per profile is the default. The uploaded resume starts as the default.

What may vary per resume: which bullets are in, their order inside a job, their wording, the order of skills.
What never varies: employer, title, dates, and anything no fact supports (rule 1 of `CLAUDE.md`).

## Decisions (the user's)

1. Every upload merges into one set of blocks and is also kept as its own resume.
2. One resume per next role. It is kept when the role later leaves the next five.
3. A resume tailored for a posting (Match) is not in the list unless the person saves it.
4. Any resume can be made the default.
5. Where two uploads disagree, each keeps its own words, new resumes follow the default, and the difference is shown.
6. A draft is edited freely, by hand or through the coach. "Finalise" and "Make default" run the checks.
7. Only a finalised resume is downloaded or sent.
8. The graph reads every block; a block in the person's own words counts.

## Version 1 (this build)

In:

- The blocks of the newest analysis (`FactBook` of one profile), read through a route.
- The uploaded resume as a resume: every resume-sourced bullet, in the order of the file, in its own words.
- A path resume for a next role, made when the person asks for it: bullets ranked against the role's requirements,
  selected and ordered by code, each in the fact's own words. One relevance call (Haiku), lexical fallback.
- Draft edits: move a bullet up or down inside its job, leave it out, put a block back, type any wording, reset.
- Finalise: every typed line is checked in code against the facts it cites (`match/verify.py`). What no fact
  supports becomes a finding with two answers: "it is mine" (the line becomes a fact in the person's own words,
  stored in `user_facts`) or "undo". With no findings left, the files are exported and read back (`match/lint.py`).
- Make default. Download PDF and Word of the finalised version.
- The page `/resume`: the list of resumes, the one in view as lines, the pressed line's source and actions, the
  blocks left out, the Finalise view, and the blocks view.

Not in version 1 (each is a later wave):

- Merging a second upload into the same blocks, and showing disagreements. Blocks are per profile for now; an
  account-level store with a stable key per block comes with merging.
- A wording accepted in one resume offered to the others.
- The coach proposing a rewrite against a line (a chat tool). Version 1 shows the existing coach beside the page; it
  answers and cannot change the resume.
- The grammar check. No such check exists in the app.
- Saving a Match resume into the list. Apply for me sending the default. A frozen copy per application.
- The graph computed from every block instead of one analysis.
- The original uploaded file as a download: the analysis keeps its text, not its bytes.

## Contract

`shared/resume.ts`, mirrored by `backend/tayyari/contract/resume.py` (`tests/unit/resume/test_contract_parity.py`).
It reuses `Fact`, `ResumeVariant`, `Bullet` and `LintReport` of `shared/match.ts`.

## Storage

`resumes` (migration 0018, additive):

| column | type | note |
|---|---|---|
| id | String(ID_LEN) pk | random |
| user_id | String(ID_LEN) FK users CASCADE, NULL | NULL for a profile with no account, like `matches` |
| profile_id | String(ID_LEN) FK profiles CASCADE | |
| kind | String(8) | `upload` or `path` |
| role_key | String(64) | the role's id for a path, `''` for the upload |
| title | String(255) | |
| draft | JSON | `ResumeVariant` |
| final | JSON NULL | `ResumeVariant` with its lint, as finalised |
| facts | JSON | the `FactBook` as it stood (as `matches.facts` does) |
| is_default | Boolean | |
| version | Integer | bumped on every write of the draft |
| created_at, updated_at | UtcDateTime | |
| finalised_at | UtcDateTime NULL | |

Unique `(profile_id, kind, role_key)`. Index on `profile_id`.

## Routes (`api/routes/resume.py`)

Who may call: the same rule as Match (`user_or_anonymous(..., anonymous_allowed=container.match_anonymous)`), and
the profile must be one that caller may open (an account's own profile; an unclaimed profile for a caller with no
sign-in while anonymous matching is on). Another account's profile or resume is a 404.

| method and path | body | answer |
|---|---|---|
| `GET /api/resumes?profile=<id>` | | `ResumeList`. Creates the uploaded resume on the first call. |
| `GET /api/resumes/blocks?profile=<id>` | | `BlocksResponse` |
| `POST /api/resumes/path?profile=<id>` | `PathRequest` | `ResumeDetail`. The one already made for that role, else made now. Spend is reserved first. |
| `GET /api/resumes/{id}` | | `ResumeDetail` |
| `PATCH /api/resumes/{id}` | `ResumeOpsRequest` | `ResumeDetail`. All ops apply or none (422 names the first that does not fit). |
| `POST /api/resumes/{id}/finalise` | `FinaliseRequest` | `FinaliseResponse` |
| `POST /api/resumes/{id}/default` | | `ResumeDetail`. 409 `not_finalised` unless a finalised version exists. |
| `GET /api/resumes/{id}/resume.pdf`, `.docx` | | the file of the finalised version. 409 `not_finalised` without one. |

`/resume` is added to `APP_ROUTES` (`api/static.py`).

## Service (`backend/tayyari/resume/`)

Pure functions over a `FactBook` and a `ResumeVariant`, plus one service class wired in `container.py`.

- `upload_variant(book)`: header from the book; each experience and project entry with its bullets in source order,
  each bullet `fallback_bullet` (the fact's own wording, `exact`, `verified`); education; skills from the evidenced
  skill facts. Facts whose every ref is the resume. No model call.
- `path_variant(book, role, ...)`: a requirement table built in code from `Role.requirements`; relevance through
  `variant.score_relevance` (lexical fallback); selection through `variant.select`; every bullet in the fact's own
  wording. Facts from GitHub or Lovable that score for the role may be surfaced, as Match does.
- `apply_ops(variant, book, ops)`: `move` swaps a bullet with its neighbour inside its entry; `leave_out` removes it;
  `put_back` appends the block to its entry as `fallback_bullet`; `edit` sets the text (trimmed, at most
  `MAX_LINE_CHARS`, no newline) and marks the bullet `needs_user` until it is finalised; `reset` restores the fact's
  wording. Titles, employers and dates cannot be edited.
- `marks(variant, upload)`: `moved`, `edited`, `added` per bullet against the uploaded resume; `changes` counts
  them plus the bullets left out.
- `finalise(variant, book, answers)`: for every edited bullet, `verify.check_text` against the facts it cites. An
  unsupported figure, scope verb, term or name is a `Finding`. `mine` turns the typed line into a user fact
  (`facts.user_fact`), adds it to the book and to the bullet's `factIds`, and the bullet is checked again; `undo`
  resets it. When nothing is left: every bullet `verified`, then `MatchService.export_resume` (files and read-back).
  The exported `variant` with its lint is stored as `final`.

Rule 1 holds: Leafout writes nothing here. Every line is a fact's own words, or the person's, and a typed claim
no source has is stored as said by the person.

## Checks and tests

- Unit: `upload_variant` on the golden fact books (bullets in source order, nothing invented); `path_variant` with
  the fake Claude and with the lexical fallback; every op, and each refusal; `marks`; `finalise` with a known-bad
  line (a figure no fact has must produce a finding) and its two answers.
- Integration: one test per route, including another account's resume (404), a download before finalise (409), and
  the default moving from one resume to another.
- Contract parity for `shared/resume.ts`.
- Frontend: `npm run check` (types, palette, restraint, build). A `/dev/resume` gallery from fixtures.

## Waves and files

- Orchestrator: `shared/resume.ts`, this file, the pointer in `docs/roadmap.md`, `CLAUDE.md` status.
- Backend wave: `backend/tayyari/contract/resume.py`, `backend/tayyari/resume/**`, `storage/tables.py` (one table),
  `storage/ports.py`, `storage/repos/resumes.py`, migration `0018_resumes.py`, `tests/fakes`, `container.py`,
  `api/routes/resume.py`, `api/routes/__init__.py`, `api/static.py` (`APP_ROUTES`), `backend/tests/**/resume/**`,
  `docs/backend.md`.
- Frontend wave: `src/resume/**`, `src/styles/resume.css`, the route in the app shell, one link to `/resume` from a
  place that is not the dashboard files (`src/views/Dashboard.tsx`, `src/views/dashboard/**`,
  `src/styles/dashboard.css`, `src/dev/design/Screens.tsx` are being changed by another session and are off limits).
