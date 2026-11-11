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

Not in version 1 (the grammar check, the coach's rewrite, wordings and saving a Match resume were built in version 2
below; merging a second upload and the original file as a download in version 3; the graph read from every block in
"The graph reads every block" below; what is left is a later wave):

- An account-level store of blocks shared by several profiles: blocks are still per profile.

## Version 2 (2026-11-11)

Four of the version 1 gaps. Contract in `shared/resume.ts` (and `ChatResume` / the `rewrite` event in `shared/types.ts`),
mirrored by `contract/resume.py`, `contract/requests.py`, `contract/events.py`. No migration: `resumes.kind` is
`String(8)` and `posting` fits. A line is always addressed by its `bulletId` (`b-<block id>`, the same on every resume);
accepting any proposal below is the existing `edit` op, `{ op: 'edit', bulletId, text }`, so Finalise still checks the
line.

1. **The grammar check, at Finalise.** `POST /api/resumes/{id}/grammar` (no body) answers
   `GrammarResponse { proposals: GrammarProposal[]; ran: boolean }`, `GrammarProposal { bulletId; before; after; why }`.
   One Haiku call for the whole draft (`resume.grammar`, structured output, thinking off, spend reserved first, counted in
   the per-address `chat` window). Code keeps a proposal only if it differs from the line, is one line within
   `MAX_LINE_CHARS`, holds no `[x]`, and passes `resume.grammar.same_facts`: `match.verify.check_text` both ways (no
   figure, name, tool or role term added or dropped), the same scope verbs, no word of substance added or dropped (a
   function word, or a spelling or inflection of a word that goes, is allowed). A refused proposal is logged with its
   reason and never shown. Nothing is stored. With no key or on a model failure: `proposals: []`, `ran: false`, never a 500.
   Known-bad fixtures: a "fix" that changes a number, adds a claim, drops a name (and swaps a name, raises the scope).
2. **The coach proposes a rewrite against a line.** `ChatRequest.resume?: { id }`. When the caller may open that resume by
   the resume routes' rule (and it is a resume of the profile being chatted about; otherwise ignored silently), the coach
   is shown a "resume in view" block after the cached blocks and the desk: each line (at most 40) with a short id, its
   words and up to two quotes of the facts it rests on, fenced as data. The coach may end an answer with
   `[[rewrite: <line id> | <new text>]]` (one per answer, never with a button). Code validates: the id was shown this
   turn, one line within `MAX_LINE_CHARS`, no `[x]`, different from the line, and `resume.finalise.check_line` passes
   against the facts the line cites. A valid one is the chat event
   `{ type: 'rewrite', resumeId, lineId, before, after }` (`lineId` is the `bulletId`); an invalid one is dropped and
   logged (reason only). Trailers: the filter holds `[[rewrite: …]]` back exactly like `[[action: …]]`; with more than
   one trailer at the end the first valid one is kept and the rest are dropped as `multiple` (this changes version 1
   chat behaviour, where two action lines were both dropped).
3. **A wording accepted in one resume, offered to the others.** `GET /api/resumes/{id}/wordings` answers
   `WordingsResponse { wordings: LineWording[] }`, `LineWording { lineId; text; fromResumeId; fromTitle }`: for each line
   of this draft, the finalised wordings of the same block(s) in the profile's other resumes (finalised version only;
   never the line's own current text nor the block's own wording; de-duplicated; newest first; at most
   `MAX_WORDINGS_PER_LINE` = 3). Pure code, no model call.
4. **Saving a Match resume into the list.** `POST /api/resumes/from-match` with `FromMatchRequest { matchId }` answers
   `ResumeDetail` for a new resume of kind `posting` (`ResumeSummary.matchId` set, `roleId` absent, `isDefault` false):
   the match's tailored variant with its facts as they stood. Finalised if the match had exported it; otherwise a draft
   (a `[x]` line goes back to its block's own wording, open questions are dropped). Idempotent. The match must be the
   caller's (another's is the same 404 as an unknown one); 404 when no resume has been built for it. It appears in
   `GET /api/resumes` with `kind: 'posting'`.

## Version 3 (2026-11-11): a second file

Decisions 1 and 5: every upload merges into one set of blocks and is also kept as its own resume; where two uploads
disagree each keeps its own words, new resumes follow the default, and the difference is shown. Contract in
`shared/resume.ts` (`MergeReport`, `UploadResponse`, `ResumeFile`, `BlockWording`, `BlockDisagreement`), mirrored by
`contract/resume.py` (parity test updated). Code in `resume/merge.py` (pure), `resume/files.py`, `ResumeService.upload_file`.

