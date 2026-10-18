/**
 * Defensive readers for the practice API (`shared/interview.ts`). Everything that crosses a trust boundary (a server
 * answer, a value read back from this browser's storage) goes through these: a value of the wrong type is nothing, a
 * string that is too long is nothing (a quote is never cut, because a cut quote is no longer word for word), a list is
 * capped at the contract's limits, and an item of the wrong shape is dropped, never trusted.
 *
 * The readers add nothing the server did not send, with one exception that is arithmetic and not a claim: a debrief
 * whose counts are missing gets them counted from its own results. A `null` is read as an absent optional, because the
 * server omits `None` fields but a proxy or a test double may not.
 */
import type {
  AssistantReply,
  AssistantTurn,
  AvoidLine,
  CheckResult,
  ClaimBest,
  ClaimMark,
  Debrief,
  FixStep,
  GraphRoleSource,
  InterviewKind,
  InterviewLine,
  InterviewPlan,
  InterviewSession,
  LineKind,
  MaterialKind,
  PlanSlot,
  PracticeFact,
  PracticeHistory,
  Quote,
  RoleSource,
  SayLine,
  SessionStatus,
  SessionSummary,
  SessionView,
  SlotKind,
  SlotResult,
  SlotSource,
  Speaker,
  StoryParts,
  Turn,
  Verdict,
  VerdictCounts,
  WorkTask,
} from '../../shared/interview';

export const isObj = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v);

export const arr = (v: unknown): unknown[] => (Array.isArray(v) ? v : []);

export const finite = (v: unknown): number | undefined => (typeof v === 'number' && Number.isFinite(v) ? v : undefined);

/** A whole number of at least `min` (default 0), rounded; anything else is nothing. */
export const whole = (v: unknown, min = 0): number | undefined => {
  const n = finite(v);
  return n !== undefined && Math.round(n) >= min ? Math.round(n) : undefined;
};

export const oneOf = <T extends string>(allowed: readonly T[], v: unknown): T | undefined =>
  typeof v === 'string' && (allowed as readonly string[]).includes(v) ? (v as T) : undefined;

/** A non-empty string no longer than `max`; anything else is nothing. */
export const text = (v: unknown, max: number): string | undefined => {
  if (typeof v !== 'string') return undefined;
  const t = v.trim();
  return t !== '' && t.length <= max ? t : undefined;
};

/** A string no longer than `max` that may be empty (the person's work can be); anything else is nothing. */
export const str = (v: unknown, max: number): string | undefined => (typeof v === 'string' && v.length <= max ? v : undefined);

/** An ISO time that parses. */
export const isoTime = (v: unknown): string | undefined => {
  const t = text(v, 64);
  return t !== undefined && Number.isFinite(Date.parse(t)) ? t : undefined;
};

/** `YYYY-MM-DD` that names a real day. */
export const isoDay = (v: unknown): string | undefined => {
  if (typeof v !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(v)) return undefined;
  const [y, m, d] = v.split('-').map(Number);
  const date = new Date(y, m - 1, d);
  return date.getFullYear() === y && date.getMonth() === m - 1 && date.getDate() === d ? v : undefined;
};

/** The items of a list that read, at most `max` of them. */
export const list = <T>(raw: unknown, read: (item: unknown) => T | null, max: number): T[] =>
  arr(raw)
    .flatMap((item) => {
      const v = read(item);
      return v ? [v] : [];
    })
    .slice(0, max);

/* ───────────────────────── the vocabularies ───────────────────────── */

export const KINDS: readonly InterviewKind[] = ['role', 'resume', 'stories', 'work'];
export const SLOT_KINDS: readonly SlotKind[] = ['ask', 'gap', 'claim', 'story', 'task'];
export const VERDICTS: readonly Verdict[] = ['defended', 'shaky', 'couldnt', 'not_assessed'];
const RUNGS = ['surface', 'push', 'floor'] as const;
const MODES = ['speech', 'typed'] as const;
const STATUSES: readonly SessionStatus[] = ['active', 'done'];
const LINE_KINDS: readonly LineKind[] = ['question', 'followup', 'floor', 'done'];
const SPEAKERS: readonly Speaker[] = ['interviewer', 'you'];
const MATERIALS: readonly MaterialKind[] = ['code', 'table', 'text'];
const ORIGINS = ['posting', 'evidence', 'role'] as const;

