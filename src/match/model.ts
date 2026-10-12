/**
 * Labels, ordering and the small derivations the match screens share. Everything here is a pure function of the
 * contract types: no screen invents a word, a count or a score the data does not carry. There are no percentages
 * anywhere on purpose (project-soul: bands, not decimals).
 */
import type {
  Bullet,
  Change,
  Confidence,
  Coverage,
  CoverageStatus,
  Decision,
  DecisionChoiceKey,
  FactRef,
  FactSource,
  GateVerdict,
  MatchBand,
  MatchResult,
  Posting,
  Requirement,
  RequirementTier,
  ResumeVariant,
  StatusCounts,
} from '../../shared/match';
import type { MatchFact } from './types';

/* ───────────────────────── labels ───────────────────────── */

export const STATUS_LABEL: Record<CoverageStatus, string> = {
  covered: 'Covered',
  partial: 'Partial',
  synonym_only: 'Synonym only',
  missing_have_it: "You have it, resume doesn't say",
  missing_gap: 'Gap',
  unknown: "Can't tell",
};

/** The order status chips are listed in: strongest evidence first. */
export const STATUS_ORDER: readonly CoverageStatus[] = [
  'covered',
  'partial',
  'synonym_only',
  'missing_have_it',
  'missing_gap',
  'unknown',
];

export const GATE_LABEL: Record<GateVerdict, string> = { pass: 'Pass', fail: 'Fail', uncertain: "Can't tell" };

export const TIER_LABEL: Record<RequirementTier, string> = {
  must: 'Must',
  required: 'Required',
  preferred: 'Preferred',
  nice: 'Nice to have',
};

export const TIER_ORDER: readonly RequirementTier[] = ['must', 'required', 'preferred', 'nice'];

export const BAND_LABEL: Record<MatchBand, string> = {
  strong: 'Strong match',
  good: 'Good match',
  stretch: 'Stretch',
  weak: 'Weak match',
};

export const CONFIDENCE_LABEL: Record<Confidence, string> = { high: 'High', medium: 'Medium', low: 'Low' };

export const WORK_MODE_LABEL = { onsite: 'On site', hybrid: 'Hybrid', remote: 'Remote', unspecified: '' } as const;

const SOURCE_LABEL: Record<FactSource, string> = {
  resume: 'resume',
  github: 'GitHub',
  lovable: 'Lovable',
  link: 'link',
  user: 'you said',
};

export const CHANGE_LABEL: Record<Change['kind'], string> = {
  surfaced: 'Surfaced',
  reordered: 'Reordered',
  reworded: 'Reworded',
  left_out: 'Left out',
};

export const DECISION_LABEL: Record<DecisionChoiceKey, string> = {
  keep: 'Keep',
  soften: 'Soften',
  drop: 'Drop',
  fill: 'Use this figure',
  use_posting_term: "Yes, use the posting's term",
  keep_my_wording: 'Keep my wording',
};

export const DECISION_CHIP: Record<Decision['kind'], string> = {
  stretch: 'Stretch',
  number: 'Needs a number',
  confirm_term: 'Confirm',
};

/* ───────────────────────── rows ───────────────────────── */

/** Needs-attention order inside a tier: nothing behind it first, fully covered last. */
const UNMET_RANK: Record<CoverageStatus, number> = {
  missing_gap: 0,
  unknown: 1,
  partial: 2,
  synonym_only: 3,
  missing_have_it: 4,
  covered: 5,
};

export interface Row {
  requirement: Requirement;
  /** Absent while the match is still streaming. */
  coverage?: Coverage;
}

