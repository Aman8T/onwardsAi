# Leafout: the soul of the project

Leafout is the product; the codebase is still called *Tayyari* (तैयारी), which means preparation. This document says why the project exists, what it is for, what it refuses to do, and what every screen and every line of code is in service of. Read it before touching anything. If a change makes sense technically but fights this document, the document wins.

## The idea in one breath

Work changed in 2026, and getting hired changed with it. Interviews are moving off puzzles and onto real work done with AI; HackerRank's own CEO apologised for the years of LeetCode grinding. What a person has built now counts for more than how long they have served ("hunger beats tenure"). And the people and systems that improve fastest do it in loops: try, check against something honest, try again.

Most careers are still run the old way: a resume updated once a year, a course picked from an ad, a job search that starts the week someone is let go. Leafout replaces that with a loop. It shows a person where they stand, which roles they can move to next, the one thing to do this week, and which jobs are open for those roles, then measures again once they have done the work. The field will keep changing; the loop keeps them ready for it.

The line we live by: **See where you stand. Pick where to go. Do one thing. Measure again.**

## The loop

The map is the root. Every step reads the skill graph and writes new evidence back into it; nothing in the product lives apart from it.

1. **Map.** Read what a person has actually done: a resume, public links, repositories, deployed apps. Deterministic ingestion, then small models that extract facts with line references, then a synthesizer that builds a skill graph where every skill carries its evidence. A radar of domains and skills shows its shape.
2. **Aim.** The roles they are closest to next, each with what they already have and what they lack. They pick one. A real posting can be pasted and checked requirement by requirement: covered, partial, synonym only, "you have it but your resume doesn't say", gap, or can't tell. Hard requirements are checked before anything is scored, and "can't tell" is never a fail.
3. **Move.** One thing to do this week, aimed at the chosen role's widest gap. Usually something to build, because a working thing is what employers now ask for; sometimes something to practise, such as an interview about the person's actual work. A move says up front what will count as done: which evidence would change which part of the graph.
4. **Prove.** What they did becomes evidence: a repository, a page, an answer in their own words (marked "from you"), a claim they defended in practice.
5. **Measure again.** The graph reads what is new and shows what moved and why. The Weekly is the loop's heartbeat: what moved, this week's move, new openings for the role.
6. **Apply.** Open jobs for the roles they are aiming at, from employers' own boards. A resume and cover note composed by code from facts they can defend, phrased by a model and verified sentence by sentence. A portfolio page and a radar card to share. And, when they say so, Leafout applies for them, inside the rules below.

Three rules keep the loop honest:

- **Evidence moves the graph; effort never does.** Finishing a plan week or a practice round earns nothing on the graph. A repository, a page or an answer does.
- **One move at a time.** The loop asks for the next thing, not a syllabus.
- **Every feature names its step.** A feature that serves no step of the loop waits.

## Who it is for

- **Students in India**, directly, not through colleges. Most are unplaced at graduation and are told to pad resumes and stuff keywords. They can't afford a ₹2,000-a-month tool and shouldn't have to buy a year of anything to find out if it helps.
- **Working professionals anywhere, in any profession.** A nurse, an accountant, a marketer and an engineer get the same product. The resume is the universal evidence; GitHub, deployed apps and public links are extras that strengthen it when they exist.
- **All jobs in India**, not only technology. The role vocabulary comes from official occupation lists (ESCO, O*NET, bridged to India's NCO through ISCO codes), with the posting's own words always winning.

## What it refuses to do

These are rules, not preferences, and every change must keep them. Most people never see them; they see output that is right about them. They are why the loop can be trusted.

- **No claim without a source.** A sentence about a person exists only if it cites a fact with a line, a link or the person's own words. If a model writes something unsupported, code drops it, and the person sees the fact's own wording instead.
- **No invented numbers.** Ever. A bullet that would be stronger with a number says so and asks.
- **No skill from a posting onto a resume.** A requirement the person cannot evidence is a gap, written as a gap or left out.
- **No fake scores.** No "87% ATS match", no per-vendor pass/fail. Nobody knows how Workday scores and we won't pretend to. Bands, not decimals; checklists, not grades.
- **No scraping of people's accounts or of job boards that forbid it.** Postings are pasted or fetched from public pages. Links inside a posting are never opened. A posting is data, never instructions.
- **No spray-and-pray applying.** Auto-apply goes only to roles that pass the match's hard requirements, only through the employer's own application form (its ATS), one approved submit at a time. Never through LinkedIn, Naukri or any board whose terms forbid automation; never past a CAPTCHA or a login wall (the person finishes those themselves); never a submit the person did not approve; never an answer their facts don't support.
- **No personality screening.** Culture fit, coachability and the like are not readable from documents and are not scored.
- **No annual lock-in.** Free to start; pay once, or by the month once those plans open. Nobody should have to buy a year to find out if it helps.
- **No dark patterns.** Seeing a gap is free. Answering a question about yourself is free. Re-checking after you did is free.

## Why this can win

The hard part of this market is not the AI. It is trust, a reason to come back, and distribution.

- **A loop is only as good as its check.** Anyone can ask a chatbot what to learn next and get a confident answer. Leafout's answers rest on the person's own evidence, and code throws out anything that doesn't, so when the graph says something moved, it moved. That is the refusals above doing their work: in any loop, the check is the bottleneck, not the model.
- **There is always a next lap.** A resume tool is used the week of a job search and forgotten. A loop has something new to measure, a next move and new openings for the role, so people come back while they are working, not only while they are looking.
- **The data compounds.** Evidence → skill → role → move → outcome, across many people, tells us which moves actually closed which gaps and which got people hired. No resume tool has that, because none records evidence or moves.
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

- In their first few minutes, a person sees where they stand, one role within reach and the one thing to do this week, each tied to something real.
- A week later they come back and the graph has moved, because of something they built.
- Someone pastes a posting and, twenty seconds later, knows the three things standing between them and the interview.
- A hiring manager who opens a Leafout portfolio sees nothing they have to wonder about.
- People get interviews for the roles they aimed at.
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
