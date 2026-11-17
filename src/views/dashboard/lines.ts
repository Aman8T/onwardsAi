/**
 * The line under the greeting: one at a time, changing every few seconds, each from a theme.
 *
 *   Achievement      what the person did, counted (the shelf's achievements) or earned (the read's proud lines)
 *   Roast            two or three jabs at what the resume and the sources fail to show: the read's own, then ones
 *                    phrased here from the records (a skill the resume never mentions, the top role's widest gap, a
 *                    claim that did not hold up out loud)
 *   Next move        the read's motivational lines
 *   Did you know?    facts counted from the graph (cited lines, the most-proved skill, the closest role), the read's
 *                    quirky lines, and what is true of the product
 *   Tip of the day   one a day from TIPS
 *   Quote            two a day from QUOTES, each with who said it
 *
 * QUOTES and TIPS are the only hand-written text; edit them here. Everything about the person is either a stored line
 * of the read or counted from stored data. No statistics about other people, no number that is not in the graph.
 * No DOM, no network.
 */
import type { PracticeHistory } from '../../../shared/interview';
import type { Profile, ReadTone } from '../../../shared/types';
import { rankRoles } from '../../lib/layout';
import type { Achievement } from './model';
import { notOnResume, readLines } from './model';

export type LineTheme = 'win' | 'roast' | 'move' | 'know' | 'tip' | 'quote';

export const THEME_LABEL: Record<LineTheme, string> = {
  win: 'Achievement',
  roast: 'Roast',
  move: 'Next move',
  know: 'Did you know?',
  tip: 'Tip of the day',
  quote: 'Quote',
};

/** Where a line's words came from: the read (a model, at analysis time), a count over the records, or this file. */
export type LineOrigin = 'read' | 'counted' | 'written';

export interface DeskLine {
  id: string;
  theme: LineTheme;
  origin: LineOrigin;
  text: string;
  /** Small print after the line: who said a quote, the detail of an achievement. */
  note?: string;
  /** The skill whose evidence opens under the line. */
  skillId?: string;
}

export interface LinesInput {
  profile: Profile;
  /** The shelf's achievements, newest and freshest first. */
  shelf: readonly Achievement[];
  history: PracticeHistory | null;
  /** The local day as a whole number; the tip and the quotes of the day follow it. */
  day: number;
}

/* ───────────────────────── the hand-written lines ───────────────────────── */

/** Each one checked against its source; add only quotes you can place. */
export const QUOTES: ReadonlyArray<{ text: string; by: string }> = [
  { text: 'The best way to predict the future is to invent it.', by: 'Alan Kay' },
  { text: 'Talk is cheap. Show me the code.', by: 'Linus Torvalds' },
  { text: 'What I cannot create, I do not understand.', by: 'Richard Feynman' },
  { text: 'Well done is better than well said.', by: 'Benjamin Franklin' },
  { text: 'Simplicity is prerequisite for reliability.', by: 'Edsger W. Dijkstra' },
  { text: 'In the fields of observation, chance favours only the prepared mind.', by: 'Louis Pasteur' },
  { text: 'We are what we repeatedly do.', by: 'Will Durant' },
  { text: 'I have no special talents. I am only passionately curious.', by: 'Albert Einstein' },
  { text: 'Arise, awake, and stop not till the goal is reached.', by: 'Swami Vivekananda' },
  { text: 'Luck is the residue of design.', by: 'Branch Rickey' },
];

export const TIPS: readonly string[] = [
  'Lead each bullet with the result, then say how you got it.',
  'A number without a baseline is a guess. Give both: from what, to what.',
  'Say your claims out loud once before an interview. The shaky ones show up fast.',
  'Tailor the top third of your resume. Most readers stop there.',
  'Link the proof. A repo, a live page or a write-up beats an adjective.',
  'Write down a win the week it happens. You will not remember the numbers later.',
  'Ask a referrer one specific question. It is easier to answer than "any openings?"',
  "Read a posting's first three requirements twice. They are usually the real job.",
  'Cut any line you could not talk about for two minutes.',
  'One role, one resume. Reorder what you have before you rewrite it.',
];

/* ───────────────────────── the lines about the person ───────────────────────── */

const TONE_THEME: Partial<Record<ReadTone, LineTheme>> = {
  proud: 'win',
  roast: 'roast',
  sarcastic: 'roast',
  motivational: 'move',
  quirky: 'know',
  // "plain" (one sentence of who they are) is not a line worth rotating
};

const WINS_MAX = 4;
const ROASTS_MAX = 3;
const KNOWS_MAX = 3;
const MOVES_MAX = 2;
const QUOTES_A_DAY = 2;
const NOTE_MAX = 90;
const CLAIM_MAX = 80;
const PROVED_MIN = 3;

const clip = (text: string, max: number): string => {
  const t = text.trim();
  return t.length > max ? `${t.slice(0, max - 1).trimEnd()}…` : t;
};

const stop = (text: string): string => (/[.!?…]$/.test(text.trim()) ? text.trim() : `${text.trim()}.`);

