# Handover: Leafout, as of 2026-11-16

The owner is moving to a new computer. This file is the starting point for any agent picking up the work: where everything lives, how to set up a fresh machine, what is half-done, and what waits on the owner. Nothing below depends on the old laptop.

Read in this order: this file, `project-soul.md`, `CLAUDE.md`, then the section of `docs/roadmap.md` for the work you take on. `CLAUDE.md` is still the rulebook; this file only says where things stand.

## 1. What exists

| Thing | Where | State |
|---|---|---|
| The app (code name Tayyari, product name Leafout) | `github.com/Aman8T/onwardsAi`, branch `main` @ `7af4e73` | Live at https://app.leafout.in (FastAPI Cloud app `599a983d-3900-4d21-8261-180cef4d1eca`, last deploy `52aa77d4`, 2026-11-12) |
| The marketing site leafout.in | `github.com/Aman8T/tayyari-career-intelligence`, `main` @ `4046f57` | Built in Lovable; pushing `main` syncs to Lovable, the owner publishes there |
| Design canvases | claude.ai artifacts, private to the owner's account (section 6) | Source copies of the recent ones are in the private handover archive |
| Unmerged work | feature branches on GitHub (section 3) | All pushed 2026-11-16 |
| Private handover archive | not on GitHub: this repo is public. A git bundle the owner carries over (section 2, step 9) | Canvas sources, Claude memory notes, the sessions' hand-over notes |

## 2. Setting up the new computer

1. **GitHub over SSH.** Make a new key, add it to the Aman8T account, and add a `Host github.com` block that uses it. The old key `id_ed25519_github` stays on the old laptop.
2. **Clone both repos.**
   ```bash
   git clone git@github.com:Aman8T/onwardsAi.git
   ```
   ```bash
   git clone git@github.com:Aman8T/tayyari-career-intelligence.git
   ```
