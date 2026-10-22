/**
 * The words of the practice room and its setup, kept apart from the markup so they can be read on their own. Pure
 * functions of the contract in `shared/interview.ts`; nothing here touches the DOM.
 *
 * The rules this file keeps (project-soul.md): a verdict is one of four words and never a number; Not assessed never
 * counts against the person; there is no reading of personality (no confidence, tone, accent, pace, filler words); the
 * privacy line says what is true (who turns the speech into text, that only text is kept, that nothing is shared with
 * an employer); and nothing here shows a price.
 */
import type { InterviewKind, PlanSlot, Rung, SlotKind, Verdict } from '../../shared/interview';
import type { RoleRequirement } from '../../shared/types';
import { plural } from '../lib/ui';

/** Who turns the person's speech into text: Sarvam AI when the server offers it, else the browser. */
export type SpeechBy = 'sarvam' | 'browser';

export const KIND_LABEL: Record<InterviewKind, string> = {
  role: 'This role',
  resume: 'Defend your resume',
  stories: 'Behavioural stories',
  work: 'Work sample',
  redrill: 'Re-drill what wobbled',
};

export const SLOT_KIND_LABEL: Record<SlotKind, string> = {
  ask: 'An ask you show',
  gap: 'A gap',
  claim: 'A claim on your resume',
  story: 'A story',
  task: 'About your work',
};

export const VERDICT_LABEL: Record<Verdict, string> = {
  defended: 'Defended',
  shaky: 'Shaky',
  couldnt: "Couldn't defend",
  not_assessed: 'Not assessed',
};

/** Wherever speech is promised, with the browser's speech. */
export const PRIVACY_LINE =
  'Your browser turns your speech into text. Only the text reaches Leafout, kept for your debrief for 30 days. No audio or video is recorded.';

/** Where speech is set up: Chrome's recogniser is Google's. */
export const CHROME_NOTE = "In Chrome, speech-to-text runs on Google's speech service.";

/** The same promise when Sarvam AI writes the words down: the voice leaves the browser for it, and is dropped there. */
export const PRIVACY_LINE_SARVAM =
  'Your voice goes to Sarvam AI, an Indian speech service, to be written down. Leafout keeps only the text, for your debrief, for 30 days, and keeps no audio.';

/** Away from the room (Practice, the debrief, Progress), true whoever wrote the words down. */
export const KEPT_LINE =
  'Leafout keeps only the text of your answers, for your debrief, for 30 days. No audio or video is kept.';

/** Where speech is set up, with Sarvam. */
export const SARVAM_NOTE = 'Speech by Sarvam AI, made in India for Indian English and Hinglish.';

/** The privacy line that is true for whoever is writing the words down. */
export const privacyLine = (by: SpeechBy): string => (by === 'sarvam' ? PRIVACY_LINE_SARVAM : PRIVACY_LINE);

/** The one line on who does the speech, shown where speech is set up. */
export const engineNote = (by: SpeechBy): string => (by === 'sarvam' ? SARVAM_NOTE : CHROME_NOTE);

export const FREE_LINE = 'Free while we test it · 3 a day';

/** The same line for an account with no limits: the count of 3 a day is not true for it, so it is left out. */
export const FREE_LINE_UNLIMITED = 'Free while we test it';

/** Where the line also says how many are left today, an account with no limits reads this instead. */
export const UNLIMITED_LINE = 'Unlimited for this account';

/** The line that says what practice costs: with the daily count, or without it for an account with no limits. */
export const freeLineFor = (unlimited: boolean): string => (unlimited ? FREE_LINE_UNLIMITED : FREE_LINE);

export const NOT_RECORDED = 'Nothing is recorded';

/** The short form of the promise on the room's bar. With Sarvam the voice does leave the browser for a moment, so it says what is true. */
export const NO_AUDIO_KEPT = 'No audio is kept';
export const keptLine = (by: SpeechBy): string => (by === 'sarvam' ? NO_AUDIO_KEPT : NOT_RECORDED);

export const NO_EMPLOYER = 'Nothing here is shared with an employer.';

/* ───────────────────────── the edges ───────────────────────── */

export const MIC_BLOCKED = {
  title: "Leafout can't hear you.",
  body: 'Your browser blocked the microphone. Allow it from the address bar, or type your answers.',
};

