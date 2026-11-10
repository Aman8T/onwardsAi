/**
 * The small derivations the resume screens share. Everything here is a pure function of the contract types: no
 * screen invents a word of the resume, a count or a score the data does not carry.
 *
 * A resume is shown as lines. Names, headings, entry titles, skills and education are fixed lines (the same on every
 * resume); a bullet is a block the person can press, move, leave out and reword.
 */
import { describeRef, shortDate } from '../match/model';
import type {
  BlockEntry,
  BlocksResponse,
  Bullet,
  BulletMark,
  ExperienceEntry,
  Fact,
  FactSource,
  Finding,
  FindingKind,
  MarkKind,
  PathSlot,
  ResumeBlock,
  ResumeDetail,
  ResumeList,
  ResumeSummary,
  ResumeVariant,
} from './types';

/* ───────────────────────── the resume as lines ───────────────────────── */

export type LineKind = 'name' | 'fixed' | 'head' | 'role' | 'bullet';

export interface SheetLine {
  key: string;
  /** 1-based, in reading order. */
  no: number;
  kind: LineKind;
  text: string;
  /** `kind: 'bullet'` only. */
  bullet?: Bullet;
  /** The job or project the bullet sits under, as its fixed line reads. */
  entryTitle?: string;
  marks: MarkKind[];
  /** A bullet can swap with the one above (or below) it inside its entry. */
  canUp: boolean;
  canDown: boolean;
}

export const MARK_LABEL: Record<MarkKind, string> = { moved: 'moved', edited: 'edited by you', added: 'added' };
const MARK_ORDER: readonly MarkKind[] = ['moved', 'edited', 'added'];

const entryTitle = (e: { title: string; org?: string; dates?: string }) => [e.title, e.org, e.dates].filter(Boolean).join(' · ');

/** The bullets a reader sees: a dropped one is not on the page. */
const shown = (bullets: readonly Bullet[]) => bullets.filter((b) => b.status !== 'dropped');

/** Every line of a draft, in the order the page reads: who, experience, projects, skills, education. */
export function sheetLines(variant: ResumeVariant, marks: readonly BulletMark[]): SheetLine[] {
  const byBullet = new Map<string, MarkKind[]>();
  for (const m of marks) byBullet.set(m.bulletId, [...(byBullet.get(m.bulletId) ?? []), m.kind]);

  const lines: Omit<SheetLine, 'no'>[] = [];
  const fixed = (key: string, kind: LineKind, text: string) => {
    if (text.trim()) lines.push({ key, kind, text, marks: [], canUp: false, canDown: false });
  };
  const entries = (prefix: string, list: readonly ExperienceEntry[]) => {
    for (const e of list) {
      const title = entryTitle(e);
      fixed(`${prefix}:${e.entryFactId}`, 'role', title);
      const bullets = shown(e.bullets);
      bullets.forEach((bullet, i) => {
        const own = byBullet.get(bullet.id) ?? [];
        lines.push({
          key: `b:${bullet.id}`,
          kind: 'bullet',
          text: bullet.text,
          bullet,
          entryTitle: title,
          marks: MARK_ORDER.filter((k) => own.includes(k)),
          canUp: i > 0,
          canDown: i < bullets.length - 1,
        });
      });
    }
  };

  const { header, summary, skills, experience, projects, education } = variant;
  fixed('name', 'name', header.name);
  fixed('headline', 'fixed', header.headline ?? '');
  fixed('contact', 'fixed', header.contact.join(' · '));
  if (summary.status !== 'dropped' && summary.text.trim()) {
    fixed('h:summary', 'head', 'Summary');
    fixed('summary', 'fixed', summary.text);
  }
  if (experience.length > 0) {
    fixed('h:experience', 'head', 'Experience');
    entries('e', experience);
  }
  if (projects.length > 0) {
    fixed('h:projects', 'head', 'Projects');
    entries('p', projects);
  }
  if (skills.some((g) => g.items.length > 0)) {
    fixed('h:skills', 'head', 'Skills');
    skills.forEach((g, i) => {
      const names = g.items.map((item) => item.name).join(', ');
      fixed(`s:${i}`, 'fixed', g.group ? `${g.group}: ${names}` : names);
    });
  }
  if (education.length > 0) {
    fixed('h:education', 'head', 'Education');
    for (const e of education) fixed(`ed:${e.factId}`, 'fixed', entryTitle(e));
  }
  return lines.map((line, i) => ({ ...line, no: i + 1 }));
}

