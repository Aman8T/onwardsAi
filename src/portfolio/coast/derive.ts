/**
 * The Neon Coast page model: a pure function over spec + content + graph (no React, no CSS). It decides which
 * panels exist (only blocks in `shownBlocks(spec, mode)` that have something to show) and words every line from
 * fact fields, copy slots or the kit's constant chrome, the same way the standard blocks do. Nothing is invented:
 * an empty source means the panel is absent, and a figure set large is one the fact's own line already holds.
 */
import type { Fact, GraphSnapshot, PortfolioContent, PortfolioSpec } from '../../../shared/portfolio';
import { initials } from '../../lib/ui';
import { figureOf } from '../blocks/Achievements';
import { cityOf, contactItems, earliestStart, fmtYears } from '../blocks/SectionShell';
import type { ContactItem } from '../blocks/SectionShell';
import {
  asOfLabel,
  displayUrl,
  experienceLines,
  factsOf,
  formatPeriod,
  needsFallbackFaces,
  ownWords,
  projectBlurb,
  safeHref,
  shownBlocks,
  shownDomains,
  slotText,
  sourcesLine,
} from '../model';
import type { ViewMode } from '../model';

/** The panels a page can link to from its own navigation and hero buttons. */
export type CoastAnchor = 'skills' | 'work' | 'projects' | 'clips' | 'contact';

export interface CoastRole {
  id: string;
  period: string;
  title: string;
  org: string;
  /** Location and the fact's one line of detail. */
  sub: string;
  lines: string[];
}

export interface CoastProject {
  id: string;
  title: string;
  /** Organisation and period, else the first two tags (as the standard rows do). */
  meta: string;
  blurb: string;
  tags: string[];
  href?: string;
  linkLabel?: string;
}

export interface CoastClip {
  id: string;
  title: string;
  meta: string;
  href?: string;
}

export interface CoastHonour {
  id: string;
  /** A figure the line itself states ("9.4%", "820 → 140 ms"), set large; absent when the line has none. */
  figure?: string;
  title: string;
  meta: string;
}

export interface CoastSchool {
  id: string;
  title: string;
  place: string;
  period: string;
  detail: string;
  grade: string;
}

export interface CoastPage {
  name: string;
  monogram: string;
  /** Characters in the name's longest word: the title is sized so that word always fits its line. */
  longest: number;
  /** A non-Latin name: the poster face has no glyphs for it, so the title is set in the body face. */
  plainName: boolean;
  eyebrow: string;
  tagline: string;
  facts: string[];
  actions: { to: CoastAnchor; label: string }[];
  nav: { to: CoastAnchor; label: string }[];
  stats: { value: string; label: string }[];
  showGraph: boolean;
  about?: { lede: string; body: string; facts: { k: string; v: string }[] };
  quote?: { text: string; caption: string };
  roles: CoastRole[];
  /** "2.4 years · 3 roles", from the person's own record. */
  rolesMeta: string;
  projects: CoastProject[];
  clips: CoastClip[];
  honours: CoastHonour[];
  schools: CoastSchool[];
  openTo: string[];
  contact: ContactItem[];
  footer: string;
}

const clean = (s: string | undefined): string => (s ?? '').replace(/\s+/g, ' ').trim();
const join = (parts: Array<string | undefined>, sep: string): string => parts.map(clean).filter(Boolean).join(sep);
const plural = (n: number, one: string, many: string): string => `${n} ${n === 1 ? one : many}`;

/** The hero's short facts, exactly the standard hero's: years, the current role, or the latest study when there is no work yet. */
function heroFacts(content: PortfolioContent, spec: PortfolioSpec): string[] {
  const { person } = content;
  const experience = factsOf(content, 'experience');
  const current = experience.find((f) => f.current);
  const years = person.yearsExperience && person.yearsExperience > 0 ? person.yearsExperience : undefined;
  const items: string[] = [];
  if (years !== undefined) items.push(`${fmtYears(years)} ${years === 1 ? 'year' : 'years'} of experience`);
  if (current) items.push(current.org ? `${current.title} at ${current.org}` : current.title);
  if (!experience.length) {
    const edu = factsOf(content, 'education')[0];
    if (edu) {
      items.push(edu.title);
      if (edu.org) items.push(edu.org);
      if (edu.grade && spec.privacy.grades) items.push(edu.grade);
    }
  }
  return items.map(clean).filter(Boolean).slice(0, 3);
}

/** The About panel's fact rows, exactly the standard block's: where, since when, what they studied. */
function aboutFacts(content: PortfolioContent): { k: string; v: string }[] {
  const out: { k: string; v: string }[] = [];
  if (content.person.location) out.push({ k: 'Based in', v: clean(content.person.location) });
  const experience = factsOf(content, 'experience');
  const since = earliestStart(experience);
  if (since && experience[0]) {
    out.push({ k: 'Working since', v: formatPeriod({ ...experience[0], start: since, end: undefined, current: false }) });
  }
  const edu = factsOf(content, 'education')[0];
  if (edu) out.push({ k: 'Studied', v: join([edu.title, edu.org], ', ') });
  return out;
}