/** Limits: generous, so a real answer is never refused, but a page cannot be flooded. */
const MAX_SLOTS = 12;
const MAX_SOURCES = 6;
const MAX_TURNS = 400;
const MAX_ASSISTANT = 60;
const MAX_WORK = 20_000;

/* ───────────────────────── plan and session ───────────────────────── */

export function readRole(raw: unknown): RoleSource | undefined {
  if (!isObj(raw)) return undefined;
  const runId = text(raw.runId, 96);
  const jobId = text(raw.jobId, 96);
  return runId && jobId ? { runId, jobId } : undefined;
}

/** A role of the person's graph: its id has the shape the address allows (a slug). */
export function readGraphRole(raw: unknown): GraphRoleSource | undefined {
  if (!isObj(raw)) return undefined;
  const roleId = text(raw.roleId, 64);
  return roleId && /^[A-Za-z0-9_-]+$/.test(roleId) ? { roleId } : undefined;
}

export function readSource(raw: unknown): SlotSource | null {
  if (!isObj(raw)) return null;
  const origin = oneOf(ORIGINS, raw.origin);
  const quote = text(raw.text, 1500);
  if (!origin || !quote) return null;
  const source = text(raw.source, 120);
  return { origin, text: quote, ...(source ? { source } : {}) };
}

export function readSlot(raw: unknown): PlanSlot | null {
  if (!isObj(raw)) return null;
  const id = text(raw.id, 16);
  const kind = oneOf(SLOT_KINDS, raw.kind);
  if (!id || !kind) return null;
  return { id, kind, topic: text(raw.topic, 160) ?? '', sources: list(raw.sources, readSource, MAX_SOURCES) };
}

/** The plan, or null when the answer is not one. A plan with no slot is a plan (the screen says there is nothing to ask). */
export function readPlan(raw: unknown): InterviewPlan | null {
  if (!isObj(raw)) return null;
  const kind = oneOf(KINDS, raw.kind);
  if (!kind || !Array.isArray(raw.slots)) return null;
  const role = readRole(raw.role);
  const graphRole = readGraphRole(raw.graphRole);
  const note = text(raw.note, 400);
  const family = text(raw.family, 120);
  return {
    kind,
    title: text(raw.title, 300) ?? '',
    slots: list(raw.slots, readSlot, MAX_SLOTS),
    minutes: whole(raw.minutes) ?? 0,
    ...(role ? { role } : {}),
    ...(graphRole ? { graphRole } : {}),
    ...(note ? { note } : {}),
    ...(family ? { family } : {}),
  };
}

export function readLine(raw: unknown): InterviewLine | undefined {
  if (!isObj(raw)) return undefined;
  const kind = oneOf(LINE_KINDS, raw.kind);
  const line = text(raw.text, 1500);
  if (!kind || !line) return undefined;
  const lead = text(raw.lead, 600);
  const slotId = text(raw.slotId, 16);
  const rung = oneOf(RUNGS, raw.rung);
  return { kind, text: line, ...(lead ? { lead } : {}), ...(slotId ? { slotId } : {}), ...(rung ? { rung } : {}) };
}

export function readTurn(raw: unknown): Turn | null {
  if (!isObj(raw)) return null;
  const speaker = oneOf(SPEAKERS, raw.speaker);
  const said = text(raw.text, 7000);
  const at = whole(raw.at);
  if (!speaker || !said || at === undefined) return null;
  const slotId = text(raw.slotId, 16);
  const rung = oneOf(RUNGS, raw.rung);
  const mode = oneOf(MODES, raw.mode);
  return { speaker, text: said, at, ...(slotId ? { slotId } : {}), ...(rung ? { rung } : {}), ...(mode ? { mode } : {}) };
}

