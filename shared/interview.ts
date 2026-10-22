/**
 * Practice the interview (I1): the contract between `/api/interview/*` and the web app.
 *
 * A practice interview asks about the person's own work and credits only what they back up, in their own words:
 *
 *   1. plan     code picks the slots from the person's evidence lines, a role's asks and gaps (from a search with AI), the
 *               requirements of a role on their graph, or a work-sample template for their kind of work. No model call:
 *               the plan is free to look at and change.
 *   2. phrase   Claude Haiku phrases each slot once: a question, a "push" follow-up, an easier "floor", and what a full
 *               answer covers (hidden until the debrief). Code checks every name and number against the slot's sources.
 *   3. answer   the browser turns speech into text (or the person types). Code routes "I don't know", "skip", "say it
 *               again", a dispute and silence without a model. Otherwise Haiku judges the answer, quoting it word for word
 *               before a verdict; code checks the quote is in the answer and picks the next rung by fixed rules.
 *   4. debrief  counts in code; "Say", "Avoid" and "Fix" phrased by Haiku from the person's words and cited evidence, with
 *               `[brackets]` for what only they know; every name and number checked in code.
 *
 * Verdicts are words (Defended, Shaky, Couldn't defend, Not assessed), never a score. Not assessed never counts against
 * the person. Nothing here is shared with an employer.
 *
 * Re-drill (I2, canvas https://claude.ai/artifact/BXiFG9TDUYtLeCACtGxoSu): the claims whose best verdict is Shaky or Couldn't
 * defend come back, each starting where it wobbled. Code picks them, orders them and sets each one's start rung; a Fix step
 * from a debrief can go into a Prepare plan or stay in Practice, and ticking it never changes a verdict.
 *
 *   GET    /api/interview/redrill?profileId=        RedrillView
 *   POST   /api/interview/fixes                     FixAdd -> FixTask (idempotent per session and slot: again moves it)
 *   PATCH  /api/interview/fixes/{id}                FixUpdate -> FixTask
 *   DELETE /api/interview/fixes/{id}?profileId=
 *
 * Mirrored by backend/tayyari/contract/interview.py; tests/unit/contract/test_interview_parity.py reads this file and fails
 * when the two drift. Same subset as discover.ts: interfaces, literal unions, `x?:` optionals, `T[]`, inline objects.
 */

/** The four kinds of practice, and a re-drill of the claims that wobbled. */
export type InterviewKind = 'role' | 'resume' | 'stories' | 'work' | 'redrill';

/**
 * What one slot is about: an ask of the posting the person shows, a gap (an ask they don't show), a claim on their
 * resume, a story from their experience, or a question about their work sample.
 */
export type SlotKind = 'ask' | 'gap' | 'claim' | 'story' | 'task';

/** A verdict, worked out in code from the judge's quote and class. Never a score. */
export type Verdict = 'defended' | 'shaky' | 'couldnt' | 'not_assessed';

/** The three rungs of one slot: the question, a push for specifics, an easier floor. */
export type Rung = 'surface' | 'push' | 'floor';

export type AnswerMode = 'speech' | 'typed';

/** What the person did: answered, or pressed a button that needs no model. */
export type AnswerAction = 'answer' | 'dont_know' | 'skip' | 'repeat';

export type InterviewLength = 'short' | 'full';

/** "Spend more time on": changes which slots the plan picks. */
export type InterviewFocus = 'gaps' | 'claims' | 'stories';

export type SessionStatus = 'active' | 'done';

/** What the interviewer is saying now: a slot's question, a follow-up, an easier one, or the end. */
export type LineKind = 'question' | 'followup' | 'floor' | 'done';

export type Speaker = 'interviewer' | 'you';

/** The parts of a complete story. */
export type StoryPartName = 'situation' | 'action' | 'result' | 'reflection';

/** How a work sample's material reads: code, a small table (tab-separated rows), or plain lines. */
export type MaterialKind = 'code' | 'table' | 'text';

/** Where a Fix step went: into a Prepare plan, or kept in Practice. */
export type FixWhere = 'plan' | 'practice';

/** A role from a search with AI (Discover, or a saved application): the run it was found in and its id. */
export interface RoleSource {
  runId: string;
  jobId: string;
}