3. **Commit identity, per repo, never global.** Use the personal identity `Aman8T` with the email the earlier commits carry (`git log -1 --format='%an <%ae>'` shows it). Check `git var GIT_AUTHOR_IDENT` before the first commit on a new machine.
4. **Tools.** Node 22+ with npm, Python 3.13, and `uv`. Then `npm ci` at the repo root and `uv sync` in `backend/`. On the old laptop Node came from `uv tool install nodejs-wheel`; a normal Node install is fine on a new machine.
5. **Secrets.** Create `.env` at the root of the onwardsAi checkout (gitignored). The old one held two names: `ANTHROPIC_API_KEY` and `BROWSER_USE_API_KEY`. Copy the values over by hand or issue new keys. Every other variable is documented in `backend/.env.example`. Without a database setting the app uses local SQLite.
6. **Run it.** `npm run dev` (API on 8787, web on 5173), then `npm run check:all`. The first Vite start after a fresh install can take two to three minutes before it serves a module; it is not hung.
7. **FastAPI Cloud.** Log in again with `fastapi login` (the old laptop's token had expired by 2026-11-16, so no deploy could be confirmed from it). Deploy only when the owner asks: `npm run deploy -- --app-id 599a983d-3900-4d21-8261-180cef4d1eca`. On Windows set `PYTHONUTF8=1`.
8. **This repo is public; the site repo is private.** `Aman8T/onwardsAi` is world-readable on every branch, while `tayyari-career-intelligence` (and CrackIt's `topper-tribe-ai`) are private. Anything pushed to onwardsAi can be read by anyone: no resumes, personal portfolios, employer details, machine paths or memory notes. Personal material goes in a private repo or stays local.
9. **The private handover archive.** It travels as one file, `leafout-handover-archive-2026-11-16.bundle` (made on the old laptop next to the `Main App` folder). On the new machine, inside a clone of this repo: `git fetch <path-to-bundle> archive/local-machine-2026-11-16:archive/local-machine-2026-11-16`, then check out that branch. Its `handover-archive/README.md` explains the three folders: `canvases/` (sources of the starred canvases in section 6), `claude-memory/` (notes earlier Claude Code sessions kept; several describe the old laptop only, so read before restoring), and `side-session-notes/` (the briefs and findings sessions left for each other, by session id). To keep it on GitHub, push the branch to a **private** repository, never to this one.

## 3. Branches

### onwardsAi

| Branch | Ahead of main | What it is | Next step |
|---|---|---|---|
| `main` | — | Everything live: dashboard, materials kit on every screen, /resume v1–3, Ask dock, auto-apply A1, neutral stage | — |
| `portfolio/neon-coast` | 4 commits | A third portfolio skin, "Neon Coast" (GTA 6 poster mood, no Rockstar assets). `src/portfolio/coast/`, `src/styles/coast.css`, `SkinPicker.tsx`, `?view=coast`; `design.md` §9a | Owner: confirm the name, merge or not, and whether the editor's choice sets the published default. Not checked in Safari, Firefox, `/p/<id>`, print, reduced motion |
| `fix/auto-apply-e2e` | 1 commit (`9f468af`), 29 behind | Why no live auto-apply run finished: Lever's hidden hCaptcha was treated as a wall, Greenhouse's S3 upload host was blocked. `check:all` passed | Merges into main cleanly. Merge, deploy, then one real submit |
| `feat/inbound-email` | 2 commits (`c904d8e`, `8577231`), 29 behind | A2: a per-user application inbox on Resend Receiving (`apply.leafout.in`), research `docs/research/inbound-email-tracking.md`; plus the end-to-end auto-apply design draft `docs/auto-apply-e2e-design.md` with decisions D1–D14 open | Owner decisions D1–D14 (section 5) first. The merge conflicts in 11 files (`api/routes/__init__.py`, `container.py`, `storage/ports.py`, `storage/repos/__init__.py`, `storage/tables.py`, `tests/fakes/repos.py`, `tests/unit/test_layering.py`, `docs/auto-apply.md`, `docs/backend.md`, `docs/roadmap.md`, `package.json`). Its migration `0018_inbound_email` collides with main's `0018_resumes`: renumber it 0022 with `down_revision` 0021. Then Resend and DNS setup |
| `docs/career-ops-2026-11` | 1 commit (`55717ea`) | career-ops added to `docs/research/open-source-resume-tools.md` | Fast-forwards. Open the PR: https://github.com/Aman8T/onwardsAi/pull/new/docs/career-ops-2026-11 |
| `exp/auto-apply-test` | — (old base) | The auto-apply experiment that chose Claude + Browser Use (`experiments/auto_apply/`) | Reference only |
| `design/audit-pass`, `feat/auto-apply-build`, `feat/auto-apply-v2` | 0 | Already in main; pushed only so nothing exists on one machine | Can be deleted |
| `feat/auto-apply`, `claude/hopeful-thompson-x6arb8` | — | Older: the first auto-apply attempt (fully in main) and a hand-over note from 2026-11-05. `docs/handover.md` on `claude/hopeful-thompson-x6arb8` has the repo map, gate results and the ONW-nn open points of that date | Reference only; check its open points against today's main before acting on one |
| `claude/charming-ride-ojz4kd` | — | A work log of 2026-11-05 plus launch-audit fixes (`8e15cea`: no model names on screen, dated match checks, no board fetches, honest Weekly news, Indian amounts, postings retention, match API contract) that are **not in main** | Decide whether each fix still applies; merging conflicts in 8 files |

### tayyari-career-intelligence

`main` and `theme/neutral-stage` are both at `4046f57` (the site on the neutral stage and the five materials). Nothing unpushed.

## 4. Production state

- **Live on app.leafout.in since 2026-11-12 (deploy `52aa77d4`)**: everything on `main`. Auto-apply is switched on in production (`AUTO_APPLY_ENABLED=true`) but no live run has finished yet; `fix/auto-apply-e2e` is the fix and is not deployed.
- **Set on FastAPI Cloud** (names only): `BROWSER_USE_API_KEY`, `AUTO_APPLY_ENABLED`, `DATABASE_URL` (Neon Postgres), plus the earlier ones in `backend/.env.example`. `apply_credit_cost` is not set and there is no auto-apply price.
- **Off until the owner sets secrets**: Leafout sign-in (L1), the Weekly, Razorpay live sales, Resend inbound email.
- **The old laptop's network blocked app.leafout.in**, so the 2026-11-12 deploy was never looked at in a browser. Check https://app.leafout.in/dashboard from the new machine.

## 5. Waiting on the owner

**Auto-apply**
- D1–D14 in `docs/auto-apply-e2e-design.md` (branch `feat/inbound-email`). The two most pressing: D1, CAPTCHA "pass, never solve" (allowing a hidden CAPTCHA at submit is a rule change to `CLAUDE.md` rule 9); D2, which email goes on application forms. The owner was uneasy about `@apply.leafout.in` and about touching users' Google accounts; the last open question was whether to shortlist a neutral domain.
- A price and `apply_credit_cost`, the re-fill limit and queue order, a retention rule for half-done applications, approving the §3.3 "As built" deviations, and the $20 FastAPI Cloud plan.
- Stuck `needs_signin` rows still hold credits (one free user got a 402): add "Try again" or release them.
- The landing site advertises Apply for me while it has no price. Enable it properly or hold that section back.

**Design**
- Payments: pick one of four directions on the payments canvas (A the bill, B what you hold, C the ledger, D the tag). The session recommended A, with D's price on the posting for the paywall. Missing pieces: receipt number, GST line, invoice download, a ledger or orders route, "Try again".
- Neon Coast: name, merge, default (section 3).
- Apply review screen: a confidence word only on model-written fields (the spec wants every field), the tab says "Posting" where the spec says "Job", "Approve and send" is one click, the start chip still says "Needs your sign-in".
- Landing: whether the role strip's endless loop stays (it breaks the no-infinite-animation rule); the canvas hero still shows the old headline.
- Smaller: a quieter "Search again" on Discover's error state; the backend wording "1 hard condition aren't evidenced yet" in `backend/tayyari/match/coverage.py`; the quotes in `views/dashboard/lines.ts`; whether /resume gets a nav slot; whether the "Wide" layout goes back to 920 px.
- Not seen on any screen yet: sign-out, Analysis done and error, loading states, tablet widths, keyboard and screen reader.

**Ideas parked by the owner**
- Resume swarm (five simulated readers quote remarks against resume blocks, no score, about $0.003 a panel). Paused; two questions open: do readers talk to each other, may a reader give a verdict. MiroFish is AGPL: borrow ideas, copy no code.
- The 3D desk (two canvases): a "brag" piece that may break the design rules; suggested behind a flag as a third view.
- Room ideas: keep only B, the evidence wall, for a later dashboard pass.
- Pandavas career agents: Krishna coordinates; Yudhishthira values and offers; Bhima applications and practice; Arjuna skills and courses; Nakula brand and profile; Sahadeva market, events and network. Modes named from the epic (Vanvas, Agyaatvaas, Chakravyuha, Lakshagriha, Rajasuya, Kurukshetra), an "Akshaya Patra" opportunity feed and a "Council" view. Suggested MVP: Krishna, Arjuna and Bhima. It exists only as a conversation; no canvas yet.
- Razorpay "AI Builders": it is a job listing, not a programme. Apply or not is open.
- The skills table (X1) is the next item in `docs/roadmap.md`.

## 6. Design canvases

All are claude.ai artifacts in the owner's account; they open from any computer signed in to it. Sources for the starred ones are in the private handover archive under `handover-archive/canvases/` (section 2, step 9).

| Canvas | Link | What it settled |
|---|---|---|
| ★ Leafout All Screens | https://claude.ai/artifact/TyGbAo9C2GDWrXB9nxckei | Every app screen on one shared stylesheet; the empty /dashboard; the "read" ideas |
| ★ Home, Ask and Thinking | https://claude.ai/artifact/BCWRN3S1E3ue6w9jEY566c | The one-screen Home (`HomeOne`), Ask dock, thinking stream, /resume, neutral-stage boards. Built and live |
| ★ Themes | https://claude.ai/artifact/SV9MijVdyRTVxBoe8HmwSy | Spotlight chosen: neutral black/grey stage, colour only as accent and light |
| ★ Payments | https://claude.ai/artifact/E53zzSqgikQ3BLku9SnSaB | Four directions, none chosen |
| ★ Neon Coast portfolio skin | https://claude.ai/artifact/64daa4foX7TSvr7R9gDcYC | The skin on `portfolio/neon-coast`, with the owner's own portfolio |
| ★ The Portfolio, 5 Directions | https://claude.ai/artifact/SZbjdWY12PKRCBFJrMBzyY | Five GTA-style portfolio directions that led to Neon Coast |
| ★ Landing Canvas | https://claude.ai/artifact/KbrS3F2vq6wWvZAjmZ1EkK | The landing page as shipped on 2026-11-09 |
| Dark Mode Explorations | https://claude.ai/artifact/6pLcBR45SWHWTihoFffZji | Direction D applied to the quiet dark table (2026-11-07) |
| 3D Desk / Real Desk | https://claude.ai/artifact/5UEkkUWTZLutortFhyeU94 · https://claude.ai/artifact/LN35HhzrcfWLvCS43HmCCN | Parked |
| Your Room Ideas | https://claude.ai/artifact/5fACJXbtSCVQmEg56SsqJ6 | Keep B, the evidence wall |
| Auto-apply (A1) | https://claude.ai/artifact/CsN9PPninRZ3Z1j5vx6Nh5 | Screens built and live |
| Older feature canvases | listed in `docs/roadmap.md` and `CLAUDE.md` | Posting match, Discover, Practice, Prepare, Re-drill, Radar card, Weekly, Pricing, calm layer, portfolio kit, cursor light |

Earlier explorations in the same account: Leafout mark options, logo and mascot, landing variants (minimal, loud, scroll states, front-page section), breathing, colour and fluid pass, Home dashboard, Modes and Features, Tayyari Dark Mode, Leaf radar experiments, Living Dragon, Assisted Apply. `Artifact` list in Claude finds them by title.

## 7. Sessions on the old laptop

One line each; the full notes the sessions left for each other are in the private archive under `handover-archive/side-session-notes/<session id>/`.

| Session | Did | Ends in |
|---|---|---|
| "/ screen" (main design thread) | Dashboard and every screen redesigned canvas-first; desk, read, /resume v2–3, neutral stage, materials kit; deployed 2026-11-12 | `main`; notes `local_493a75b9…/` (theme, landing, payments, resume briefs) |
| Unified screen design (this one) | All Screens canvas; this handover; pushed every branch | this file, the private archive |
| Dark mode theme improvements | Dark direction D; nine-point design audit on app and landing; auto-apply screen audit; deploy fix (Python 3.13, `--app-id`) | `main`, landing `main`; notes `local_d3eba6a5…/og-default-redraw.md` |
| SSH setup and repo clone | SSH and landing clone; reviews of old auto-apply, Cloudflare Browser Run (not a drop-in), Browser Use `liveUrl` as a hand-off; career-ops survey | `docs/career-ops-2026-11` |
| SSH setup and repo clone (fork) | Auto-apply A1 end to end: experiment, spec, build, deploy; inbound email; e2e design draft | `main`, `exp/auto-apply-test`, `feat/inbound-email` |
| Diagnose why auto-apply never finishes | Lever hCaptcha and Greenhouse S3 fixes | `fix/auto-apply-e2e`; notes `local_37271ee4…/auto-apply-e2e-findings.md` |
| Landing page: new theme and style | Landing on the neutral stage and the five materials | landing `main` @ `4046f57` |
| landing Page | "Apply for me" section, sourced stats, role strip, sign up / log in in place of upload | landing `main` (superseded) |
| Light/dark theme and always-moving colours | Themes canvas; neutral stage agreed; Ask dock shrink (now on main) | Themes canvas |
| Payment pages | Four payment directions, canvas only | Payments canvas; notes `local_493a75b9…/payments-canvas-handover.md` |
| Resume page: one resume, many combinations | Brief, boards, build of /resume (merged `168b408`) | `main`; notes `local_493a75b9…/resume-*.md` |
| Resume panel: swarm of simulated readers | Discussion, paused | notes `local_c53cecdd…/` |
| GTA 6 styled portfolio edition | Neon Coast skin | `portfolio/neon-coast`; notes `local_2c21c32c…/neon-coast-skin.md` |
| 3d leafout · unique IDeas design | Desk and room canvases | canvases above |
| Redraw og-default.png | New 1200×630 share image | in both repos' `main` |
| Pandavas career agents concept | The concept in section 5 | conversation only |
| Leafout project story write-up | A 296-character "story behind it" answer for an application form | conversation only |
| Razorpay AI Builders eligibility | It is a job listing | conversation only |

## 8. What did not move, on purpose

- **`.env`** with the two API keys: secrets never go to git (section 2, step 5).
- **The local SQLite database** (`%LOCALAPPDATA%\tayyari\tayyari.sqlite3`): dev data, including the owner's own analysis used for the Neon Coast canvas. Re-run the analysis on the new machine from the resume.
- **The owner's own resume** and any other personal files: copy them by hand.
- **The private handover archive**: deliberately not in this public repo (section 2, step 9).
- **`experiments/auto_apply/data/`** in the old main checkout: a test resume and two guard zips, gitignored by design; `exp/auto-apply-test` explains how to regenerate them.
- **Scratchpad leftovers**: screenshots, task logs, CSS backups and a partial patch from build waves since committed. Nothing in them is needed.

## 9. How the owner works

- Design on a canvas first with creative freedom; the owner picks, then it is built and checked in the browser.
- A hand-touched, lived-in look: no card grids, no shadows except a small one on paper, a black or grey stage with colour only as accent and light. "Think like a user, audit, then act like a UX expert."
- Commit, push and deploy only when the owner says so. The owner signs in and sets secrets himself.
- Move fast, ask only when blocked, commit after each step; parallel subagents are welcome, cheaper models where they suffice.
- Brief answers, costs in rupees, short copy that uses its whole character limit.
