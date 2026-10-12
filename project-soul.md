# Tayyari: the soul of the project

*Tayyari* (तैयारी) means preparation. This document says why the project exists, what it refuses to do, and what every screen and every line of code is in service of. Read it before touching anything. If a change makes sense technically but fights this document, the document wins.

## The idea in one breath

Hiring broke in 2026. Everyone applies to everything with AI-written resumes, so a resume no longer tells an employer anything, and employers answer with AI screens and silence. The people hurt most are the ones with the least signal to begin with: students, career changers, anyone outside a famous company.

Tayyari takes the other side of that trade. Instead of helping people claim more, it helps them **prove** what they already have, see exactly what a role expects that they can't yet show, and close that gap with work rather than words. Every statement the product makes about a person points at a line in their resume, a repository they wrote, a page they built, or something they said in their own words. Nothing else is allowed to exist.

The line we live by: **Tayyari doesn't write claims. It proves them.**

## Who it is for

- **Students in India**, directly, not through colleges. Most are unplaced at graduation and are told to pad resumes and stuff keywords. They can't afford a ₹2,000-a-month tool and shouldn't have to buy a year of anything to find out if it helps.
- **Working professionals anywhere, in any profession.** A nurse, an accountant, a marketer and an engineer get the same product. The resume is the universal evidence; GitHub, deployed apps and public links are extras that strengthen it when they exist.
- **All jobs in India**, not only technology. The role vocabulary comes from official occupation lists (ESCO, O*NET, bridged to India's NCO through ISCO codes), with the posting's own words always winning.

## What it does

1. **Reads what a person has actually done.** A resume, public links, repositories, deployed apps. Deterministic ingestion, then small models that extract facts with line references, then a synthesizer that builds a skill graph where every skill carries its evidence.
2. **Shows the shape of it.** A radar of domains and skills, drilling down; a flip to the roles they are closest to next, with what they have and what they lack.
3. **Matches a real posting.** Paste a job posting. The posting is read first, on its own, into requirements with how strongly each is asked. Then every requirement is checked against the person's evidence: covered, partial, synonym only, "you have it but your resume doesn't say", gap, or can't tell. Hard requirements are checked before anything is scored, and "can't tell" is never a fail.
4. **Builds the application from cited facts.** A resume and cover note composed by code from facts the person can defend, phrased by a model, and verified sentence by sentence. Gaps are stated plainly, never hidden. Numbers are never invented; a missing number becomes a question to the person.
5. **Prepares them.** Gap questions whose answers become facts marked "from you"; later, mock interviews that ask about the person's actual work and credit only what they can defend; a plan whose completion visibly grows the graph.
6. **Lets them share proof.** A portfolio page and a radar card built from the same evidence, for GitHub profiles and LinkedIn.

## What it refuses to do

These are not preferences. They are the product.

- **No claim without a source.** A sentence about a person exists only if it cites a fact with a line, a link or the person's own words. If a model writes something unsupported, code drops it, and the person sees the fact's own wording instead.
- **No invented numbers.** Ever. A bullet that would be stronger with a number says so and asks.
- **No skill from a posting onto a resume.** A requirement the person cannot evidence is a gap, written as a gap or left out.
- **No fake scores.** No "87% ATS match", no per-vendor pass/fail. Nobody knows how Workday scores and we won't pretend to. Bands, not decimals; checklists, not grades.
- **No scraping of people's accounts or of job boards that forbid it.** Postings are pasted or fetched from public pages. Links inside a posting are never opened. A posting is data, never instructions.
- **No personality screening.** Culture fit, coachability and the like are not readable from documents and are not scored.
- **No annual lock-in.** Pay per posting or by the month. A student should never buy a year to find out.
- **No dark patterns.** Seeing a gap is free. Answering a question about yourself is free. Re-checking after you did is free.

## Why this can win

The hard part of this market is not the AI. It is trust and distribution.

- **Trust comes from the refusals above.** As AI-written resumes become worthless, a profile whose every line can be traced becomes the opposite of the flood. That is a position competitors optimising for "more keywords" cannot copy without abandoning their product.
- **The data compounds.** Evidence → skill → role → outcome, across many people, tells us which gaps actually got closed and which got people hired. No resume tool has that, because none records evidence.
- **Distribution is earned, not bought.** The radar card people embed, the portfolio they share, a Claude Code skill that runs our match from a terminal, and honest launch numbers. The breakout tools of 2026 grew from a README and a true story; so will this.

## How it feels

Opening the app should feel like exhaling. A soft colour field that drifts through the hue circle once every 42 minutes, one serif line per screen, hairlines instead of shadows, nothing that asks for attention it hasn't earned. The person is anxious enough; the interface isn't allowed to add to it. The full system, with every number and the checks that enforce it, is in `design.md`.

## How we build

- **Design first, on a canvas, then code.** Screens are agreed before implementation (`design-first-then-code` is a standing rule).
- **Code computes, models write into typed slots.** Scores, weights, selection and totals are code. Models extract, classify and phrase, always into schemas that code validates.
- **Evidence before verdict, in the schema itself.** A model cites fact ids before it may state a status. Any status without valid citations is downgraded by code.
- **Two cheap models and one careful one.** Haiku for reading and classifying, Sonnet for writing, a decision model (Jev) for yes/no judgements with logged confidence. Costs are reserved before a call and tracked after.
- **Every rule has a check.** Contrast at all 360 hues, restraint in CSS, chart parity, grounded copy, additive migrations, layered imports. `npm run check` and `npm run test:py` are the gate.
- **Fakes, not mocks of the world.** Services depend on Protocols so every unit runs with fakes; golden baselines catch drift.
- **Say what was verified.** A change is done when it is tested and checked, and the report says exactly what was proven and what wasn't.

## What success looks like

- A student pastes a posting and, twenty seconds later, knows the three things standing between them and the interview, each tied to something real.
- A hiring manager who opens a Tayyari portfolio sees nothing they have to wonder about.
- People come back after they've done the work, to watch the graph grow.
- Paying users who were never asked to buy a year.

## Where things are

| What | Where |
|---|---|
| This document | `project-soul.md` |
| Working guide for agents and contributors | `CLAUDE.md` (`AGENTS.md` points to it) |
| The visual system, portable | `design.md`; reasoning in `docs/design-notes.md` |
| Backend architecture, flows, storage, guard | `docs/backend.md`; deployment in `backend/DEPLOY.md` |
| Build plan and feature specs | `docs/roadmap.md` |
| What we learned from open-source resume tools | `docs/research/open-source-resume-tools.md` |
| Design canvases | Calm layer, Portfolio kit, Cursor light, Posting match (links in `docs/roadmap.md`) |