/**
 * A role on the person's own graph (`Profile.roles`), for `role` practice without a posting: its plan is built from the
 * role's requirements, gaps first, and the interviewer's title is the role's title.
 */
export interface GraphRoleSource {
  roleId: string;
}

/**
 * One thing a slot is about: the posting's own words, one of the person's evidence lines, or (`role`) a requirement of a
 * role on their graph, which is Tayyari's reading of what the role needs, never a posting's words.
 */
export interface SlotSource {
  origin: 'posting' | 'evidence' | 'role';
  text: string;
  /** For evidence: where it is from ("Resume", "GitHub", a page's label). */
  source?: string;
}

/** What a re-drilled claim held at its previous mark: the room's "Back from 16 Oct · Shaky", and what phrasing aims at. */
export interface SlotLast {
  verdict: Verdict;
  /** The day of that mark (ISO date). */
  on: string;
  /** The question as it was first asked then. */
  question: string;
  /** The verdict's quote of that answer, word for word. */
  quote?: string;
  /** The rung that decided it. */
  rung: Rung;
}

export interface PlanSlot {
  /** "s1", "s2" ... in plan order. */
  id: string;
  kind: SlotKind;
  /** A short name for the plan rail ("Performance", "Mentoring"), from the skill the evidence backs or the ask. */
  topic: string;
  sources: SlotSource[];
  /** Re-drill: the rung the slot opens at, set in code (Shaky → push, Couldn't defend → surface in new words). */
  start?: Rung;
  /** Re-drill: the claim's previous mark. */
  last?: SlotLast;
}

/** `POST /api/interview/plan` (no model call) and the base of `StartRequest`. */
export interface PlanRequest {
  profileId: string;
  kind: InterviewKind;
  /** For `role`, this or `graphRole` (not both); for `work` it picks the task's family from the role's title. */
  role?: RoleSource;
  /** For `role`: a role on the person's graph instead of one from a search. */
  graphRole?: GraphRoleSource;
  /** Default `full`. */
  length?: InterviewLength;
  focus?: InterviewFocus;
  /** For `redrill`: the claim keys picked on setup (`RedrillClaim.key`), at most 6; absent = code's top 3. */
  claims?: string[];
}

export interface InterviewPlan {
  kind: InterviewKind;
  /** "Frontend Engineer at Headout", "Senior Frontend Engineer" (a graph role), "Defend your resume", "Work sample · …". */
  title: string;
  slots: PlanSlot[];
  /** About how long it takes, in minutes. */
  minutes: number;
  role?: RoleSource;
  graphRole?: GraphRoleSource;
  /** A sentence about the plan when it is shorter than asked ("Your profile is short, so …"). */
  note?: string;
  /** For `work`: the family the task comes from ("Frontend engineer"). */
  family?: string;
}

/** `POST /api/interview/sessions`: the plan's inputs plus the slots the person chose to skip. */
export interface StartRequest {
  profileId: string;
  kind: InterviewKind;
  role?: RoleSource;
  graphRole?: GraphRoleSource;
  length?: InterviewLength;
  focus?: InterviewFocus;
  /** Slot ids from the plan to leave out (at least one slot must remain). */
  skip?: string[];
  /** For `redrill`: as in `PlanRequest`. */
  claims?: string[];
}

/** What the interviewer is saying now. */
export interface InterviewLine {
  kind: LineKind;
  text: string;
  /** Said before `text`: "That's fine. I'll note it and we'll move on.", "Let's come back to the question." */
  lead?: string;
  slotId?: string;
  rung?: Rung;
}

/** One line of the transcript. `at` is seconds since the interview started (server clock). */
export interface Turn {
  speaker: Speaker;
  text: string;
  at: number;
  slotId?: string;
  rung?: Rung;
  mode?: AnswerMode;
}

/** A work sample's task. The checks it looks for stay on the server until the debrief. */
export interface WorkTask {
  family: string;
  familyLabel: string;
  task: string;
  /** "CartSummary.tsx", "Weekly orders by city", "Handover note, bed 12". */
  materialName: string;
  material: string;
  materialKind: MaterialKind;
  /** A guide, not a limit. */
  minutes: number;
  /** Whether the optional assistant may be used. */
  assistant: boolean;
}

export interface AssistantTurn {
  you: string;
  assistant: string;
}

