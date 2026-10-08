/**
 * Pure selectors over a portfolio (no React, no CSS): every block, graph variant, the press skin and the
 * editor read the data through these, so the standard and newspaper views can never disagree.
 *
 * Graph numbers are passed through untouched (integers from the snapshot); captions are templates over
 * those values, never model text.
 */
import { FAMILY_HUES, SLOT_LIMITS } from '../../shared/portfolio';
import type {
  BlockKind,
  BlockSpec,
  CopySlot,
  Fact,
  FactKind,
  GraphDomain,
  GraphSnapshot,
  GraphVariant,
  PortfolioContent,
  PortfolioSpec,
} from '../../shared/portfolio';

/** Who is looking: the owner in the editor (sees hidden blocks as such) or a viewer (public page). */
export type ViewMode = 'owner' | 'public';

/* ───────────────────────── content ───────────────────────── */

export function slot(content: PortfolioContent, id: string): CopySlot | undefined {
  return content.slots.find((s) => s.id === id);
}

/** The slot's text, or '' when the slot does not exist (blocks then fall back to fact wording). */
export function slotText(content: PortfolioContent, id: string): string {
  return slot(content, id)?.text ?? '';
}

/** Character limit for a slot id (`exp:<id>` and `proj:<id>` use their prefix), or undefined. */
export function slotLimit(id: string): number | undefined {
  const fixed = (SLOT_LIMITS as Record<string, number>)[id];
  if (fixed !== undefined && !id.includes(':') && id !== 'exp' && id !== 'proj') return fixed;
  const [prefix, rest] = id.split(':', 2);
  if (rest && (prefix === 'exp' || prefix === 'proj')) return SLOT_LIMITS[prefix];
  return undefined;
}

export function factsOf(content: PortfolioContent, kind: FactKind): Fact[] {
  return content.facts.filter((f) => f.kind === kind);
}

export function factById(content: PortfolioContent, id: string): Fact | undefined {
  return content.facts.find((f) => f.id === id);
}

/** Bullet lines for an experience entry: the `exp:<id>` slot split on newlines, else the fact's own highlights (≤ 3). */
export function experienceLines(content: PortfolioContent, f: Fact): string[] {
  const text = slotText(content, `exp:${f.id}`).trim();
  if (text) return text.split('\n').map((l) => l.replace(/^[-•]\s*/, '').trim()).filter(Boolean).slice(0, 3);
  return f.highlights.slice(0, 3);
}

/** A project's blurb: the `proj:<id>` slot, else the fact's detail, else its first highlight. */
export function projectBlurb(content: PortfolioContent, f: Fact): string {
  return slotText(content, `proj:${f.id}`).trim() || f.detail || f.highlights[0] || '';
}

/** The person's own words for a pull quote (a `quote` fact), if the sources had one. */
export function ownWords(content: PortfolioContent): Fact | undefined {
  return factsOf(content, 'quote')[0];
}

/* ───────────────────────── blocks ───────────────────────── */

/** Blocks to render, in order. Viewers see visible + public blocks; the owner sees visible ones. */
export function shownBlocks(spec: PortfolioSpec, mode: ViewMode): BlockSpec[] {
  return spec.blocks.filter((b) => b.visible && (mode === 'owner' || b.public));
}

export function blockOf(spec: PortfolioSpec, kind: BlockKind): BlockSpec | undefined {
  return spec.blocks.find((b) => b.kind === kind);
}

/** The graph variant in use (the graph block's variant), defaulting to `peaks`. */
export function graphVariant(spec: PortfolioSpec): GraphVariant {
  return (blockOf(spec, 'graph')?.variant as GraphVariant | undefined) ?? 'peaks';
}

/** True when the hero is `split` and the graph block directly follows it: they render as one band. */
export function graphInHero(blocks: BlockSpec[]): boolean {
  return blocks[0]?.kind === 'hero' && blocks[0].variant === 'split' && blocks[1]?.kind === 'graph';
}

/* ───────────────────────── graph ───────────────────────── */

/** Domains shown on the page: the snapshot's fixed order minus the owner's hidden ones. */
export function shownDomains(graph: GraphSnapshot, spec: PortfolioSpec): GraphDomain[] {
  const hidden = new Set(spec.graph.hiddenDomainIds);
  return graph.domains.filter((d) => !hidden.has(d.id));
}

/** The `n` strongest domains (ties keep the fixed order); used for emphasis, never for reordering axes. */
export function peaks(domains: GraphDomain[], n: number): GraphDomain[] {
  return domains
    .map((d, i) => ({ d, i }))
    .sort((a, b) => b.d.score - a.d.score || a.i - b.i)
    .slice(0, n)
    .map((x) => x.d);
}

export function median(values: number[]): number {
  if (values.length === 0) return 0;
  const s = [...values].sort((a, b) => a - b);
  const mid = Math.floor(s.length / 2);
  return s.length % 2 ? s[mid] : (s[mid - 1] + s[mid]) / 2;
}

/** Natural-language list: "A", "A and B", "A, B and C". */
export function andList(items: string[]): string {
  if (items.length <= 1) return items[0] ?? '';
  return `${items.slice(0, -1).join(', ')} and ${items[items.length - 1]}`;
}