/** Roasts phrased here, each from a stored fact: used when the read gave fewer than three. */
function countedRoasts(profile: Profile, history: PracticeHistory | null): DeskLine[] {
  const out: DeskLine[] = [];
  const missing = notOnResume(profile);
  const first = missing?.items[0];
  if (first) {
    out.push({
      id: `roast:resume:${first.skill.id}`,
      theme: 'roast',
      origin: 'counted',
      text: `Your resume never mentions ${first.skill.name}. ${first.source.label} had to say it for you.`,
      skillId: first.skill.id,
    });
  }
  const top = rankRoles(profile)[0];
  const gap = top?.requirements.filter((q) => !q.have && q.target > q.current).sort((a, b) => b.target - b.current - (a.target - a.current))[0];
  if (top && gap) {
    out.push({
      id: `roast:gap:${top.id}`,
      theme: 'roast',
      origin: 'counted',
      text:
        gap.current > 0
          ? `${top.title} asks for ${gap.name} at ${gap.target}. Your evidence says ${gap.current}.`
          : `${top.title} asks for ${gap.name}. Your evidence for it so far: none.`,
    });
  }
  const dropped = history?.claims.find((c) => c.best === 'couldnt');
  if (dropped) {
    out.push({
      id: 'roast:claim',
      theme: 'roast',
      origin: 'counted',
      text: `You wrote “${clip(dropped.text, CLAIM_MAX)}” and could not defend it out loud.`,
    });
  }
  const read = profile.sources.filter((s) => s.ok);
  if (read.length === 1 && read[0].kind === 'resume') {
    out.push({ id: 'roast:one-source', theme: 'roast', origin: 'counted', text: 'One source, and it is the one you wrote yourself.' });
  }
  return out;
}

/** Facts counted from the graph, and what is true of the product. */
function countedKnows(profile: Profile): DeskLine[] {
  const out: DeskLine[] = [];
  const cited = profile.skills.reduce((n, s) => n + s.evidence.length, 0);
  const sources = profile.sources.filter((s) => s.ok).length;
  const proved = profile.skills.slice().sort((a, b) => b.evidence.length - a.evidence.length || b.score - a.score)[0];
  if (proved && proved.evidence.length >= PROVED_MIN) {
    out.push({
      id: `know:proved:${proved.id}`,
      theme: 'know',
      origin: 'counted',
      text: `${proved.name} has the most proof behind it: ${proved.evidence.length} cited lines.`,
      skillId: proved.id,
    });
  }
  const top = rankRoles(profile)[0];
  if (top) {
    out.push({ id: `know:top:${top.id}`, theme: 'know', origin: 'counted', text: `${top.title} is your closest next role: ${top.alignment}% aligned, ${top.eta} to close.` });
  }
  if (cited > 0 && sources > 0) {
    out.push({
      id: 'know:cited',
      theme: 'know',
      origin: 'counted',
      text: `Your graph rests on ${cited} cited ${cited === 1 ? 'line' : 'lines'} from ${sources} ${sources === 1 ? 'source' : 'sources'}.`,
    });
  }
  out.push({ id: 'know:product', theme: 'know', origin: 'written', text: 'Every score on this page cites a line from what you gave Leafout. Nothing else counts.' });
  return out;
}

/**
 * The lines of today, the themes taking turns so two of a kind never follow each other while another kind is left.
 * Never empty: the quotes, the tip and the product's own fact need no data.
 */
export function deskLines(input: LinesInput): DeskLine[] {
  const { profile, shelf, history, day } = input;
  const skills = new Set(profile.skills.map((s) => s.id));
  const byTheme: Record<LineTheme, DeskLine[]> = { win: [], roast: [], move: [], know: [], tip: [], quote: [] };

  // what was done comes before what the graph says
  for (const a of shelf) {
    const skillId = a.id.startsWith('skill:') ? a.id.slice('skill:'.length) : undefined;
    byTheme.win.push({
      id: `win:${a.id}`,
      theme: 'win',
      origin: 'counted',
      text: stop(a.title),
      // a skill's detail is its evidence, and that opens under the line
      ...(skillId && skills.has(skillId) ? { skillId } : { note: clip(a.detail, NOTE_MAX) }),
    });
  }

  let proud = 0;
  readLines(profile).forEach((line, i) => {
    const theme = TONE_THEME[line.tone];
    if (!theme) return;
    const at = theme === 'win' ? proud++ : byTheme[theme].length;
    // the read's proud lines lead the achievements: they are the person's, in a sentence
    byTheme[theme].splice(at, 0, {
      id: `read:${i}`,
      theme,
      origin: 'read',
      text: line.text.trim(),
      ...(line.skillId && skills.has(line.skillId) ? { skillId: line.skillId } : {}),
    });
  });

  byTheme.roast.push(...countedRoasts(profile, history));
  byTheme.know.push(...countedKnows(profile));

  const n = Math.max(0, Math.floor(day));
  byTheme.tip.push({ id: `tip:${n % TIPS.length}`, theme: 'tip', origin: 'written', text: TIPS[n % TIPS.length] });
  for (let i = 0; i < Math.min(QUOTES_A_DAY, QUOTES.length); i += 1) {
    const q = QUOTES[(n * QUOTES_A_DAY + i) % QUOTES.length];
    byTheme.quote.push({ id: `quote:${q.by}:${i}`, theme: 'quote', origin: 'written', text: `“${q.text}”`, note: q.by });
  }

  const rows: DeskLine[][] = [
    byTheme.win.slice(0, WINS_MAX),
    byTheme.quote,
    byTheme.roast.slice(0, ROASTS_MAX),
    byTheme.know.slice(0, KNOWS_MAX),
    byTheme.tip,
    byTheme.move.slice(0, MOVES_MAX),
  ];
  const out: DeskLine[] = [];
  for (let turn = 0; rows.some((r) => r.length > turn); turn += 1) {
    for (const row of rows) if (row[turn]) out.push(row[turn]);
  }
  return out;
}

/** The local day as a whole number: the same all day, one more tomorrow. */
export function localDay(now: number): number {
  return Math.floor((now - new Date(now).getTimezoneOffset() * 60_000) / (24 * 3600 * 1000));
}
