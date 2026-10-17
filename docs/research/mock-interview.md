# Mock interview: what HackerRank does, and what we take

Research for I1, the grounded mock interview, done 2026-10-16. The user asked to learn from HackerRank's Chakra, both its
features and its payment model. HackerRank runs two related products:
- **Chakra**, its AI interviewer for employers;
- **AI mock interviews** for candidates, in the HackerRank Community.

This note covers both, then the decisions taken for Tayyari. The design itself is on the canvas linked from
`docs/roadmap.md` (I1).

Most of what follows comes from HackerRank's own pages and is unverified. No independent evaluation of Chakra's accuracy or
fairness was found.

## 1. Chakra (employers)

Chakra became generally available on 5 October 2026, after about six months in beta.
- HackerRank says it ran more than 500,000 interviews in the beta, with Snowflake, Snorkel and Capgemini among the
  testers.
- It is pitched as replacing three steps (recruiter screen, take-home, engineer follow-up) with one session.

**Format**
- The candidate gets a link and interviews when they choose. The demo shows a 30-minute clock and says it is "best
  experienced on Desktop".
- It is a spoken conversation, with a "Listening…" state and mic controls.
- For technical roles, it spins up a canvas with a real-world problem: a file tree (`backend`, `frontend`, `tickets`,
  `PLAN.md`) and a prompt box for an AI assistant (Plan mode, `@` files, `#` PRs, `/` commands). Since October it can
  also open an inline code editor for a coding question.
- After the hands-on work, Chakra asks follow-ups about the candidate's choices, such as "why this approach" or "what
  if this constraint changed". Follow-ups come from what the candidate actually said, so two candidates for one role
  can get different paths.
- Sample roles are AI Engineer, Forward Deployed Engineer, Customer Experience Engineer and AI Product Manager. Sales,
  support and product roles are covered too.

**Setup.** Three agents:
- An Interview Creator turns the job description and a short conversation into a plan. The employer can edit it:
  reorder sections, change question types, and mark requirements "Must have", which weigh more.
- An Interviewer runs the conversation.
- A Reporter scores the transcript.

**What it measures.** Problem solving, judgment, communication, and "AI fluency": how well someone frames a problem for
AI, judges its output and steers it.

**Report**
- A score per competency, for example "Agentic Coding 4.1".
- A "why" for every score, tied to a moment in the transcript or the work.
- Strengths and gaps, an overall recommendation, the code submission, and a recording with a synced transcript.
  Sources disagree on whether the recording is video or audio.
- HackerRank's own buyer advice is that every rating should tie to a replayable transcript moment, and that a
  paragraph of generic commentary is "summarizing", not evaluating.

**Integrity**
- A webcam check for missing or extra faces.
- A pause when the candidate leaves full screen or adds a monitor.
- Detection of helper apps (it names Cluely).
- A desktop app mode that can close other programs.
- HackerRank reports 70 to 80% fewer suspicious-activity flags than in its classic tests, which it puts down to
  allowing AI openly.

**Trust**
- Candidates are told AI is used, and can ask for another process, an accommodation, human review or an explanation.
- Humans make the hiring decision.
- HackerRank cites SOC 2 and says candidate data never trains models.
- The two published bias audits cover the plagiarism and image checks, not Chakra's scoring.

**Payment model**
- Priced per interview attempt, cheaper at volume. No public rate.
- A separate account from the Starter, Pro and Enterprise plans, enabled by support.
- Fees under the master agreement are paid yearly in advance.
- "Start free trial" and "Book a demo" are the only paths on the page. Third-party plan prices conflict and are
  unverified.

## 2. HackerRank's practice interviews (candidates)

These are AI mock interviews in the HackerRank Community (Prepare tab), by voice or chat.

**Types.** Technical Screen, Coding, System Design, Behavioural and AI Fluency. The technical screen offers set roles
(Software, Frontend, Backend, AI Engineer, Forward Deployed Engineer), or a Custom Role built from a pasted job
description.

**The session**
- A voice interviewer runs it by itself: it introduces the flow, answers questions first, then asks follow-ups.
- Camera and mic are on. You can show or hide the transcript, mute, and end the interview.
- Block "at least 30 minutes"; the time left shows in a corner.

**Report.** An overall recommendation (Strong, Moderate or Weak Fit), a summary of strengths and areas to improve, and
the full recording with a synced transcript. Each section is rated Strong, Moderate or Weak, with a short narrative
and a transcript reference. Past attempts are listed with a date and a "score" whose scale is not given.

**Payment model**
- Free: one Software Engineer coding mock and one Technical Screen mock, each once.
- Everything else needs credits, bought by quantity. Credits never expire and work for any type.
- Refunds on request by email.
- Razorpay in India, Stripe everywhere else, the same split Tayyari already has.
- Prices are shown only at checkout.