/** The label at the right of a line: "moved · edited by you". */
export const markText = (marks: readonly MarkKind[]) => marks.map((k) => MARK_LABEL[k]).join(' · ');

/** The bullet that rests on a fact (the line a block became when it was put back). */
export function bulletOfFact(variant: ResumeVariant, factId: string): Bullet | undefined {
  for (const entry of [...variant.experience, ...variant.projects]) {
    const hit = shown(entry.bullets).find((b) => b.factIds.includes(factId));
    if (hit) return hit;
  }
  return undefined;
}

/* ───────────────────────── sources ───────────────────────── */

const SOURCE_LABEL: Record<FactSource, string> = {
  resume: 'your resume',
  github: 'GitHub',
  lovable: 'Lovable',
  link: 'a link of yours',
  user: 'your words',
};

export interface Source {
  /** "your resume · line 14", "GitHub · github.com/riyak", "your words". */
  label: string;
  /** The words of the source, verbatim. */
  quote: string;
  fromUser: boolean;
}

/** Where a fact comes from: its first source. Undefined for a fact the page was not given. */
export function sourceOf(fact: Fact | undefined): Source | undefined {
  const ref = fact?.refs[0];
  if (!ref) return undefined;
  const { where, quote, fromUser } = describeRef(ref);
  return { label: [SOURCE_LABEL[ref.source], where].filter(Boolean).join(' · '), quote, fromUser };
}

export const factIndex = (facts: readonly Fact[]): Map<string, Fact> => new Map(facts.map((f) => [f.id, f]));

/* ───────────────────────── the resume in view ───────────────────────── */

const count = (n: number, word: string) => `${n} ${n === 1 ? word : `${word}s`}`;

/** The resume the page opens on when the address names none: the default, else the upload, else the first. */
export function pickResumeId(list: ResumeList | null): string | undefined {
  const resumes = list?.resumes ?? [];
  return (resumes.find((r) => r.isDefault) ?? resumes.find((r) => r.kind === 'upload') ?? resumes[0])?.id;
}

export const slotOf = (list: ResumeList | null, resume: ResumeSummary): PathSlot | undefined =>
  resume.roleId ? list?.paths.find((p) => p.roleId === resume.roleId) : undefined;

/** The line above the title. */
export function kickerOf(resume: ResumeSummary, slot: PathSlot | undefined): string {
  const parts = [resume.kind === 'upload' ? 'Uploaded by you' : 'Built for a next role'];
  if (resume.kind === 'path' && slot) parts.push(`${Math.round(slot.alignment)}% aligned today`);
  if (resume.isDefault) parts.push('your default');
  return parts.join(' · ');
}

const against = (resume: ResumeSummary) => (resume.kind === 'upload' ? 'from the file as you wrote it' : 'from your uploaded resume');

/** "Draft · 5 changes from your uploaded resume" or "Finalised 6 Nov · as you wrote it": one line for the rail. */
export function statusOf(resume: ResumeSummary): string {
  const changes = resume.changes > 0 ? `${count(resume.changes, 'change')} ${against(resume)}` : resume.kind === 'upload' ? 'as you wrote it' : 'no changes yet';
  if (resume.state === 'draft') return `draft · ${changes}`;
  const when = resume.finalisedAt ? shortDate(resume.finalisedAt) : '';
  return `finalised${when ? ` ${when}` : ''} · ${changes}`;
}

/** The state line under the title: the state, the number of changes and what kind they are. */
export function stateLine(detail: ResumeDetail): string {
  const { resume, marks } = detail;
  const kinds = MARK_ORDER.map((k) => ({ k, n: marks.filter((m) => m.kind === k).length })).filter((x) => x.n > 0);
  const breakdown = kinds.map((x) => `${x.n} ${x.k}`).join(', ');
  const head = resume.state === 'draft' ? 'Draft' : `Finalised${resume.finalisedAt ? ` ${shortDate(resume.finalisedAt)}` : ''}`;
  const changes =
    resume.changes > 0
      ? `${count(resume.changes, 'change')} ${against(resume)}${breakdown ? ` (${breakdown})` : ''}`
      : resume.kind === 'upload'
        ? 'as you wrote it'
        : 'no changes from your uploaded resume';
  return `${head} · ${changes} · only you see this`;
}