**Model.** A profile's blocks are the `FactBook` of its newest analysis, plus one more reference per file that says the
same thing, plus the new blocks a file brings. A file is read by the analysis's own reader (`ingest.resume.parse_resume`)
and its facts come from the analysis's own extractor (`match.facts.extract_portfolio_facts` then `build_facts`, purpose
`match.facts`, one Haiku call, the file's text fenced as a `<source>` block of numbered lines and every quote checked
against the file by code; the profile's skills are given so the file's evidenced skills become skill facts). Each new fact
cites the file as its source (`docId` = the file's id). Then `merge(existing_book, new_facts)` places every fact of the file
in exactly one section, in code, with no model call. The file also becomes a `resumes` row of its own (`kind` `upload`,
`role_key` the file's id; the first resume keeps `''`), made by `upload_variant` over the file's facts, in the file's
order and words, finalised at once, never the default.

**The merge** (constants in `core/limits.py`, `MERGE_*`):

| section | when | what is kept |
|---|---|---|
| `same` | the same words up to a stop word, plural or punctuation (Jaccard of content words >= 0.85) with the same figures and dates; a matched entry with no conflict; the same skill (`match.aliases.same_term`) | the file is another reference of the block |
| `new` | no counterpart, or a line of an entry the book lacks | a new block (a new entry for a new job, project or degree) |
| `near` | the shorter line's content words are >= 0.5 in the other and >= 3 are shared, and no conflict | the block keeps its wording; the file's wording is listed beside it, tied to the file |
| `disagree` | related as for `near`, and each side has a figure (a number with its unit, `measures_of`) or a year or fiscal year the other lacks; for an entry: the title (Jaccard < 0.85), the employer (overlap < 0.6) or a start or end date more than 3 months apart | the block keeps its words; the file keeps its own in its resume; `differs` lists `figure`, `date`, `title`, `employer` |

An entry matches when two of three signals agree: employer (word overlap >= 0.6), title (>= 0.6) and overlapping periods; an
entry with no employer and no dates matches on its title alone. A line is compared only with the lines of the entry its
entry matched; lines of no entry (awards, certificates, details) only with each other. Each existing block is claimed by at
most one line of the file, best pairs first, so the order of the file never decides. Known-bad fixtures
(`tests/unit/resume/test_merge.py`): two bullets that differ only in a number are `disagree`, never `same`; a reworded
bullet is `near`, never `new`; an unrelated bullet is `new`.

**Block ids.** An existing block keeps its id for ever (stored resumes keep working). A new block's id is `m-` and ten hex
characters of a hash of its kind, its folded words and its entry's id (`-2`, `-3` for a repeat inside one file), so it
depends on nothing a later file can change; a block a third file also says is the same block. Merging is pure and never
changes an old block's text, kind or position. The file's own book (the facts of its resume) carries the file's words under
the id of the block each fact was placed on, so `b-<block id>` is the same line id on every resume that has the block.

**Storage** (migration 0021, additive; chains from 0020 `0020_application_resume_copy`): `resume_files` (`id`, `user_id`
NULL, `profile_id`, `origin` `analysis` or `upload`, `filename`, `mime`, `size`, `sha256`, `data` bytes, `text`, `facts`
the `FactBook` as extracted, `merge` the report as sent, `merged_book` the blocks after this file, `created_at`). The newest
file's `merged_book` is the person's blocks (`merged_book(base, files)`); with no upload the book of the first resume is.
`GET /api/resumes`, `/blocks` and a new path resume all read it. At most `MAX_RESUME_FILES` (8) files a profile (409
`too_many_files`). Uploading the same bytes again answers with the first result and makes no model call.

**The first file.** When a profile is created from a file, `analysis/service._persist` stores that file (`origin`
`analysis`, no merge) next to the saved profile; a failure is logged and never fails the analysis. Profiles analysed before
this have no stored bytes: their first resume answers 404 `no_original`.

**Routes** (same caller rules as the other resume routes):

| method and path | answer |
|---|---|
| `POST /api/resumes/upload?profile=<id>` | multipart, one file in the field `resume` (PDF, DOCX, TXT, MD; the analysis upload's size and type limits, 413 / 400) -> `UploadResponse { resume, merge }`. Spend is reserved first (`EST_MATCH_USD`, twice when the profile has no uploaded resume yet) and the call counts in the `chat` window. 400 `unreadable_file` for a file with no readable text or nothing to extract |
| `GET /api/resumes/files?profile=<id>` | `ResumeFilesResponse`: id, filename, mime, size, uploaded date, `resumeId`, and counts per section (none for the analysis file) |
| `GET /api/resumes/{id}/original` | the stored bytes with their content type, `attachment`, `nosniff`, an ASCII-safe file name; 404 `no_original` for a path or posting resume and for a resume with no stored file |
| `GET /api/resumes/blocks` | each block may carry `wordings` (other files' `near` wordings, with file and resume) and `disagreements` (with `differs`) |

`ResumeService.all_resume_texts(profile_id) -> list[ResumeText]` (`file_id`, `filename`, `text`, `created_at`, `origin`),
newest first, is the one function the graph will read; it is empty for a profile analysed before files were kept.

Cut: the file's contact lines are not merged into the header; no way to resolve a disagreement (it stays listed); a
merged-in block is not offered by the wordings route (the blocks route lists it).

## The graph reads every block (2026-11-11)

Decision 8. "Update my graph from all my blocks" is a full analysis that starts from a profile instead of an upload:
`POST /api/analyze` with the form field `fromProfile=<profile id>` (every other field is ignored; the upload path is
unchanged). Same readers, synthesizer, guards, rate window, spend reservation, stream and storage; the only new words are two
headings. Code in `resume/reread.py` (pure), `api/reread.py`, `analysis/service._carry_over`, `storage/repos/carry.py`.

- **Who.** The resume routes' rule (`Container.match_anonymous`, 401 otherwise): an account's own profile, or with no sign-in
  one nobody has claimed; a sample run, another account's profile and an unknown id are the same 404, before the rate-limit
  counters and the spend reservation are touched.
- **The resume source.** The text of every stored resume file (`ResumeService.all_resume_texts`), oldest first, each under
  `Resume file: <name> (<date>)`, de-duplicated by the sha256 of their whitespace-folded text (the newest copy stays), capped
  at `RESUME_MAX_CHARS` (the analysis's own resume limit). The own-words section is reserved first
  (`In your own words (stated by you, not from a document)`, one `- line` each, the lines typed on a resume and vouched for:
  `user_facts` whose references are `resume:…`, at most 4 000 characters, the newest kept); then files are taken newest first and
  kept whole. What does not fit, and the oldest own-words lines that did not, are named in a log line of the run ("Reading 3
  resume files and 4 lines in your own words; left out, over the 30,000-character limit: a.pdf (2026-11-01)"), never dropped
  silently. A newest file that alone exceeds the cap is cut at its end and says so. The text is handed to the pipeline as a
  text resume, so it is fenced as untrusted exactly like an upload and the readers may cite the own-words lines as evidence.
- **Fallbacks.** A profile analysed before files were kept uses the resume text its stored context keeps (heading `Resume (the
  text your last analysis kept)`). With neither: 409 `nothing_to_reread`.
- **Links, notes, goal.** The links and the notes of the last analysis (its stored sources) and the profile's goal are used again.
- **Carrying.** A re-read makes a NEW profile id. When it is stored, `ProfileCarryRepo.carry(old, new, user_id)` moves in ONE
  transaction: the `resumes` (the default flag with them), the `resume_files`, the `user_facts` (their key is a hash of the
  profile id, recomputed) and the account's `desk_notes`. The new profile's clashing rows (same resume kind and role, same
  note id, same line) are replaced by the moved ones, and the moved default stays the one default. A signed-in caller also claims
  the new profile in that transaction. Only the account that may open the old profile may carry (`PermissionError`
  otherwise, nothing changes). Applications, matches, practice sessions and plans stay with the profile they were made for. A
  re-read keeps no new file of its own (the combined text is not stored as a resume file). If the move fails, it is logged
  (`reread_carry_failed`), the new profile stands, the old rows stay where they were, and the final event says so.
- **The event.** `{ type: 'done', profile, carried?: boolean }`: `carried` is present only on a re-read.
- **The screen.** `actions.rereadGraph()` runs it through the existing analysis flow (the Analysis screen, `previous` kept,
  the dashboard after; `analysis.carried === false` says the move failed). `src/resume/RereadGraph.tsx` is the card.

## Sent with Apply for me (2026-11-11)

"Apply with my current resume" sends the default resume's finalised file (PDF, the same bytes as the download), and a
default that is only a draft is never sent. At the moment an application's values are prepared it freezes a copy: the
resume's id, title, finalised variant and the file hash that is part of the approval digest (`applications.resume_copy`,
migration 0020). A later edit or a new default never changes it, and a default that changes before approval fails the
digest re-check. The application's page says "Sent with: <title>, as finalised <date>". Spec: `docs/auto-apply.md` 3.4.

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
| kind | String(8) | `upload`, `path` or (version 2) `posting` |
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