export function readTask(raw: unknown): WorkTask | undefined {
  if (!isObj(raw)) return undefined;
  const task = text(raw.task, 1500);
  const materialName = text(raw.materialName, 160);
  const material = str(raw.material, MAX_WORK);
  const materialKind = oneOf(MATERIALS, raw.materialKind);
  if (!task || !materialName || material === undefined || !materialKind) return undefined;
  const familyLabel = text(raw.familyLabel, 160) ?? text(raw.family, 160) ?? '';
  return {
    family: text(raw.family, 160) ?? familyLabel,
    familyLabel,
    task,
    materialName,
    material,
    materialKind,
    minutes: whole(raw.minutes, 1) ?? 10,
    assistant: raw.assistant === true,
  };
}

function readAssistantTurn(raw: unknown): AssistantTurn | null {
  if (!isObj(raw)) return null;
  const you = text(raw.you, 2000);
  const assistant = text(raw.assistant, 8000);
  return you && assistant ? { you, assistant } : null;
}

/** The session, or null when the answer is not one (no id, no kind, no question to ask). */
export function readSession(raw: unknown): InterviewSession | null {
  if (!isObj(raw)) return null;
  const id = text(raw.id, 64);
  const kind = oneOf(KINDS, raw.kind);
  const status = oneOf(STATUSES, raw.status);
  const slots = list(raw.slots, readSlot, MAX_SLOTS);
  if (!id || !kind || !status || slots.length === 0) return null;
  const slotIndex = Math.min(whole(raw.slotIndex) ?? 0, slots.length);
  const current = readLine(raw.current);
  const role = readRole(raw.role);
  const graphRole = readGraphRole(raw.graphRole);
  const task = readTask(raw.task);
  const work = str(raw.work, MAX_WORK);
  const testNote = str(raw.testNote, 4000);
  const assistantTurns = list(raw.assistantTurns, readAssistantTurn, MAX_ASSISTANT);
  return {
    id,
    profileId: text(raw.profileId, 64) ?? '',
    kind,
    title: text(raw.title, 300) ?? '',
    status,
    createdAt: isoTime(raw.createdAt) ?? new Date().toISOString(),
    slots,
    slotIndex,
    ...(current ? { current } : {}),
    turns: list(raw.turns, readTurn, MAX_TURNS),
    minutes: whole(raw.minutes) ?? 0,
    ...(role ? { role } : {}),
    ...(graphRole ? { graphRole } : {}),
    ...(task ? { task } : {}),
    ...(work !== undefined ? { work } : {}),
    ...(testNote !== undefined ? { testNote } : {}),
    ...(assistantTurns.length > 0 ? { assistantTurns } : {}),
  };
}

export function readReply(raw: unknown): AssistantReply | null {
  if (!isObj(raw)) return null;
  const reply = text(raw.reply, 8000);
  if (!reply) return null;
  return { reply, asked: whole(raw.asked) ?? 0, left: whole(raw.left) ?? 0 };
}

/* ───────────────────────── the debrief ───────────────────────── */

function readCounts(raw: unknown): VerdictCounts | undefined {
  if (!isObj(raw)) return undefined;
  const defended = whole(raw.defended);
  const shaky = whole(raw.shaky);
  const couldnt = whole(raw.couldnt);
  const notAssessed = whole(raw.notAssessed);
  if (defended === undefined || shaky === undefined || couldnt === undefined || notAssessed === undefined) return undefined;
  return { defended, shaky, couldnt, notAssessed };
}

/** Counts worked out from the verdicts themselves: arithmetic, used only when the server's own counts are missing. */
export function countVerdicts(verdicts: readonly Verdict[]): VerdictCounts {
  const counts: VerdictCounts = { defended: 0, shaky: 0, couldnt: 0, notAssessed: 0 };
  for (const v of verdicts) {
    if (v === 'defended') counts.defended += 1;
    else if (v === 'shaky') counts.shaky += 1;
    else if (v === 'couldnt') counts.couldnt += 1;
    else counts.notAssessed += 1;
  }
  return counts;
}

