/**
 * The newspaper's page model: a pure function over spec + content + graph (no React, no CSS). It decides
 * which sections exist (only blocks in `shownBlocks(spec, mode)`), and words every line from fact fields,
 * copy slots or the kit's constant chrome. Nothing is invented: an empty source means the section is absent.
 */
import type { Fact, GraphSnapshot, PortfolioContent, PortfolioSpec } from '../../../shared/portfolio';
import {
  andList,
  asOfLabel,
  displayUrl,
  experienceLines,
  factsOf,
  firstWorkYear,
  formatPeriod,
  longDateLabel,
  ownWords,
  projectBlurb,
  safeHref,
  shownBlocks,
  slotText,
  sourcesLine,
} from '../model';
import type { ViewMode } from '../model';

export interface PressStory {
  id: string;
  heading: string;
  /** Italic standfirst line: period, detail, tags. */
  meta: string;
  /** "BENGALURU, Jul 2025" (printed with an em dash after it), or ''. */
  dateline: string;
  text: string;
  href?: string;
  linkLabel?: string;
}

export interface PressNumber {
  value: string;
  label: string;
  /** Long values span both columns of the box. */
  wide: boolean;
  /** In the first row of the box (no rule above it). */
  first: boolean;
}

export interface PressHonour {
  id: string;
  heading: string;
  meta: string;
}

export interface PressSchool {
  id: string;
  heading: string;
  place: string;
  years: string;
  grade: string;
  note: string;
}

export interface PressAd {
  id: string;
  key: string;
  value: string;
  href?: string;
}

export interface PressPage {
  earLeft?: string;
  earRight?: string;
  folio: string;
  lead: { kicker: string; headline: string; deck: string; dateline: string; paragraphs: string[] };
  career: PressStory[];
  brief: PressStory[];
  numbers: PressNumber[];
  honours: PressHonour[];
  school: PressSchool[];
  quote?: { text: string; caption: string };
  /** Roles for the "Situation Wanted" classified (open-to block shown and non-empty). */
  wanted: string;
  ads: PressAd[];
  /** The graph block is shown: place the Skills Index (or Watch). */
  showIndex: boolean;
  /** Few stories: the page becomes a banner front page with fewer, wider columns. */
  sparse: boolean;
  footer: string;
}

/* ───────────────────────── small text helpers ───────────────────────── */

const clean = (s: string | undefined): string => (s ?? '').replace(/\s+/g, ' ').trim();

/** Adds a closing full stop to a line that ends on a letter or digit (joined highlights read as sentences). */
const sentence = (s: string): string => (/[\p{L}\p{N}]$/u.test(s) ? `${s}.` : s);

const upper = (s: string): string => s.toLocaleUpperCase();

/** "Bengaluru" from "Bengaluru, India". */
export function cityOf(location: string | undefined): string {
  return clean(location?.split(',')[0]);
}

const join = (parts: Array<string | undefined>, sep: string): string => parts.map(clean).filter(Boolean).join(sep);

/** "Jul 2025" – the start of a fact only. */
function startLabel(f: Fact): string {
  return f.start ? formatPeriod({ ...f, end: undefined, current: false }) : '';
}

function dateline(f: Fact): string {
  return join([upper(clean(f.location)), startLabel(f)], ', ');
}

/* ───────────────────────── sections ───────────────────────── */

function careerOf(content: PortfolioContent): PressStory[] {
  return factsOf(content, 'experience').map((f) => {
    const lines = experienceLines(content, f).map(clean).filter(Boolean);
    return {
      id: f.id,
      heading: join([f.title, f.org], ', '),
      meta: join([formatPeriod(f), lines.length ? f.detail : undefined], ' · '),
      dateline: dateline(f),
      text: lines.length ? lines.map(sentence).join(' ') : clean(f.detail),
    };
  });
}

function briefOf(content: PortfolioContent): PressStory[] {
  return factsOf(content, 'project').map((f) => {
    const href = safeHref(f.url);
    return {
      id: f.id,
      heading: clean(f.title),
      meta: join([formatPeriod(f), ...f.tags], ' · '),
      dateline: '',
      text: clean(projectBlurb(content, f)),
      href,
      linkLabel: href ? displayUrl(href) : undefined,
    };
  });
}

/** Wide values take both columns; `first` marks the top row so it carries no rule above it. */
function numbersOf(content: PortfolioContent): PressNumber[] {
  let row = 0;
  let col = 0;
  return content.stats.map((s) => {
    const wide = s.value.length > 5;
    if (wide && col !== 0) {
      row++;
      col = 0;
    }
    const first = row === 0;
    if (wide) {
      row++;
      col = 0;
    } else if (++col === 2) {
      row++;
      col = 0;
    }
    return { value: clean(s.value), label: clean(s.label), wide, first };
  });
}