export const NO_SPEECH = "This browser can't turn speech into text. Type your answers, or open Leafout in Chrome, Edge or Safari.";

export const SPEECH_NETWORK = {
  title: "The speech service couldn't be reached.",
  body: 'Your words so far are kept. Type the rest, or try again.',
};

export const SILENCE = 'Take your time. Press Say it again and the question repeats.';

export const NO_VOICE = "This browser can't read questions aloud. They show as text.";

/** The line every interview ends on is the server's; this is only the wait before the debrief is ready. */
export const WRITING_DEBRIEF = 'Writing your debrief. It takes a few seconds.';

export const END_EARLY = {
  title: 'End the interview now?',
  body: "The questions you haven't answered will be Not assessed, which never counts against you. You'll get a debrief for the rest.",
  bodyEmpty: 'Nothing has been answered yet, so every question would be Not assessed, which never counts against you.',
  yes: 'End and see the debrief',
  no: 'Keep going',
};

/** What an answer sounds like when it is first read back: no judgement, only where it came from. */
export const SAID_HERE = 'Written down as you speak';
export const SAID_PAUSED = 'Your answer so far. Fix any word, then carry on or finish.';

/** The label and the empty hint of the box the words land in: with Sarvam they arrive after each pause, not word by word. */
export const saidLabel = (by: SpeechBy): string => (by === 'sarvam' ? 'Written down after each pause' : SAID_HERE);
export const LISTEN_HINT = 'Your words appear here as you speak.';
export const LISTEN_HINT_SARVAM = 'Your words appear after each pause.';
export const listenHint = (by: SpeechBy): string => (by === 'sarvam' ? LISTEN_HINT_SARVAM : LISTEN_HINT);

/** The quiet tail while a piece of the answer is being written down (Sarvam). Never part of the answer. */
export const WRITING_WORDS = 'Writing down what you said…';

/** One piece Sarvam could not make out: the rest carries on. */
export const PIECE_UNCLEAR = "We couldn't make out a bit of that. Say it again, or type it.";

/** Sarvam cannot be used any more in this interview (the server's own sentence wins when it sends one). */
export const SARVAM_DOWN = "Sarvam's speech isn't available right now. Your browser's speech will take over.";

/** The same, in a browser that has no speech of its own: typing is what is left. */
export const SARVAM_DOWN_TYPE = "Sarvam's speech isn't available right now. You can type your answers.";

/* ───────────────────────── the room ───────────────────────── */

/** The label above the interviewer's line. */
export const LINE_LABEL = {
  question: 'Interviewer',
  followup: 'Interviewer · follow-up',
  floor: 'Interviewer · an easier one',
  done: 'Interviewer',
} as const;

