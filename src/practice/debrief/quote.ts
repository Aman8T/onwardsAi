/**
 * The word-level helpers of the debrief, kept apart from the markup so they can be read (and checked) on their own.
 * Pure functions of the contract in `shared/interview.ts`; nothing here touches the DOM.
 *
 * What the rules need from this file (project-soul.md): the deciding quote is shown inside the person's own answer, word
 * for word, so the verdict can be checked; a "Say" line shows what only the person knows as a dashed placeholder and never
 * fills it in; and every turn of the transcript has an address the verdicts can link to.
 */
import type { Debrief, Quote, SlotResult, Turn } from '../../../shared/interview';

/* ───────────────────────── the deciding quote, inside the answer ───────────────────────── */

export interface Piece {
  text: string;
  mark: boolean;
}

const escape = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
const APOSTROPHE = /['‘’ʼ]/g;

/** The pattern of one word of a quote: an apostrophe is any of the apostrophes a speech engine or a keyboard makes. */
const wordPattern = (word: string) => escape(word).replace(APOSTROPHE, "['‘’ʼ]");

/** Where `quote` sits in `answer`: exactly, else ignoring case, runs of spaces and the kind of apostrophe. */
export function locate(answer: string, quote: string): [number, number] | null {
  const q = quote.trim();
  if (q === '') return null;
  const exact = answer.indexOf(q);
  if (exact >= 0) return [exact, exact + q.length];
  const words = q.split(/\s+/).filter(Boolean);
  if (words.length === 0) return null;
  const m = new RegExp(words.map(wordPattern).join('\\s+'), 'i').exec(answer);
  return m ? [m.index, m.index + m[0].length] : null;
}

/** The answer in up to three pieces, the middle one the quote; one piece, unmarked, when the quote is not in it. */
export function markPieces(answer: string, quote: string | undefined): Piece[] {
  const at = quote ? locate(answer, quote) : null;
  if (!at) return [{ text: answer, mark: false }];
  const pieces: Piece[] = [
    { text: answer.slice(0, at[0]), mark: false },
    { text: answer.slice(at[0], at[1]), mark: true },
    { text: answer.slice(at[1]), mark: false },
  ];
  return pieces.filter((p) => p.text !== '');
}

/** True when `quote` is a passage of `answer` (so the page can show it marked in place). */
export const holds = (answer: string, quote: string | undefined): boolean => !!quote && locate(answer, quote) !== null;

/* ───────────────────────── the "Say" line ───────────────────────── */

export interface SayPiece {
  text: string;
  /** A dashed box: what only the person knows. */
  placeholder: boolean;
}

/** `[the tool you used]` is a placeholder; everything else is text. The brackets stay inside the box. */
export function sayPieces(text: string): SayPiece[] {
  const out: SayPiece[] = [];
  let last = 0;
  for (const m of text.matchAll(/\[[^[\]]{2,60}\]/g)) {
    if (m.index > last) out.push({ text: text.slice(last, m.index), placeholder: false });
    out.push({ text: m[0], placeholder: true });
    last = m.index + m[0].length;
  }
  if (last < text.length) out.push({ text: text.slice(last), placeholder: false });
  return out;
}

/* ───────────────────────── the transcript's addresses ───────────────────────── */

/**
 * An id for each turn, `t-<seconds>`: what a verdict's "At 2:31" links to. Two turns in the same second get `-2`, `-3`
 * after the first, so an id is never used twice on the page.
 */
export function turnIds(transcript: readonly Turn[]): string[] {
  const seen = new Map<number, number>();
  return transcript.map((t) => {
    const n = (seen.get(t.at) ?? 0) + 1;
    seen.set(t.at, n);
    return n === 1 ? `t-${t.at}` : `t-${t.at}-${n}`;
  });
}

/* ───────────────────────── one question's exchange ───────────────────────── */

/** The label of an interviewer line by the rung it was said on. */
export const RUNG_LABEL: Record<string, string> = {
  surface: 'Interviewer',
  push: 'Interviewer · follow-up',
  floor: 'Interviewer · an easier one',
};

export interface Exchange {
  /** Index into the transcript. */
  index: number;
  turn: Turn;
}

/** The turns that belong to one question, in order. */
export function turnsFor(debrief: Pick<Debrief, 'transcript'>, slotId: string): Exchange[] {
  return debrief.transcript.flatMap((turn, index) => (turn.slotId === slotId ? [{ index, turn }] : []));
}

/**
 * What a question card shows under "You said": the person's answers, with the interviewer's follow-up or easier question
 * between them. The first interviewer line of the question is the question itself, which the card already shows.
 */
export function exchangeFor(debrief: Pick<Debrief, 'transcript'>, result: Pick<SlotResult, 'slotId' | 'question'>): Exchange[] {
  const all = turnsFor(debrief, result.slotId);
  let skippedQuestion = false;
  return all.filter(({ turn }) => {
    if (turn.speaker === 'you') return true;
    if (!skippedQuestion && (turn.rung === undefined || turn.rung === 'surface')) {
      skippedQuestion = true;
      return false;
    }
    return true;
  });
}

/** The turn a quote was said in: the person's turn at that second, else the person's turn that holds it. */
export function turnOfQuote(exchange: readonly Exchange[], quote: Quote | undefined): Exchange | undefined {
  if (!quote) return undefined;
  const yours = exchange.filter((e) => e.turn.speaker === 'you');
  return yours.find((e) => e.turn.at === quote.at && holds(e.turn.text, quote.text)) ?? yours.find((e) => holds(e.turn.text, quote.text)) ?? yours.find((e) => e.turn.at === quote.at);
}

/** "Built from Resume · line 15 and your answer": source labels joined, the person's own answer in lower case. */
export function builtFrom(sources: readonly string[]): { label: string; cite: boolean }[] {
  const items = sources.map((s) => (s === 'Your answer' ? { label: 'your answer', cite: false } : { label: s, cite: true }));
  // the person's answer goes last, as the board writes it
  return [...items.filter((i) => i.cite), ...items.filter((i) => !i.cite)];
}
