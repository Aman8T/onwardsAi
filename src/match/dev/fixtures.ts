/**
 * The two personas' stored matches (copies of backend/tests/data/match/{meera,riya}.match.json, produced by the
 * M1 service with scripted models) and the small synthetic variations the gallery and the parity script need:
 * decisions waiting on the resume, and a fact the person said themselves. The fixtures hold neither, and both
 * screens must be seen in these states, so they are built here, from the fixture, and labelled as synthetic.
 */
import type { Bullet, CoverNote, Decision, ResumeVariant } from '../../../shared/match';
import type { Coverage, MatchResult, Posting } from '../../../shared/match';
import type { MatchFact } from '../types';
import meeraJson from './meera.match.json';
import riyaJson from './riya.match.json';

export interface Fixture {
  posting: Posting;
  facts: MatchFact[];
  result: MatchResult;
  variant: ResumeVariant;
  cover: CoverNote;
}

export type PersonaId = 'meera' | 'riya';

export const FIXTURES: Record<PersonaId, Fixture> = {
  meera: meeraJson as unknown as Fixture,
  riya: riyaJson as unknown as Fixture,
};

export const PERSONA_NAME: Record<PersonaId, string> = { meera: 'Meera Nair · Marketing', riya: 'Riya Kapoor · Frontend' };

const clone = <T,>(v: T): T => JSON.parse(JSON.stringify(v)) as T;

/**
 * A copy of the variant with three lines waiting on the person: a stretch (keep / soften / drop), a missing figure
 * (an input) and a term to confirm. Only the first experience's first three bullets are touched.
 */
export function withDecisions(variant: ResumeVariant): ResumeVariant {
  const v = clone(variant);
  const bullets: Bullet[] = v.experience[0]?.bullets ?? [];
  const decisions: Decision[] = [];
  const [a, b, c] = bullets;
  if (a) {
    a.verdict = 'stretch';
    a.status = 'needs_user';
    decisions.push({
      bulletId: a.id,
      kind: 'stretch',
      prompt: 'Your sources show part of this. The line says a little more than they do. Soften it, keep it as written, or drop it?',
      options: ['keep', 'soften', 'drop'],
    });
  }
  if (b) {
    b.text = 'Grew paid acquisition [x]% year on year.';
    b.placeholders = ['[x]%'];
    b.status = 'needs_user';
    decisions.push({
      bulletId: b.id,
      kind: 'number',
      prompt: 'Your resume says acquisition grew but gives no figure. We never invent one. Type it, or drop the line.',
      options: ['fill', 'soften', 'drop'],
    });
  }
  if (c) {
    c.status = 'needs_user';
    decisions.push({
      bulletId: c.id,
      kind: 'confirm_term',
      prompt: 'The posting looks for one term and your resume uses another. Only you know which it was.',
      options: ['use_posting_term', 'keep_my_wording', 'drop'],
    });
  }
  v.decisions = decisions;
  return v;
}

/** Applies one choice to a variant the way the server would: settle the line, or remove it. */
export function applyChoice(variant: ResumeVariant, bulletId: string, choice: string, value?: string): ResumeVariant {
  const v = clone(variant);
  for (const entry of [...v.experience, ...v.projects]) {
    for (const bullet of entry.bullets) {
      if (bullet.id !== bulletId) continue;
      if (choice === 'drop') bullet.status = 'dropped';
      else {
        bullet.status = 'verified';
        if (choice === 'fill' && value) {
          bullet.text = bullet.text.replace(bullet.placeholders[0] ?? '[x]', value.endsWith('%') ? value : `${value}%`);
          bullet.placeholders = [];
        }
      }
    }
  }
  v.decisions = v.decisions.filter((d) => d.bulletId !== bulletId);
  return v;
}

/** The fixture with the person's answer to a question added as a fact "from you", and a paragraph resting on it. */
export function withAnswer(fx: Fixture): Fixture {
  const fact: MatchFact = {
    id: 'user-1',
    text: 'Moving to the posting city in December.',
    kind: 'other',
    refs: [{ source: 'user', docId: 'answer-1', quote: 'Moving there in December, so three days in the office works.' }],
  };
  const cover = clone(fx.cover);
  const close = cover.paragraphs.findIndex((p) => p.kind === 'close');
  const note = { text: "I'm moving to the city in December, so working from the office three days a week works for me.", requirementIds: [], factIds: ['user-1'], kind: 'logistics' as const };
  cover.paragraphs.splice(close < 0 ? cover.paragraphs.length : close, 0, note);
  cover.wordCount += note.text.split(/\s+/).length;
  return { ...fx, facts: [...fx.facts, fact], cover };
}

/** The first `n` coverage rows, as if the stream had stopped there. */
export const streamedRows = (fx: Fixture, n: number): Coverage[] => fx.result.coverage.slice(0, n);