/** A file name for a download, should the server not name it. */
export function fileNameOf(resume: ResumeSummary, ext: string): string {
  const base = resume.title
    .replace(/\.[A-Za-z0-9]{2,5}$/, '')
    .replace(/[^A-Za-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .toLowerCase();
  return `${base || 'resume'}.${ext}`;
}

/* ───────────────────────── the coach's starter questions ───────────────────────── */

/** Two or three fixed questions about the resume in view, for the existing coach. Templates, never model text. */
export function starterQuestions(resume: ResumeSummary | undefined): string[] {
  if (resume?.kind === 'path') {
    return [
      `What does a ${resume.title} posting usually ask for that my resume cannot show yet?`,
      `Which lines of my resume are strongest for ${resume.title}, and why?`,
      'Which lines on my resume state a result without a number?',
    ];
  }
  return [
    'Which lines on my resume state a result without a number?',
    'Which line on my resume is weakest, and what would make it hold up?',
    'What do my other sources show that my resume does not say?',
  ];
}

/* ───────────────────────── finalise ───────────────────────── */

const FINDING_WHAT: Record<FindingKind, string> = {
  number: 'You typed this figure. Nothing in your blocks has it.',
  scope: 'This says more about your part in the work than your blocks do.',
  term: 'You typed this term. Nothing in your blocks has it.',
  name: 'You typed this name. Nothing in your blocks has it.',
};

/** Why a typed line needs the person: what the check found, the words of the source, and what Leafout cannot know. */
export function findingNote(finding: Finding, source: Source | undefined): string {
  const quote = source?.quote.trim() ?? '';
  // the quote is shown as its source wrote it; the sentence only adds a full stop when the quote has none
  const said = quote ? ` Your source says "${quote}"${/[.!?]$/.test(quote) ? '' : '.'}` : '';
  return `${FINDING_WHAT[finding.kind]}${said} Leafout cannot know if it is true.`;
}

/** `text` around the passage the check could not support, so it can be marked; the text itself is never altered. */
export function splitFinding(finding: Finding): { before: string; what: string; after: string } {
  const at = finding.what ? finding.text.indexOf(finding.what) : -1;
  if (at < 0) return { before: finding.text, what: '', after: '' };
  return { before: finding.text.slice(0, at), what: finding.what, after: finding.text.slice(at + finding.what.length) };
}

/* ───────────────────────── the blocks ───────────────────────── */

export interface BlockGroup {
  key: string;
  title: string;
  /** The small line beside the title. */
  note: string;
  blocks: ResumeBlock[];
}

const isUserBlock = (b: ResumeBlock) => b.fact.refs.some((r) => r.source === 'user');

/**
 * The blocks grouped the way a resume is: one group per job, project and degree, in the server's order, then what the
 * person said in their own words, then what sits under no entry (skills, what the other sources show). The fact of an
 * entry itself is the group's heading, not a row under it.
 */
export function groupBlocks(data: BlocksResponse): BlockGroup[] {
  const entryIds = new Set(data.entries.map((e) => e.factId));
  const rows = data.blocks.filter((b) => !entryIds.has(b.fact.id));
  const under = (e: BlockEntry) => rows.filter((b) => b.entryFactId === e.factId);
  const loose = rows.filter((b) => !b.entryFactId || !entryIds.has(b.entryFactId));

  const groups: BlockGroup[] = data.entries.map((e) => ({
    key: e.factId,
    title: entryTitle(e),
    note: e.kind === 'education' ? 'the same on every resume' : 'title, employer and dates are the same on every resume',
    blocks: under(e),
  }));
  const own = loose.filter(isUserBlock);
  const rest = loose.filter((b) => !isUserBlock(b));
  if (own.length > 0) groups.push({ key: 'own', title: 'In your own words', note: 'the only way a fact enters without a source', blocks: own });
  if (rest.length > 0) groups.push({ key: 'rest', title: 'Skills, and what your other sources show', note: 'under no job, project or degree', blocks: rest });
  return groups.filter((g) => g.blocks.length > 0 || entryIds.has(g.key));
}

/** "17 from your resume · 1 from GitHub · 1 in your own words": where the blocks come from, counted from their first source. */
export function sourceCounts(data: BlocksResponse): string {
  const order: FactSource[] = ['resume', 'github', 'lovable', 'link', 'user'];
  const words: Record<FactSource, string> = {
    resume: 'from your resume',
    github: 'from GitHub',
    lovable: 'from Lovable',
    link: 'from your links',
    user: 'in your own words',
  };
  const counts = new Map<FactSource, number>();
  for (const b of data.blocks) {
    const source = b.fact.refs[0]?.source;
    if (source) counts.set(source, (counts.get(source) ?? 0) + 1);
  }
  return order
    .filter((s) => counts.has(s))
    .map((s) => `${counts.get(s)} ${words[s]}`)
    .join(' · ');
}

export { count as countOf, shortDate };