function readQuote(raw: unknown): Quote | undefined {
  if (!isObj(raw)) return undefined;
  const quote = text(raw.text, 7000);
  const at = whole(raw.at);
  return quote && at !== undefined ? { text: quote, at } : undefined;
}

function readSay(raw: unknown): SayLine | undefined {
  if (!isObj(raw)) return undefined;
  const say = text(raw.text, 800);
  if (!say) return undefined;
  const strings = (v: unknown, max: number) => list(v, (s) => text(s, max) ?? null, 12);
  return { text: say, placeholders: strings(raw.placeholders, 100), sources: strings(raw.sources, 80) };
}

function readAvoid(raw: unknown): AvoidLine | undefined {
  if (!isObj(raw)) return undefined;
  const quote = text(raw.quote, 800);
  const advice = text(raw.advice, 400);
  return quote && advice ? { quote, advice } : undefined;
}

function readFix(raw: unknown): FixStep | undefined {
  if (!isObj(raw)) return undefined;
  const step = text(raw.text, 500);
  const minutes = whole(raw.minutes);
  return step && minutes !== undefined ? { text: step, minutes } : undefined;
}

function readStory(raw: unknown): StoryParts | undefined {
  if (!isObj(raw)) return undefined;
  return { situation: raw.situation === true, action: raw.action === true, result: raw.result === true, reflection: raw.reflection === true };
}

function readResult(raw: unknown): SlotResult | null {
  if (!isObj(raw)) return null;
  const slotId = text(raw.slotId, 16);
  const kind = oneOf(SLOT_KINDS, raw.kind);
  const verdict = oneOf(VERDICTS, raw.verdict);
  if (!slotId || !kind || !verdict) return null;
  const quote = readQuote(raw.quote);
  const say = readSay(raw.say);
  const avoid = readAvoid(raw.avoid);
  const fix = readFix(raw.fix);
  const story = readStory(raw.story);
  const newFact = text(raw.newFact, 500);
  return {
    slotId,
    kind,
    topic: text(raw.topic, 160) ?? '',
    question: text(raw.question, 1500) ?? '',
    sources: list(raw.sources, readSource, MAX_SOURCES),
    verdict,
    // a verdict other than Not assessed rests on a quote; one that arrives without it is shown as it came, and the screen says so
    ...(quote ? { quote } : {}),
    why: str(raw.why, 800) ?? '',
    ...(say ? { say } : {}),
    ...(avoid ? { avoid } : {}),
    ...(fix ? { fix } : {}),
    ...(story ? { story } : {}),
    ...(newFact ? { newFact } : {}),
    ...(raw.factAdded === true ? { factAdded: true } : {}),
  };
}

function readCheck(raw: unknown): CheckResult | null {
  if (!isObj(raw)) return null;
  const check = text(raw.check, 500);
  const verdict = oneOf(VERDICTS, raw.verdict);
  if (!check || !verdict) return null;
  const quote = text(raw.quote, MAX_WORK);
  return { check, verdict, ...(quote ? { quote } : {}) };
}