function honoursOf(content: PortfolioContent): PressHonour[] {
  return factsOf(content, 'achievement').map((f) => ({
    id: f.id,
    heading: clean(f.title),
    meta: join([f.org, formatPeriod(f), f.detail], ' · '),
  }));
}

function schoolOf(content: PortfolioContent, spec: PortfolioSpec): PressSchool[] {
  return factsOf(content, 'education').map((f) => ({
    id: f.id,
    heading: clean(f.title),
    place: join([f.org, f.location], ', '),
    years: formatPeriod(f),
    grade: spec.privacy.grades ? clean(f.grade) : '',
    note: clean(f.detail),
  }));
}

const isMail = (f: Fact): boolean => /^mailto:/i.test((f.url ?? '').trim());

function adKey(f: Fact): string {
  if (f.kind === 'contact') return isMail(f) ? 'WRITE' : 'CALL';
  if (/(^|\/\/|\.)(github|gitlab|bitbucket)\.(com|org)\b/i.test(f.url ?? '')) return 'CODE';
  return upper(clean(f.title));
}

function adsOf(content: PortfolioContent, spec: PortfolioSpec): PressAd[] {
  const out: PressAd[] = [];
  for (const f of content.facts) {
    if (f.kind === 'link') {
      const href = safeHref(f.url);
      if (href) out.push({ id: f.id, key: adKey(f), value: displayUrl(href), href });
    } else if (f.kind === 'contact') {
      if (isMail(f) ? !spec.privacy.email : !spec.privacy.phone) continue; // opt-in details only
      const href = safeHref(f.url);
      const value = href ? displayUrl(href) : clean(f.sourceRefs[0]?.quote) || clean(f.title);
      if (value) out.push({ id: f.id, key: adKey(f), value, href });
    }
  }
  return out;
}

/* ───────────────────────── the lead story ───────────────────────── */

/** Below this many characters the about slots alone make a thin story; the latest role's lines fill it out. */
const THIN_BODY = 420;

function leadOf(content: PortfolioContent, shown: Set<string>): PressPage['lead'] {
  const { person } = content;
  const city = cityOf(person.location);
  const paragraphs: string[] = [];
  if (shown.has('about')) {
    for (const id of ['aboutLede', 'aboutBody']) {
      const t = clean(slotText(content, id));
      if (t) paragraphs.push(t);
    }
  }
  const chars = paragraphs.reduce((n, p) => n + p.length, 0);
  const latest = factsOf(content, 'experience')[0];
  if (shown.has('experience') && latest && chars < THIN_BODY) {
    for (const line of experienceLines(content, latest)) {
      const t = clean(line);
      if (t) paragraphs.push(sentence(t));
    }
  }
  return {
    kicker: join([person.role, city], ' · '),
    headline: clean(slotText(content, 'pressHeadline')) || join([person.name, person.role], ', '),
    deck: clean(slotText(content, 'pressDeck')),
    dateline: upper(city),
    paragraphs,
  };
}

/* ───────────────────────── the page ───────────────────────── */

export function buildPress(
  spec: PortfolioSpec,
  content: PortfolioContent,
  graph: GraphSnapshot,
  mode: ViewMode,
): PressPage {
  const shown = new Set<string>(shownBlocks(spec, mode).map((b) => b.kind));
  const year = firstWorkYear(content);
  const city = cityOf(content.person.location);

  const career = shown.has('experience') ? careerOf(content) : [];
  const brief = shown.has('projects') ? briefOf(content) : [];
  const quoteFact = ownWords(content);
  const quoteText = clean(quoteFact?.title).replace(/^["“”‘’']+|["“”‘’']+$/g, '');
  const sources = graph.sources.length ? ` · scored by Tayyari from ${sourcesLine(graph)}` : '';

  return {
    earLeft: year !== undefined ? `Est. ${year}` : undefined,
    earRight: city ? `${city} Edition` : undefined,
    folio: `${longDateLabel(graph)} · Portfolio Edition · Price: one conversation`,
    lead: leadOf(content, shown),
    career,
    brief,
    numbers: shown.has('proof') ? numbersOf(content) : [],
    honours: shown.has('achievements') ? honoursOf(content) : [],
    school: shown.has('education') ? schoolOf(content, spec) : [],
    quote: quoteText ? { text: quoteText, caption: `${content.person.name}, in their own words` } : undefined,
    wanted: shown.has('openTo') ? andList(content.openTo.map(clean).filter(Boolean)) : '',
    ads: shown.has('contact') ? adsOf(content, spec) : [],
    showIndex: shown.has('graph'),
    sparse: career.length + brief.length <= 2,
    footer: `Skill Graph snapshot · ${asOfLabel(graph)}${sources}`,
  };
}