function roleOf(content: PortfolioContent, f: Fact): CoastRole {
  return {
    id: f.id,
    period: formatPeriod(f),
    title: clean(f.title),
    org: clean(f.org),
    sub: join([f.location, f.detail], ' · '),
    lines: experienceLines(content, f).map(clean).filter(Boolean),
  };
}

function projectOf(content: PortfolioContent, f: Fact): CoastProject {
  const href = safeHref(f.url);
  return {
    id: f.id,
    title: clean(f.title),
    meta: join([f.org, formatPeriod(f)], ' · ') || f.tags.slice(0, 2).join(' · '),
    blurb: clean(projectBlurb(content, f)),
    tags: f.tags.slice(0, 6).map(clean).filter(Boolean),
    href,
    linkLabel: href ? displayUrl(href) : undefined,
  };
}

function honourOf(f: Fact): CoastHonour {
  const fig = figureOf(f.title);
  const figure = fig ? (fig.unit === '%' ? `${fig.value}%` : join([fig.value, fig.unit], ' ')) : undefined;
  return { id: f.id, figure, title: clean(f.title), meta: join([f.org, formatPeriod(f)], ' · ') };
}

function schoolOf(f: Fact, grades: boolean): CoastSchool {
  return {
    id: f.id,
    title: clean(f.title),
    place: join([f.org, f.location], ', '),
    period: formatPeriod(f),
    detail: clean(f.detail),
    grade: grades ? clean(f.grade) : '',
  };
}

export function buildCoast(spec: PortfolioSpec, content: PortfolioContent, graph: GraphSnapshot, mode: ViewMode): CoastPage {
  const shown = new Set<string>(shownBlocks(spec, mode).map((b) => b.kind));
  const { person } = content;
  const name = clean(person.name);

  const lede = shown.has('about') ? clean(slotText(content, 'aboutLede')) : '';
  const body = shown.has('about') ? clean(slotText(content, 'aboutBody')) : '';
  const quoteText = clean(ownWords(content)?.title).replace(/^["“”‘’']+|["“”‘’']+$/g, '');

  const experience = shown.has('experience') ? factsOf(content, 'experience') : [];
  const years = person.yearsExperience;
  const roles = experience.map((f) => roleOf(content, f));
  const projects = shown.has('projects') ? factsOf(content, 'project').map((f) => projectOf(content, f)) : [];
  const clips = shown.has('clips')
    ? factsOf(content, 'clip').map((f) => ({ id: f.id, title: clean(f.title), meta: join([f.org, formatPeriod(f)], ' · '), href: safeHref(f.url) }))
    : [];
  const contact = shown.has('contact') ? contactItems(content, spec) : [];
  const showGraph = shown.has('graph') && shownDomains(graph, spec).length > 0;

  // The hero's two buttons and the navigation only ever point at panels that are on the page.
  const present: { [K in CoastAnchor]: boolean } = {
    skills: showGraph,
    work: roles.length > 0,
    projects: projects.length > 0,
    clips: clips.length > 0,
    contact: contact.length > 0,
  };
  const second = (
    [
      ['projects', 'Selected work'],
      ['clips', 'Read the clips'],
      ['work', 'Experience'],
    ] as const
  ).find(([to]) => present[to]);
  const actions: CoastPage['actions'] = [];
  if (present.contact) actions.push({ to: 'contact', label: 'Get in touch' });
  if (second) actions.push({ to: second[0], label: second[1] });
  const nav = (
    [
      ['skills', 'Skills'],
      ['work', 'Work'],
      ['projects', 'Projects'],
      ['clips', 'Clips'],
      ['contact', 'Contact'],
    ] as const
  )
    .filter(([to]) => present[to])
    .map(([to, label]) => ({ to, label }));

  const sources = sourcesLine(graph);
  return {
    name,
    monogram: person.monogram || initials(name),
    longest: Math.max(1, ...name.split(/[\s-]+/).map((w) => Array.from(w).length)),
    plainName: needsFallbackFaces(name),
    eyebrow: join([person.role, cityOf(person.location)], ', ') || clean(person.headline),
    tagline: clean(slotText(content, 'tagline')) || clean(person.headline),
    facts: heroFacts(content, spec),
    actions,
    nav,
    stats: shown.has('proof') ? content.stats.slice(0, 4).map((s) => ({ value: clean(s.value), label: clean(s.label) })) : [],
    showGraph,
    about: lede || body ? { lede, body, facts: aboutFacts(content) } : undefined,
    quote: quoteText ? { text: quoteText, caption: `${name}, in their own words` } : undefined,
    roles,
    rolesMeta: roles.length
      ? join([years && years > 0 ? `${fmtYears(years)} ${years === 1 ? 'year' : 'years'}` : '', plural(roles.length, 'role', 'roles')], ' · ')
      : '',
    projects,
    clips,
    honours: shown.has('achievements') ? factsOf(content, 'achievement').map(honourOf) : [],
    schools: shown.has('education') ? factsOf(content, 'education').map((f) => schoolOf(f, spec.privacy.grades)) : [],
    openTo: shown.has('openTo') ? content.openTo.map(clean).filter(Boolean).slice(0, 2) : [],
    contact,
    footer: `Skill Graph snapshot · ${asOfLabel(graph)} · scored by Leafout${sources ? ` from ${sources}` : ''}`,
  };
}