/** The graph's one-line caption: a template over the shown values (never model text). */
export function graphCaption(domains: GraphDomain[], variant: GraphVariant): string {
  if (domains.length === 0) return '';
  const names = (ds: GraphDomain[]) => ds.map((d) => d.name);
  if (variant === 'focus') return `Areas of focus: ${andList(names(domains))}.`;
  const [first, second] = peaks(domains, 2);
  if (variant === 'spotlight' || !second) return `Strongest in ${first.name}.`;
  const med = median(domains.map((d) => d.score));
  if (first.score - med < 15) {
    const broad = domains.filter((d) => d.score >= 55).length;
    if (broad >= 3) return `Broad: ${broad} areas at 55 or above.`;
  }
  return `Strongest in ${first.name} and ${second.name}.`;
}

/** For the spotlight variant: the next areas after the peak, framed as "Growing" (domains only). */
export function growing(domains: GraphDomain[], n = 2): GraphDomain[] {
  return peaks(domains, n + 1).slice(1);
}

/** "resume, GitHub, Lovable and portfolio" for the provenance line. */
export function sourcesLine(graph: GraphSnapshot): string {
  return andList(graph.sources.map((s) => (s === 'GitHub' || s === 'Lovable' ? s : s.toLowerCase())));
}

/** "8 Oct 2026" from the snapshot's ISO date. */
export function asOfLabel(graph: GraphSnapshot): string {
  const d = new Date(`${graph.asOf.slice(0, 10)}T00:00:00Z`);
  if (Number.isNaN(d.getTime())) return graph.asOf;
  return `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]} ${d.getUTCFullYear()}`;
}

/** "Thursday, 8 October 2026" for the newspaper folio line. */
export function longDateLabel(graph: GraphSnapshot): string {
  const d = new Date(`${graph.asOf.slice(0, 10)}T00:00:00Z`);
  if (Number.isNaN(d.getTime())) return graph.asOf;
  return `${WEEKDAYS[d.getUTCDay()]}, ${d.getUTCDate()} ${MONTHS_LONG[d.getUTCMonth()]} ${d.getUTCFullYear()}`;
}

/* ───────────────────────── dates ───────────────────────── */

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const MONTHS_LONG = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
const WEEKDAYS = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];

interface YM {
  y: number;
  m?: number;
}

function parseYM(s: string | undefined): YM | undefined {
  if (!s) return undefined;
  const m = /^(\d{4})(?:-(\d{2}))?/.exec(s.trim());
  if (!m) return undefined;
  const month = m[2] ? Number(m[2]) : undefined;
  return { y: Number(m[1]), m: month && month >= 1 && month <= 12 ? month : undefined };
}

const ym = (v: YM) => (v.m ? `${MONTHS[v.m - 1]} ${v.y}` : String(v.y));

/** "Jul 2025 – now", "Jan – Apr 2024", "2020 – 2024", "2023"; '' when the fact has no dates. */
export function formatPeriod(f: Fact): string {
  const a = parseYM(f.start);
  const b = parseYM(f.end);
  if (!a) return b ? ym(b) : f.current ? 'now' : '';
  if (f.current) return `${ym(a)} – now`;
  if (!b) return ym(a);
  if (a.y === b.y && a.m && b.m) return a.m === b.m ? ym(a) : `${MONTHS[a.m - 1]} – ${MONTHS[b.m - 1]} ${a.y}`;
  if (a.y === b.y && !a.m && !b.m) return String(a.y);
  return `${ym(a)} – ${ym(b)}`;
}

/** Year of the earliest experience start ("Est. 2023" in the newspaper), else of the earliest education start. */
export function firstWorkYear(content: PortfolioContent): number | undefined {
  const years = (kind: FactKind) =>
    factsOf(content, kind)
      .map((f) => parseYM(f.start)?.y)
      .filter((y): y is number => y !== undefined);
  const work = years('experience');
  if (work.length) return Math.min(...work);
  const edu = years('education');
  return edu.length ? Math.min(...edu) : undefined;
}

/* ───────────────────────── style ───────────────────────── */

const RADIUS = { square: 2, soft: 10, round: 18 } as const;
const SECTION_GAP = { airy: 112, compact: 72 } as const;

/**
 * CSS custom properties for the portfolio wrapper (`.pf-scope`): the family hue and its lobe hues
 * (tokens.css recomputes every role colour from them), warm-sector easing, corners and section rhythm.
 */
export function styleVars(spec: PortfolioSpec): Record<string, string> {
  const h = FAMILY_HUES[spec.style.family];
  return {
    '--h': String(h),
    '--h2': String((h + 35) % 360),
    '--h3': String((h + 315) % 360),
    '--kw': h >= 60 && h <= 110 ? '0.75' : '1',
    '--r': `${RADIUS[spec.style.corners]}px`,
    '--sec': `${SECTION_GAP[spec.style.density]}px`,
  };
}

/** True when the name needs fallback faces (non-Latin script): no blackletter, no display serif swap. */
export function needsFallbackFaces(name: string): boolean {
  return /[^\u0000-ɏḀ-ỿ -⁯\s'’.\-]/.test(name);
}

/** Only http(s) and mailto links are ever rendered as links. */
export function safeHref(url: string | undefined): string | undefined {
  if (!url) return undefined;
  return /^(https?:\/\/|mailto:)/i.test(url.trim()) ? url.trim() : undefined;
}

/** "riyakapoor.design" from "https://www.riyakapoor.design/" for link labels. */
export function displayUrl(url: string): string {
  return url.replace(/^mailto:/i, '').replace(/^https?:\/\//i, '').replace(/^www\./i, '').replace(/\/$/, '');
}