/** Every requirement with its coverage row, ordered by how strongly the posting asks, then unmet first. */
export function sortRows(posting: Posting, coverage: readonly Coverage[]): Row[] {
  const byReq = new Map(coverage.map((c) => [c.requirementId, c]));
  const rows = posting.requirements.map((requirement, i) => ({ requirement, coverage: byReq.get(requirement.id), i }));
  const tierRank = (t: RequirementTier) => TIER_ORDER.indexOf(t);
  rows.sort((a, b) => {
    const t = tierRank(a.requirement.tier) - tierRank(b.requirement.tier);
    if (t !== 0) return t;
    const ra = a.coverage ? UNMET_RANK[a.coverage.status] : 6;
    const rb = b.coverage ? UNMET_RANK[b.coverage.status] : 6;
    return ra - rb || a.i - b.i;
  });
  return rows.map(({ requirement, coverage: c }) => ({ requirement, coverage: c }));
}

/** The status chips worth showing: only statuses that occur. */
export function countChips(counts: StatusCounts): { status: CoverageStatus; n: number }[] {
  return STATUS_ORDER.map((status) => ({ status, n: counts[status] })).filter((c) => c.n > 0);
}

const WORDS = ['No', 'One', 'Two', 'Three', 'Four', 'Five', 'Six', 'Seven', 'Eight', 'Nine', 'Ten'];
const word = (n: number) => WORDS[n] ?? String(n);

/** A sentence about the band, written by code from the rows, never by a model. */
export function bandNote(result: MatchResult, posting: Posting): string {
  const byReq = new Map(result.coverage.map((c) => [c.requirementId, c.status]));
  const musts = posting.requirements.filter((r) => r.tier === 'must');
  const parts: string[] = [];
  if (result.blocked) parts.push("A hard requirement isn't met.");
  if (musts.length > 0) {
    const statuses = musts.map((r) => byReq.get(r.id) ?? 'unknown');
    const noEvidence = statuses.filter((s) => s === 'missing_gap' || s === 'unknown').length;
    if (statuses.every((s) => s === 'covered' || s === 'partial')) parts.push('Every must-have is covered or partial.');
    else if (noEvidence === 0) parts.push('Every must-have has evidence behind it.');
    else parts.push(`${word(noEvidence)} of ${musts.length} must-haves ${noEvidence === 1 ? 'has' : 'have'} no evidence yet.`);
  }
  const mustIds = new Set(musts.map((r) => r.id));
  const otherGaps = result.coverage.filter((c) => c.status === 'missing_gap' && !mustIds.has(c.requirementId)).length;
  if (otherGaps > 0) parts.push(otherGaps === 1 ? 'One other item is a gap.' : `${word(otherGaps)} other items are gaps.`);
  return parts.join(' ');
}

/** How many rows changed status between two results (the re-check announcement). */
export function changedRows(prev: readonly Coverage[], next: readonly Coverage[]): number {
  const before = new Map(prev.map((c) => [c.requirementId, c.status]));
  return next.filter((c) => before.get(c.requirementId) !== c.status).length;
}

/* ───────────────────────── citations ───────────────────────── */

export interface Cite {
  factId: string;
  /** "resume", "GitHub", "you said"… */
  source: string;
  /** "line 16", "lines 18–20", or ''. */
  where: string;
  /** The words of the source, verbatim. */
  quote: string;
  fromUser: boolean;
}

export function factIndex(facts: readonly MatchFact[]): Map<string, MatchFact> {
  return new Map(facts.map((f) => [f.id, f]));
}

export function formatLines(lines?: number[]): string {
  if (!lines || lines.length === 0) return '';
  const [first, last = first] = lines;
  return first === last ? `line ${first}` : `lines ${first}–${last}`;
}

/** A document id worth showing: a link or host, not the internal `link-1`. */
const looksLikeAddress = (docId: string) => /^[^\s/]+\.[^\s/]+(\/\S*)?$/.test(docId) || /^https?:\/\//.test(docId);

