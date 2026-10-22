# Personalized learning: what startups build, and what Leafout should take

Research run 2026-10-22. Sources:

- A deep-research workflow (106 agents; 23 sources fetched; 25 claims checked, each by a 3-vote adversarial check; 2 claims refuted).
- 11 per-company Haiku agents.
- A codebase audit for the baseline.

How to read the numbers:

- Every number cites a URL.
- "self" means the company reported it; "not public" means nothing was found.
- Most of the YC startup data is self-reported and contradicts itself. Only Duolingo (SEC filings) and Khan Academy (annual report) have hard figures.

---

## 1. Leafout baseline (what we have today)

**Personalization, deployed:**

- Skill graph from a resume, links and GitHub. Each skill has a 0–100 score, quoted evidence and the roles it unlocks (`backend/tayyari/analysis/`, `shared/types.ts`).
- Next roles with gaps (`shared/types.ts` Role).
- Posting match:
  - Requirements are checked against evidence.
  - The result is a band, not a percentage.
  - Up to 3 questions; the answers become "from you" facts (`backend/tayyari/match/`).
  - Resume and cover note built only from cited facts.

**Practice, deployed:**

- Practice the interview, in four kinds: This role, Defend your resume, Behavioural, Work sample (`backend/tayyari/interview/`).
- Code allocates the slots. The model phrases each question three ways: Surface, Push, Floor.
- Verdicts: Defended / Shaky / Couldn't defend, each tied to a quote of the person's answer.
- The debrief gives Say / Avoid / Fix. Each Fix carries a time in minutes (`interview/debrief.py`).
- Voice through Sarvam (`backend/tayyari/speech/`).
- Progress shows the best verdict per claim (`interview/history.py`, `src/practice/progress/`).

**Goal and time planning, deployed.** Prepare (`backend/tayyari/prepare/`):

- Where you stand, then the one move, then a weekly plan computed from 3, 6 or 10 hours a week.
- Ticks stored in `prepare_plans`.
- An `.ics` calendar file, a GitHub repo suggestion and a headline rewrite.

**Built, not deployed:**

- Leafout Weekly newspaper, by email and in the app (`backend/tayyari/weekly/`). It needs `leafout.in` verified on Resend.
- Home dashboard and L1 sign-in.

**Gaps that matter for this research:**

- No graph history or skill time series; there is no snapshot table in `storage/tables.py`.
- No stable skill ids; that is X1, still planned.
- `interview_sessions` rows are purged after 30 days (`InterviewSessionRow` docstring). Claim history is derived from them, so it disappears with them.
- No "Practised" badge on the graph (`docs/roadmap.md` §I1 follow-ups).
- A debrief's Fix steps don't flow into Prepare (`docs/roadmap.md` P1).

**Users and journey:**

- Users: students in India, plus working professionals in any profession (`project-soul.md`).
- Journey: upload → graph → roles → match → prepare → practice → search → portfolio → Weekly (Weekly is not live yet).

**Stack and constraints:**

- Haiku 5.5 for every role (POC cost mode).
- About $0.11 per search run; $20 daily spend ceiling (`docs/backend.md`).
- Additive-only migrations.
- Every claim about a person must cite a fact.
- No fake scores or percentiles.
- No personality or tone scoring.
- No dark patterns (`project-soul.md`, `CLAUDE.md`).

---

## 2. Summary table

YC's AI-Enhanced Learning directory listed **45 companies** on 2026-10-22 ([YC](https://www.ycombinator.com/companies/industry/ai-enhanced-learning)). Roughly a third are off-topic, for example Scribd, Deepgram and EARTH AI. The shortlist below adds Flint and General Learning from YC's Education list, plus five benchmarks from outside YC.