export interface InterviewSession {
  id: string;
  profileId: string;
  kind: InterviewKind;
  title: string;
  status: SessionStatus;
  createdAt: string;
  slots: PlanSlot[];
  /** Index of the slot being asked (equal to the number of slots once the last one is done). */
  slotIndex: number;
  /** What the interviewer is saying now; absent once the session is done. */
  current?: InterviewLine;
  turns: Turn[];
  minutes: number;
  role?: RoleSource;
  graphRole?: GraphRoleSource;
  task?: WorkTask;
  /** For `work`: the person's work, once handed in. */
  work?: string;
  testNote?: string;
  assistantTurns?: AssistantTurn[];
}

/** `POST /api/interview/sessions/{id}/answer`. */
export interface AnswerRequest {
  profileId: string;
  /** The answer's text (speech turned into text by the browser, or typed). Empty for a button action. */
  text: string;
  mode: AnswerMode;
  /** Default `answer`. */
  action?: AnswerAction;
}

/** `POST /api/interview/sessions/{id}/work`: hand in a work sample; the interviewer then asks about it. */
export interface WorkSubmit {
  profileId: string;
  work: string;
  testNote?: string;
}

/** `POST /api/interview/sessions/{id}/assistant`: one message to the work sample's optional assistant. */
export interface AssistantRequest {
  profileId: string;
  message: string;
  /** The work as it stands, so the assistant can see it. */
  work?: string;
}

export interface AssistantReply {
  reply: string;
  /** Messages sent to the assistant in this session so far. */
  asked: number;
  /** Messages still allowed in this session. */
  left: number;
}

/** A session id and the profile it belongs to: end, delete, and the body of other session actions. */
export interface SessionRequest {
  profileId: string;
}

/** `POST /api/interview/sessions/{id}/again`: ask one question of a finished session again (always free). */
export interface AgainRequest {
  profileId: string;
  slotId: string;
}

/** `POST /api/interview/sessions/{id}/facts`: add (or take back) what the person said as a fact marked "From you". */
export interface FactRequest {
  profileId: string;
  slotId: string;
  add: boolean;
}

/** The words of the answer a verdict rests on, word for word, and when they were said. */
export interface Quote {
  text: string;
  at: number;
}

/** A better way to say it, built only from the person's words and evidence; `[brackets]` hold what only they know. */
export interface SayLine {
  text: string;
  /** The bracketed placeholders in `text`, in order, without the brackets. */
  placeholders: string[];
  /** What it was built from: "Resume", "GitHub", "Your answer". */
  sources: string[];
}

export interface AvoidLine {
  /** The person's own words, word for word. */
  quote: string;
  advice: string;
}

export interface FixStep {
  text: string;
  /** About how long it takes. */
  minutes: number;
}

/** Which parts of a story the answer had. */
export interface StoryParts {
  situation: boolean;
  action: boolean;
  result: boolean;
  reflection: boolean;
}

export interface SlotResult {
  slotId: string;
  kind: SlotKind;
  topic: string;
  /** The question as it was first asked. */
  question: string;
  sources: SlotSource[];
  verdict: Verdict;
  /** Absent only when the verdict is Not assessed. */
  quote?: Quote;
  why: string;
  say?: SayLine;
  avoid?: AvoidLine;
  fix?: FixStep;
  story?: StoryParts;
  /** A fact from the answer that is not on the resume, offered to be kept as "From you". */
  newFact?: string;
  factAdded?: boolean;
  /** Re-drill: the claim's previous mark, for "Shaky 16 Oct → Defended today". */
  previous?: PreviousMark;
  /** Re-drill, only when this and the previous verdict are both Couldn't defend: a line that says less, from the answer's own words. */
  softer?: SayLine;
}

/** A claim's mark before this session. */
export interface PreviousMark {
  verdict: Verdict;
  /** ISO date. */
  on: string;
}

/** A work sample's check: what a full answer covers, and whether the work or the answers showed it. */
export interface CheckResult {
  check: string;
  verdict: Verdict;
  /** Word for word from the work, the test note or an answer. */
  quote?: string;
}

export interface VerdictCounts {
  defended: number;
  shaky: number;
  couldnt: number;
  notAssessed: number;
}