export function describeRef(ref: FactRef): Omit<Cite, 'factId'> {
  const lines = formatLines(ref.lines);
  const doc = ref.source !== 'resume' && ref.source !== 'user' && looksLikeAddress(ref.docId) ? ref.docId.replace(/^https?:\/\//, '') : '';
  return {
    source: SOURCE_LABEL[ref.source],
    where: [doc, lines].filter(Boolean).join(' · '),
    quote: ref.quote,
    fromUser: ref.source === 'user',
  };
}

/** The citation of a fact id: its first source. A fact the book does not hold is shown by its id alone. */
export function citeOf(factId: string, byId: ReadonlyMap<string, MatchFact>): Cite {
  const ref = byId.get(factId)?.refs[0];
  if (!ref) return { factId, source: '', where: '', quote: '', fromUser: false };
  return { factId, ...describeRef(ref) };
}

/** True when any source of the fact is something the person said (a "from you" fact). */
export function isUserFact(factId: string, byId: ReadonlyMap<string, MatchFact>): boolean {
  return byId.get(factId)?.refs.some((r) => r.source === 'user') ?? false;
}

/* ───────────────────────── dates and hosts ───────────────────────── */

export function shortDate(iso: string): string {
  const t = Date.parse(iso);
  return Number.isFinite(t) ? new Date(t).toLocaleDateString('en-GB', { day: 'numeric', month: 'short' }) : '';
}

/** The host of a URL a person typed, or null when it is not an http(s) address. */
export function hostOf(value: string): string | null {
  try {
    const url = new URL(value.trim());
    return url.protocol === 'http:' || url.protocol === 'https:' ? url.hostname.replace(/^www\./, '') : null;
  } catch {
    return null;
  }
}

/** Hosts that forbid automated reading; the paste screen says so before anything is sent. */
export const BLOCKED_HOSTS = ['linkedin.com', 'naukri.com'];
export const isBlockedHost = (host: string) => BLOCKED_HOSTS.some((h) => host === h || host.endsWith(`.${h}`));

/* ───────────────────────── text ───────────────────────── */

/** Splits `text` around its `[x]` placeholders so they can be marked up; the text itself is never altered. */
export function splitPlaceholders(text: string, placeholders: readonly string[]): { text: string; ph: boolean }[] {
  const holes = placeholders.filter((p) => p && text.includes(p));
  if (holes.length === 0) return [{ text, ph: false }];
  const escaped = holes.map((p) => p.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
  const parts = text.split(new RegExp(`(${escaped.join('|')})`));
  return parts.filter((p) => p !== '').map((p) => ({ text: p, ph: holes.includes(p) }));
}

export const MAX_POSTING_CHARS = 20_000;

/* ───────────────────────── resume ───────────────────────── */

/** Every bullet of a variant in reading order: the summary, then experience, then projects. */
export function allBullets(v: ResumeVariant): Bullet[] {
  return [v.summary, ...v.experience.flatMap((e) => e.bullets), ...v.projects.flatMap((e) => e.bullets)];
}

/** The bullets a reader sees: dropped ones are gone from the preview (and listed under "left out"). */
export const shownBullets = (bullets: readonly Bullet[]) => bullets.filter((b) => b.status !== 'dropped');

/** The fact ids behind a skill group: the union of its items', in first-seen order. */
export function groupFactIds(group: { items: { factIds: string[] }[] }): string[] {
  return [...new Set(group.items.flatMap((i) => i.factIds))];
}

/* ───────────────────────── which profile ───────────────────────── */

/** Sample runs belong to nobody, so they can never be matched (mirrors `isSampleProfile` in auth/useMe). */
export const isSampleId = (id: string) => id.startsWith('sample-');

/**
 * The profile a posting is matched against: the one on screen once the account owns it; with none on screen (a
 * second device), the account's first. `undefined` means "not yet" (the on-screen profile is still being claimed) or
 * "there is none", which the screens tell apart with `shownId`.
 */
export function pickProfileId(shownId: string | undefined, owned: readonly { id: string }[]): string | undefined {
  if (shownId && !isSampleId(shownId)) return owned.some((p) => p.id === shownId) ? shownId : undefined;
  return owned[0]?.id;
}