/** "2:31": minutes and seconds into the interview. */
export function clock(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

/** "About 14 minutes left": a guide from the questions still to ask, never a countdown. */
export function minutesLeft(minutes: number, slotIndex: number, total: number): number {
  if (total <= 0 || minutes <= 0) return 1;
  const remaining = Math.max(1, total - slotIndex);
  return Math.max(1, Math.round((minutes * remaining) / total));
}

export const leftLine = (n: number) => `About ${plural(n, 'minute')} left`;

/** "Question 2 of 5 · An ask you show". */
export const progressLine = (position: number, total: number, slot: PlanSlot | undefined) =>
  `Question ${position} of ${total}${slot ? ` · ${SLOT_KIND_LABEL[slot.kind]}` : ''}`;

const NUMBERS = ['no', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine', 'ten', 'eleven', 'twelve'];

/** "Five questions", "One question": a count at the start of a sentence. */
export function questionsWord(n: number): string {
  const word = n >= 0 && n < NUMBERS.length ? NUMBERS[n] : String(n);
  return `${word.charAt(0).toUpperCase()}${word.slice(1)} ${n === 1 ? 'question' : 'questions'}`;
}

/** Cut at a word boundary, with an ellipsis; never mid-word. */
export function clip(s: string, max: number): string {
  const t = s.trim();
  if (t.length <= max) return t;
  const cut = t.slice(0, max);
  const at = cut.lastIndexOf(' ');
  return `${(at > max * 0.5 ? cut.slice(0, at) : cut).replace(/[\s,.;:!?-]+$/, '')}…`;
}

/** A quoted line, in curly quotes. */
export const quoted = (s: string) => `“${s}”`;

/** What the setup page says under the title, per kind (a role's own line is built from its tracker entry). */
export const KIND_BLURB: Record<InterviewKind, string> = {
  role: 'Built from the posting: what it asks, what you show, and your gaps.',
  resume: 'No posting needed. It picks lines from your resume and asks you to back them up.',
  stories: '“Tell me about a time…”, built from your own experience, with follow-ups that make each story complete.',
  work: 'A small real task for your kind of work, then questions on your choices.',
  redrill: 'The claims you didn’t fully back up, from all your sessions. Each comes back where it wobbled last time.',
};

/** The line under the title of a role taken from the person's graph, which has no posting. */
export const GRAPH_ROLE_SUB = 'From your skill graph · no posting';

/** What practice for a graph role is, for the side summary. */
export const GRAPH_ROLE_BLURB = 'Built from what the role needs: your biggest gaps first, then what you can defend.';

/** Where a graph role's requirement stands: met, below the role's level, or not shown (the backend's `shortfall`). */
export type RequirementStanding = 'met' | 'below' | 'notShown';

export function requirementStanding(r: RoleRequirement): RequirementStanding {
  if (!r.have) return 'notShown';
  return r.current < r.target ? 'below' : 'met';
}

/** "6 requirements · 2 met · 2 below the role's level · 2 not shown yet": counts, never a percentage. */
export function requirementsLine(requirements: readonly RoleRequirement[]): string {
  const n = (s: RequirementStanding) => requirements.filter((r) => requirementStanding(r) === s).length;
  const parts = [plural(requirements.length, 'requirement')];
  if (n('met') > 0) parts.push(`${n('met')} met`);
  if (n('below') > 0) parts.push(`${n('below')} below the role's level`);
  if (n('notShown') > 0) parts.push(`${n('notShown')} not shown yet`);
  return parts.join(' · ');
}

/** The three lines of "How it works" on the setup page. */
export const HOW_IT_WORKS = [
  'One question at a time. Follow-ups come from what you just said.',
  "“I don't know” is a fine answer. It's noted, and you get a step to close it.",
] as const;

export const HOW_IT_ENDS = 'At the end: each answer Defended, Shaky, Couldn\'t defend or Not assessed, with your own words quoted. No score.';

export const VERDICT_ORDER: readonly Verdict[] = ['defended', 'shaky', 'couldnt', 'not_assessed'];

/** The sentence under a day's limit that is used up: the server's own sentence wins when it sends one. */
export const LIMIT_USED = "You've used today's practice interviews. They're free while we test them. More tomorrow. Your debriefs stay here.";

/* ───────────────────────── re-drill (I2) ───────────────────────── */

/** `contract.interview.REDRILL_SHORT` and `REDRILL_MAX`: a short re-drill asks the top 3, All at most 6. */
export const REDRILL_SHORT = 3;
export const REDRILL_MAX = 6;
/** About three minutes a claim (the server's own estimate for a re-drill). */
export const REDRILL_MINUTES = 3;

/** Where a claim opens, after "Starts at". */
export const START_LABEL: Record<Rung, string> = {
  surface: 'the opener, in new words',
  push: 'the follow-up',
  floor: 'the easier one',
};

/** "once", "twice", "3 times". */
export const timesWord = (n: number) => (n <= 1 ? 'once' : n === 2 ? 'twice' : `${n} times`);

/** The four rules a re-drill keeps, as the setup screen says them. */
export const REDRILL_RULES: readonly { title: string; body: string }[] = [
  {
    title: 'It starts where it wobbled',
    body: "A Shaky claim opens at the follow-up you stumbled on. A Couldn't defend claim is asked from the top again, in new words.",
  },
  { title: 'Your Fix steps count', body: "Claims whose Fix step you ticked come first: that's where you have something new to say." },
  { title: 'Your best answer is kept', body: "Progress keeps your best verdict. A worse answer today doesn't lower it." },
  { title: 'Judged on your words, as always', body: 'Every verdict quotes what you said. No score, no streak.' },
];

/** The room's line above a re-drilled claim: "Back from 16 Oct · Shaky". */
export const backLine = (on: string, verdict: Verdict, day: (d: string) => string) => `Back from ${day(on)} · ${VERDICT_LABEL[verdict]}`;

/** The label above a re-drill's first line when it opens at the follow-up. */
export const WOBBLED_LABEL = 'Follow-up · where it wobbled';