/** The debrief, or null when the answer is not one. */
export function readDebrief(raw: unknown): Debrief | null {
  if (!isObj(raw)) return null;
  const sessionId = text(raw.sessionId, 64);
  const kind = oneOf(KINDS, raw.kind);
  if (!sessionId || !kind) return null;
  const results = list(raw.results, readResult, MAX_SLOTS);
  const checks = list(raw.checks, readCheck, 12);
  const firstThing = text(raw.firstThing, 800);
  const firstSlotId = text(raw.firstSlotId, 16);
  const summary = text(raw.summary, 800);
  const assistantUse = text(raw.assistantUse, 800);
  const role = readRole(raw.role);
  const graphRole = readGraphRole(raw.graphRole);
  const task = readTask(raw.task);
  const work = str(raw.work, MAX_WORK);
  const kept = isoDay(raw.keptUntil) ?? (isoTime(raw.keptUntil) ? isoTime(raw.keptUntil)?.slice(0, 10) : undefined);
  return {
    sessionId,
    kind,
    title: text(raw.title, 300) ?? '',
    createdAt: isoTime(raw.createdAt) ?? '',
    minutes: whole(raw.minutes) ?? 0,
    counts: readCounts(raw.counts) ?? countVerdicts(results.map((r) => r.verdict)),
    ...(firstThing ? { firstThing } : {}),
    ...(firstSlotId ? { firstSlotId } : {}),
    ...(summary ? { summary } : {}),
    results,
    ...(checks.length > 0 ? { checks } : {}),
    ...(assistantUse ? { assistantUse } : {}),
    transcript: list(raw.transcript, readTurn, MAX_TURNS),
    keptUntil: kept ?? '',
    ...(role ? { role } : {}),
    ...(graphRole ? { graphRole } : {}),
    ...(task ? { task } : {}),
    ...(work !== undefined ? { work } : {}),
  };
}

export function readSessionView(raw: unknown): SessionView | null {
  if (!isObj(raw)) return null;
  const session = readSession(raw.session);
  if (!session) return null;
  const debrief = readDebrief(raw.debrief);
  return { session, ...(debrief ? { debrief } : {}) };
}

/* ───────────────────────── history ───────────────────────── */

function readSummary(raw: unknown): SessionSummary | null {
  if (!isObj(raw)) return null;
  const id = text(raw.id, 64);
  const kind = oneOf(KINDS, raw.kind);
  const status = oneOf(STATUSES, raw.status);
  const createdAt = isoTime(raw.createdAt);
  if (!id || !kind || !status || !createdAt) return null;
  const verdicts = list(raw.verdicts, (v) => oneOf(VERDICTS, v) ?? null, MAX_SLOTS);
  const counts = readCounts(raw.counts);
  const role = readRole(raw.role);
  const graphRole = readGraphRole(raw.graphRole);
  return {
    id,
    kind,
    title: text(raw.title, 300) ?? '',
    createdAt,
    status,
    verdicts,
    ...(counts ? { counts } : {}),
    ...(role ? { role } : {}),
    ...(graphRole ? { graphRole } : {}),
  };
}

function readMark(raw: unknown): ClaimMark | null {
  if (!isObj(raw)) return null;
  const sessionId = text(raw.sessionId, 64);
  const date = isoDay(raw.date) ?? isoTime(raw.date);
  const verdict = oneOf(VERDICTS, raw.verdict);
  return sessionId && date && verdict ? { sessionId, date, verdict } : null;
}

function readClaim(raw: unknown): ClaimBest | null {
  if (!isObj(raw)) return null;
  const claim = text(raw.text, 1500);
  const best = oneOf(VERDICTS, raw.best);
  const on = isoDay(raw.on) ?? isoTime(raw.on);
  if (!claim || !best || !on) return null;
  return { text: claim, source: text(raw.source, 120) ?? '', best, on, marks: list(raw.marks, readMark, 40) };
}

function readFact(raw: unknown): PracticeFact | null {
  if (!isObj(raw)) return null;
  const fact = text(raw.text, 800);
  const sessionId = text(raw.sessionId, 64);
  const slotId = text(raw.slotId, 16);
  const at = whole(raw.at);
  const date = isoDay(raw.date) ?? isoTime(raw.date);
  return fact && sessionId && slotId && at !== undefined && date ? { text: fact, sessionId, slotId, at, date } : null;
}

export function readHistory(raw: unknown): PracticeHistory | null {
  if (!isObj(raw) || !Array.isArray(raw.sessions)) return null;
  const leftToday = whole(raw.leftToday);
  return {
    sessions: list(raw.sessions, readSummary, 200),
    claims: list(raw.claims, readClaim, 400),
    facts: list(raw.facts, readFact, 200),
    ...(leftToday !== undefined ? { leftToday } : {}),
  };
}