| Company | Problem / learner | Reach | Revenue / pricing | Moat | Relevance |
|---|---|---|---|---|---|
| **Speak** (non-YC) | Adults who studied English but can't speak it; Korea first | "10M+ users" (self, [TechCrunch 2024-06](https://techcrunch.com/2024/06/20/language-learning-app-speak-nets-20m-doubles-valuation)); 200+ enterprise customers (self, [blog 2024-12](https://speak.com/blog/series-c)) | "$100M+ ARR" claimed but unverified; $50M+ at end-2024 ([The Signal](https://readthesignal.co/p/speak-hit-100m-arr-the-ai-that-answers)). $78M Series C at a $1B valuation ([blog](https://speak.com/blog/series-c)). $17.99–39.99/mo; **the top tier sells lessons built from your mistakes and a study plan** ([SpeakShark 2026-08](https://speakshark.com/blog/speak-app-review-2026)) | Distribution and speaking data; the conversation layer is easy to copy | **High.** The model for Practice → "built from your mistakes" |
| **Duolingo** (non-YC) | Casual language learners | 58.7M daily users, 140.6M monthly, 12.7M paying (~9%), Q2 2026 ([SEC 8-K](https://www.sec.gov/Archives/edgar/data/0001562088/000162828026053299/q2fy26duolingo6-30x26share.htm)) | $298.5M quarterly revenue; "increase revenue without adding friction to free users" (same) | Data from very large use; Birdbrain predicts success per exercise ([Duolingo blog](https://blog.duolingo.com/learning-how-to-help-you-learn-introducing-birdbrain)) | Medium. Shows code-selected difficulty and a free-first business |
| **Khanmigo** (non-YC) | K-12 students and teachers | 2.0M had access in 2024-25 ([annual report](https://www.annualreport.khanacademy.org/)); ~1.5M licensed learners in 795 districts | From $10 per student per year for districts ([pricing](https://www.khanacademy.org/schools/pricing)); free for teachers, funded by Microsoft | Brand, content and district contracts | Low for features. **Lesson: institutions pay for implementation (rostering, dashboards, training), not for the tutor** |
| **Yoodli** (non-YC) | Sales and L&D teams; communication roleplay | "1M+ learners" (self, [yoodli.ai](https://yoodli.ai/)); Google, Snowflake, Databricks ([TechCrunch 2025-12](https://finance.yahoo.com/news/ex-googler-yoodli-triples-valuation-234358139.html)) | $40M Series B at a valuation over $300M (same; valuation unsourced). $8–20/mo; enterprise is custom ([pricing](https://yoodli.ai/pricing)) | Enterprise compliance and integrations; the roleplay is easy to copy | **High.** Rubric-scored roleplay, "one strength + one to improve", memory of the last 10 sessions |
| **Final Round AI** (non-YC) | Job seekers | "10M+" (self, [site](https://www.finalroundai.com)) | $6.88M seed, 2025-01 ([Seedtable](https://seedtable.com/companies/final-round-ai/funding-rounds/seed-2025-01)); "$25+/mo" on its own site, $60–96/mo on third-party sites | Weak; has a live in-interview copilot that employers call cheating ([review](https://blog.loopcv.pro/final-round-ai-review/amp/)) | Medium. "Re-drill weak spots" is worth taking; **the copilot is an anti-pattern** |
| **Miyagi Labs** (YC W25) | Exam takers: SAT, NEET, CAT | Not public | Not public | Weak | **High in shape.** Diagnose → plan → practice → score predictor ([YC](https://www.ycombinator.com/companies/miyagi-labs)); our flow without the fake score |
| **SuperKalam** (YC W23, Bengaluru) | UPSC aspirants, including working professionals with limited hours | 60k (self, [Inc42 2024-05](https://inc42.com/?p=458365)); "3,00,000+" (self, [pricing](https://superkalam.com/pricing)) | ~$2M seed ([Inc42](https://inc42.com/?p=458365)); ₹1,199/mo, ₹5,499 for 6 months (same pricing page; conflicting figures elsewhere) | Weak | **High for India.** Daily targets, answer grading in under 60 s, framed as cheaper than ₹1L+ coaching |
| **Wondering** (YC S26) | Self-learners on any topic | 12k+ users, 687 active in a week, 100+ paying (self, [YC](https://www.ycombinator.com/companies/wondering)) | Pro $14.99/mo ([chatgate](https://chatgate.ai/post/wondering), via App Store) | Weak | Medium. **FSRS spaced review and a daily calendar slot** |
| **Alice.tech** (YC W25) | University and Danish secondary students before exams | 2k–250k, conflicting (self, [alice.tech](https://www.alice.tech/)) | $20k MRR (self, [YC](https://www.ycombinator.com/companies/alice-tech)); seed $4.7–4.8M ([iamsterdam](https://startupmap.iamsterdam.com/news/feed/alice-tech-raises-4-8m-for-ai-learning)); €7–20/mo | Weak | Medium. Practice built from your own material; we already do this from the resume and posting |
| **YouLearn** (YC Sp25) | College students | 150k MAU (self, [launch](https://www.ycombinator.com/launches/NTk-youlearn-an-ai-tutor-personalized-to-each-student)) up to "2M+" (self); unreliable | $12–20/mo ([terms](https://www.youlearn.ai/terms-conditions)); funding unverified | Weak | Low |
| **Flint** (YC S23) | K-12 schools | 400k+ (self, press) | $15M Series A, 2025-11 (company-reported) | Sales to schools | Low; different buyer |
| **General Learning** (YC F24) | IB, SAT and GCSE students | Not public | Its ARR claim was **refuted** 1–2 | Content brands per curriculum | Low |
| **Pandai** (YC S21, Malaysia) | Malaysian school students and parents | 280k–1M, conflicting (self, [YC](https://www.ycombinator.com/companies/pandai), [pricing](https://my.pandai.org/parents/pricing/)) | US$2.03M seed ([DNA 2022-01](https://www.digitalnewsasia.com/startups/malaysian-edtech-pandai-raises-us2mil-seed-round)); RM9–39/mo | Syllabus alignment | Low. Diagnosis by chapter and difficulty, reports to parents |
| **Mathos** (YC W24) | Maths students | "1M+ in the past year" (self, [YC](https://www.ycombinator.com/companies/mathos)) | $500K ([Caplight](https://www.caplight.com/company/mathgptpro)); $5.98–9.98/week ([pricing](https://www.mathos.ai/pricing)) | Weak | Low. The learner chooses "a nudge or the full solution" |
| **Read Bean**, **Studdy** (YC) | Chinese reading; homework help | Not public | Read Bean $14.99/mo ([App Store](https://apps.apple.com/us/app/-/id6736993989)); Studdy not public | Weak | Low |

**Two controlled studies to design around:**

- **[Bastani et al., PNAS 2025](https://ideas.repec.org/a/nas/journl/v122y2025pe2422633122.html):** about 1,000 students.
  - Plain GPT-4 help raised practice scores 48% but **cut the later exam score without AI by 17%**: students copied.
  - A hints-only tutor removed that harm and added nothing.
- **[CS1 RCT, arXiv 2609.29995](https://arxiv.org/abs/2609.29995):** a preprint.
  - The Socratic tutor was rated least helpful (3.53/5 against 4.12–4.27).
- **Design target:** the learner produces the answer, the help still feels useful, and the judgement is made on unassisted work. Practice already works this way.

---

## 3. Grouped by personalization approach

| Approach | Who does it | Leafout today |
|---|---|---|
| Knowledge diagnosis | Duolingo Birdbrain, Read Bean, Pandai, Alice.tech | Skill graph with cited evidence. **Stronger than most** (evidence, not a quiz), but it never updates from practice |
| Goal-based planning | Miyagi, SuperKalam, Speak Premium Plus | Prepare for a role or one gap. **Have it** |
| Time-aware scheduling | Wondering (daily slot, FSRS), SuperKalam (daily targets) | 3, 6 or 10 h a week and an `.ics` file. No deadline input, no spaced review |
| Interactive practice | Speak, Yoodli, Final Round, Alice exam simulations | Practice, four kinds, voice. **Have it**; missing re-drill and memory across sessions |
| AI tutoring | Khanmigo, Flint, Studdy, YouLearn, Mathos | Chat coach on graph nodes. The evidence says don't make it an answer machine |

## 4. What people pay for, and what they don't

**Paid for:**

- **Speaking or practice built around your own mistakes:** Speak's top tier at $39.99/mo; Yoodli $8–20/mo.
- **Exam or outcome-shaped plans:** SuperKalam ₹1,199/mo.
- **Being seen by recruiters:** Naukri's paid tiers, per a competitor's guide ([GoodSpace](https://goodspace.ai/guides/naukri-paid-services-worth-it)).
- **Institutions:** implementation, meaning rostering, dashboards, training and compliance (Khan from $10 per student per year; Yoodli enterprise).

**Not paid for:**

- **Mock interviews alone in India:** InterviewBit's are "all free" ([FAQ](https://www.interviewbit.com/pages/faq/)).
- **A generic AI tutor:** no evidence of individual willingness to pay.
- **Annual student passes stay cheap:** Unstop Pro launched at ₹1,999 a year ([Media Infoline 2023-09](https://www.mediainfoline.com/brand/unstop-launches-unstop-pro-incentivizing-student-learning-and-upskilling)).
- **Scale of paying users:** Duolingo converts about 9%, and only on a huge free base.

**Implication:**

- The ₹599 and ₹1,999 tiers proposed in `docs/research/willingness-to-pay.md` sit in a defensible range: SuperKalam ₹1,199/mo, Unstop ₹1,999/yr.
- The paid hook should be "practice and plans built from *your* weak claims", not more practice sessions.

---

## 5. Gap analysis

| Feature (seen at) | Verdict | Why and effort |
|---|---|---|
| Evidence-based level per skill (Birdbrain, Read Bean) | **Already have** | The graph scores with cited evidence. Practice verdicts don't feed back; see the "Practised" line |
| "Practised" line on graph skills (Duolingo's mastery loop) | **Can build quickly** | Already specced (`docs/roadmap.md` I1). Read from `interview/history.py`, show on the graph; never adds a skill. **~1–2 days**, frontend plus one read |
| Re-drill weak claims (Final Round "Re-drill", Speak mistakes lessons) | **Can build quickly** | A fifth Practice kind whose slots come from claims judged Shaky or Couldn't defend. Code already allocates slots (`interview/plan.py`). **~2–3 days.** Needs a durable claim-marks table first, because sessions purge at 30 days (additive migration, ~1 day) |
| Debrief Fix steps → Prepare tasks | **Can improve** | Fix steps already carry minutes (`interview/debrief.py`), and Prepare already counts hours. Turn them into tasks in the role's plan. **~1–2 days**; a known follow-up in the roadmap |
| Spaced re-check (Wondering FSRS) | **Can build quickly** | Code-computed intervals (for example 2, 7 and 21 days after Shaky) shown in Weekly's "your week" and as `.ics` events. No model cost. **~2 days** after re-drill; Weekly must ship |
| Deadline-aware plan ("interview on 12 Nov") (SuperKalam, Miyagi) | **Can improve** | Prepare computes weeks from hours. Add a date and code fits or trims moves. **~1 day** |
| Practice from a pasted posting (Alice and YouLearn, "from your material") | **Can improve** | Match already reads postings. Practice "This role" takes a graph role, not a posting. **~1 day**; known gap |
| Rubric named before a roleplay, with memory across sessions (Yoodli) | **Can improve** | Show which claims will be pushed before the start; carry the last session's Shaky claims as follow-ups. **~2–3 days**, overlaps re-drill |
| Progress over time ("since last month") (Duolingo, Pandai reports) | **Can build quickly** | Snapshot the graph on re-analysis (new table) and diff in code for Weekly and Home. **~2–3 days**; ids are fragile until X1 |
| Generated micro-lessons or content library (Wondering, General Learning) | **Skip** | Off-core, unbounded content cost, and lessons can't cite the person's facts. Prepare's one move points to outside resources instead |
| Streaks, badges, leaderboards (Pandai, Duolingo, SuperKalam) | **Skip** | Leaderboards need a cohort and invite percentile claims (rule 8); streak pressure is a dark pattern (`project-soul.md`). Weekly's quiet "your week" line is enough |
| Score predictor (Miyagi) | **Skip** | A fake score; violates rule 8 |
| Live interview copilot (Final Round) | **Skip** | Dishonest by design; against "no claim without a fact" |
| Answer-giving tutor | **Skip** | Bastani: −17% on later unassisted work. Keep the coach on nudges; the work-sample assistant is already ungraded and counted |
| Placement-cell dashboard for colleges (Khan districts, Yoodli enterprise) | **Later** | The real institutional money, but it needs org accounts, rostering and a cohort view. **Weeks.** Revisit after individuals pay |

---

## 6. Top 5, by impact against effort

1. **Re-drill my weak claims** (~3–4 days, with the durable claim-marks table).
   - Closes diagnose → practice → re-practice.
   - It is the thing Speak's top tier charges for, and the natural paid hook.
   - Code picks the slots; the model only phrases.
2. **Fix steps become Prepare tasks** (~1–2 days).
   - Almost free, because both halves exist.
   - Makes the debrief actionable inside the plan the person already ticks.
3. **"Practised" line on the graph** (~1–2 days).
   - Practice evidence updates what the person sees about themself without inventing skills.
   - It is the cheapest visible personalization loop.
4. **Deadline-aware Prepare plus spaced re-checks through Weekly and `.ics`** (~3 days total).
   - Time-aware scheduling with zero model cost.
   - Gives Weekly a reason to be opened each Monday.
5. **Graph snapshots and "since last time"** (~2–3 days; better after X1).
   - Visible progress drives retention.
   - It is also the proof of value a ₹599 plan needs.

**Deliberately not on the list:** micro-lessons, gamification, AI tutor, score predictors, live copilot.

## Caveats

- YC company facts are mostly self-description; their reach numbers contradict each other.
- Refuted, do not cite:
  - StudyDojo's "2,000+ paying customers, mid-six-figure ARR".
  - Khan's "30 min a week gives 20% more learning".
- Speak's $100M ARR is a company claim with no primary announcement found.
- Indian buyer behaviour is thin: no conversion data for Scaler, InterviewBit, Internshala or Naukri.
- Effort estimates are rough, from reading the module layout; nothing was prototyped.
