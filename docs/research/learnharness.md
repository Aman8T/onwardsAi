# LearnHarness: what Tayyari should take

Research run 2026-11-17. Source: https://github.com/amosblomqvist/learnharness (README, `docs/architecture.md`, `docs/teaching-methodology.md`, `AGENTS.md`, repository metadata). The system prompts and source files were not read, and no code was run.

---

## 1. What it is

An open-source AI tutor ("an agent harness for learning difficult things"): Next.js with an Electron desktop host, the Pi agent framework, and local JSON/JSONL storage. Users bring their own model keys or a local model server.

**The loop:**

1. **Measure.** Before teaching, a Prober agent finds where the learner's understanding ends on each part the lesson depends on.
2. **Plan.** A coarse dependency map (a Mermaid DAG) from that point to the goal, shown to the learner. "The plan is the map, not the road": plan nodes are not lesson chapters.
3. **Teach.** A Teacher agent moves one reasoning step at a time from what the learner already holds, and every step ends with a check. A confusion is repaired before anything is built on it.
4. **Practice.** A Practitioner agent poses varied problems, one at a time, where the right knowledge must come to mind without being named.

**Other mechanisms:**

- Each agent has a narrow tool set. The Prober's `probe_done` hands off to a fresh Teacher.
- Question tools end the model's turn with a placeholder; the learner's answer arrives later as the tool result, so the model cannot answer its own question.
- The knowledge graph stores propositions and reasoning chains, not ability levels. "Practice outcomes are session evidence, not graph mastery."
- Graph extraction runs only over teacher text not yet covered, tracked by message id (it triggers past 4096 uncovered tokens).
- Side questions go to a cheap tool-less call that sees the lesson text but not the answer keys.
- A second turn on a busy conversation gets a 409; a replay-plus-live stream lets a dropped browser reattach.

**State (2026-11-17):** created 2026-10-16, about 9.8k stars and 544 open issues. That is fast growth for a month-old repository; treat the star count with caution. The teaching method is the author's own theory and cites no studies; there is no spaced repetition.

## 2. Why not its code

- **AGPL-3.0.** Its code would bring the AGPL's network obligations into a hosted app.
- **Different stack and storage.** Local files with one owner per data folder and no transactions, against FastAPI with Neon Postgres.
- **Young.** One month old, with a large open issue count.

Ideas only. Per `CLAUDE.md`, read third-party repositories; never run them.

## 3. Ideas worth taking, best first

### 3.1 Ask before planning (Prepare)

Prepare plans from the graph's gaps (`Profile.roles`), but a skill missing from a resume is absence of evidence, not absence of knowledge. LearnHarness measures first and plans the shortest path, so nothing already known is taught again.

For Tayyari: before the weekly plan, two or three questions per gap. Each answer becomes a "from you" fact, as the posting match's questions already do (`backend/tayyari/match/`), so rule 1 holds. Code decides which gaps are covered and drops them from the plan; the model only phrases the questions and classifies the answers into enum statuses, with citations before the status (rule 2).

This matters most for the first users (`project-soul.md`, switchers first): experienced IT people know far more than their resumes say, and a plan that teaches them the basics loses them.

### 3.2 Practice results stay as evidence

LearnHarness keeps practice outcomes as session evidence that never moves the graph. This confirms the current design: `claim_marks` and the "Practised" badge sit beside the skill scores and never change them. Keep it so if anyone proposes letting practice raise a score (rule 8).

### 3.3 A side question that cannot see the answers (Practice)

A "what does this question mean?" control in the practice room, answered by a cheap tool-less call that gets the question as asked, but not the slot, the Surface/Push/Floor phrasings or what the judge looks for. Spend is reserved like any other call.

### 3.4 Re-read only what is new ("Update my graph")

"Update my graph" re-reads every stored file and the own-words lines. Recording what each analysis already read, and reading only what was added since, would cut its cost as files pile up. Whether the synthesizer can work from a partial read needs checking first.

### 3.5 Smaller

- **Turn-ending question tools.** Relevant only where a model asks the person something inside a free-form loop (the coach). Practice is code-driven and already safe.
- **409 on a concurrent turn, plus a replay stream.** Could let the analysis thinking stream reattach after a phone drops its connection.

## 4. What not to take

A full tutor ("teach me Kubernetes"). It is a large new product with many model calls per lesson under the daily spend ceiling, and it runs against the plan of five watched paid calls before new features. If a "learn this gap" step comes later, LearnHarness's measure, plan, teach, practice loop is the reference, and it starts on a canvas like every feature.

## 5. Recommendation

After the concierge five, the next change to Prepare is 3.1: small, inside the cited-fact rules, and it makes plans shorter and more accurate for the people Tayyari is for first.