export interface Debrief {
  sessionId: string;
  kind: InterviewKind;
  title: string;
  createdAt: string;
  /** How long it took, in whole minutes. */
  minutes: number;
  counts: VerdictCounts;
  /** The one thing to work on first, chosen in code. */
  firstThing?: string;
  /** Its slot, for "Ask this one again". */
  firstSlotId?: string;
  /** A line about the role's asks, worked out in code ("Of Headout's 2 asks you show, you defended 1 …"). */
  summary?: string;
  results: SlotResult[];
  checks?: CheckResult[];
  /** What was seen of the assistant's use, as facts ("Asked the assistant 2 things · changed its version before using it"). */
  assistantUse?: string;
  transcript: Turn[];
  /** The day the transcript is deleted (ISO date). */
  keptUntil: string;
  role?: RoleSource;
  graphRole?: GraphRoleSource;
  task?: WorkTask;
  work?: string;
  /** Re-drill: how many claims ranked above their previous mark ("2 of 3 moved up"), counted in code. */
  redrill?: RedrillMoved;
}

export interface RedrillMoved {
  moved: number;
  of: number;
}

/** `GET /api/interview/sessions/{id}?profileId=`: the session, and its debrief once it is done. */
export interface SessionView {
  session: InterviewSession;
  debrief?: Debrief;
}

export interface SessionSummary {
  id: string;
  kind: InterviewKind;
  title: string;
  createdAt: string;
  status: SessionStatus;
  /** One per slot, in order (the pips). */
  verdicts: Verdict[];
  counts?: VerdictCounts;
  role?: RoleSource;
  graphRole?: GraphRoleSource;
}

export interface ClaimMark {
  sessionId: string;
  date: string;
  verdict: Verdict;
}

/** One evidence line asked about across sessions, with its best verdict. The best counts: Shaky can only get better. */
export interface ClaimBest {
  text: string;
  source: string;
  best: Verdict;
  /** The date of the best verdict. */
  on: string;
  marks: ClaimMark[];
}

/** A fact the person kept from practice, marked "From you". */
export interface PracticeFact {
  text: string;
  sessionId: string;
  slotId: string;
  /** When it was said, seconds into the session. */
  at: number;
  date: string;
}

/** `GET /api/interview/sessions?profileId=`: the person's practice, newest first. */
export interface PracticeHistory {
  sessions: SessionSummary[];
  claims: ClaimBest[];
  facts: PracticeFact[];
  /** Practice interviews left today (free while we test it). */
  leftToday?: number;
}

/**
 * A Fix step placed somewhere: into a Prepare plan (`where: 'plan'`, its current week) or kept in Practice. The id is the
 * claim mark's; the text and minutes are the debrief's, copied by the server. Ticking it never changes a verdict.
 */
export interface FixTask {
  id: string;
  sessionId: string;
  slotId: string;
  /** The evidence line the step is for. */
  claimText: string;
  text: string;
  /** About how long it takes. */
  minutes: number;
  where: FixWhere;
  /** For `plan`. */
  planId?: string;
  addedAt: string;
  doneAt?: string;
}

/** A claim's latest mark. */
export interface LastMark {
  verdict: Verdict;
  /** ISO date. */
  on: string;
  rung: Rung;
}

/** One claim a re-drill can bring back: its best verdict is Shaky or Couldn't defend and the line is still the person's. */
export interface RedrillClaim {
  /** The folded text of the evidence line; what `PlanRequest.claims` names. */
  key: string;
  text: string;
  /** "Resume · line 15", "Story · Behavioural stories". */
  source: string;
  best: Verdict;
  last: LastMark;
  /** How many sessions asked it. */
  times: number;
  /** Where it would open. */
  start: Rung;
  fix?: FixTask;
}

/** `GET /api/interview/redrill?profileId=`: the claims in re-drill order (code's), and the Fix steps kept in Practice. */
export interface RedrillView {
  claims: RedrillClaim[];
  kept: FixTask[];
  leftToday?: number;
}

/** `POST /api/interview/fixes`: place a session's Fix step. No `planId` = keep it in Practice. */
export interface FixAdd {
  profileId: string;
  sessionId: string;
  slotId: string;
  planId?: string;
}

/** `PATCH /api/interview/fixes/{id}`: tick or untick. */
export interface FixUpdate {
  profileId: string;
  done: boolean;
}