For comparison, other candidate tools (from a third-party roundup, unverified):
- Pramp: 5 free peer credits a month.
- Big Interview: $39 a month or $299 lifetime.
- interviewing.io: about $179 a session with a human.
- Final Round AI: a subscription.

## 3. What Tayyari takes, and what it leaves

| They do | We take | We leave |
|---|---|---|
| A real task, then questions on your choices (Chakra) | **Work sample, then defend it**, with tasks for every profession | — |
| A plan from the job description, editable, must-haves weigh more | The plan shown before you start; skip any slot; must-asks first | — |
| Follow-ups from what the candidate just said | Surface, Push and Floor follow-ups, chosen by rules in code | — |
| Each rating tied to a transcript moment | Every verdict quotes the person's words, linked to the moment; code checks the quote | — |
| AI allowed in the task, "AI fluency" scored | An optional assistant in work samples; the interviewer asks how it was used | An AI-fluency score |
| A score per competency and a hire recommendation | Counts of verdicts (Defended, Shaky, Couldn't defend, Not assessed) | Numbers and recommendations (`project-soul.md`: no fake scores) |
| Webcam checks, full-screen lock, helper-app detection | — | All of it: practice needs no watching |
| A recording with a synced transcript | The transcript (kept 30 days) | The recording |
| Voice or chat | Speak by default, type any time | Camera |
| One free mock of two types, then credits by quantity that never expire; Razorpay or Stripe | Later, when it is priced | — |
| Per-attempt pricing for employers, paid yearly in advance | Paying per interview | Yearly plans (`project-soul.md`: no annual lock-in) |

What none of them does, and Tayyari does:
- The questions come from the person's own evidence: resume lines, repositories, pages.
- The "say this instead" line is built only from the person's words and cited facts, with brackets for what only they
  know.
- A good answer about something missing from the resume can become a fact marked "From you".
- Practice feeds the graph, the prep page and the tracker.

## 4. Decisions (the user, 2026-10-16)

- **Answering:** speak by default and type any time.
  - The browser turns speech into text, so it costs nothing extra; in Chrome this uses Google's speech service, and
    the app must say so.
  - The interviewer can read its questions aloud with the browser's voice.
  - No camera. No audio is kept. The transcript is kept 30 days for the debrief.
- **Speech provider, 2026-10-17:** the user added a Sarvam AI key (100 free credits) and asked to use it through
  Sarvam's SDK. Speech-to-text and the interviewer's voice now go through Sarvam (Indian English, Hinglish, the
  interview's own names as hints) when the key and the day's budget allow, with the browser's speech and typing as
  the fallbacks. Tayyari still keeps no audio.
- **All four kinds in the first version:**
  - This role (from a posting);
  - Defend your resume;
  - Behavioural stories;
  - Work sample, then defend it.
- **Free for now, price later,** under the daily spend cap and per-person limits (3 a day on the canvas; the number is
  set at build). When it is priced, the canvas holds two options:
  - one credit from the same packs as a posting match;
  - separate practice packs.

  Either way: credits never expire, never a yearly plan, re-asking a shaky question is free, Razorpay or Stripe.

**Cost estimate, to be measured.** About 2 cents of Haiku 5.5 per interview. Most of it is the interviewer's turns
(the plan card and the transcript so far, about 5,000 tokens each, about 15 turns) plus one judge call per answer and
the debrief.

## Sources

- TechCrunch, 5 Oct 2026: https://techcrunch.com/2026/10/05/hackerranks-ai-interviewer-offers-a-glimpse-into-what-job-interviews-could-become/
- Chakra (chakra.sh redirects here): https://www.hackerrank.com/chakra
- How an AI interviewer works (HackerRank): https://www.hackerrank.com/writing/how-does-an-ai-interviewer-work
- The best AI interviewers in 2026 (HackerRank blog, per-attempt pricing): https://www.hackerrank.com/blog/the-best-ai-interviewers-in-2026-how-to-evaluate-them/
- HackerRank on X, inline code editor: https://x.com/hackerrank/status/2080304717465522435
- Culturro review (third party: separate account, MSA terms, report contents, audit gaps): https://culturro.com/hr-software/ai-interview-agents/hackerrank-chakra/
- Introduction to Mock Interview (free allowance): https://help.hackerrank.com/articles/8988753946-introduction-to-mock-interview
- Purchase Mock Interview Credits (no expiry, Razorpay/Stripe, refunds): https://help.hackerrank.com/articles/3282259518
- Technical Screen mock (custom role from a job description, report): https://help.hackerrank.com/articles/5671120169-technical-screen-mock-interview
- Behavioural mock (STAR, section ratings, recording): https://help.hackerrank.com/articles/5174727491-behavioural-mock-interview
- AI Fluency mock: https://help.hackerrank.com/articles/3796809491-ai-fluency-mock-interview
- Third-party roundup of practice tools and prices: https://codersera.com/blog/best-free-ai-mock-interview-platforms-2026/
