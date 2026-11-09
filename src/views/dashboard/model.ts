/**
 * Pure helpers for the top of the dashboard (boards "Dashboard idea 1" and "idea 2" on the all-screens canvas):
 *
 *   readProse       the read's segments as one string
 *   notOnResume     the strongest skills the other sources proved that the resume never mentions
 *   deltas          what moved between two analyses of the same person
 *   askQuestions    four questions worth asking about yourself, from the top role and the gap
 *
 * Everything here is counted from the profile; nothing is phrased by a model. No DOM, no network.
 */
import type { Evidence, Profile, ReadSegment, Role, Skill, SourceSummary } from '../../../shared/types';
import { rankRoles } from '../../lib/layout';

export function readProse(read: ReadSegment[] | undefined): string {
  return (read ?? []).map((s) => s.text).join('');
}

/** How many segments of the read point at a skill that is on the graph. */
export function citedCount(profile: Profile): number {
  const ids = new Set(profile.skills.map((s) => s.id));
  return (profile.read ?? []).filter((s) => s.skillId && ids.has(s.skillId)).length;
}

/* ───────────────────────── not on your resume ───────────────────────── */

export interface Surprise {
  skill: Skill;
  /** The strongest evidence item, from a source other than the resume. */
  evidence: Evidence;
  source: SourceSummary;
}

export interface NotOnResume {
  resume: SourceSummary;
  items: Surprise[];
  /** The role the items count towards: the most probable next role. */
  role: Role | undefined;
}

const SURPRISE_MIN_SCORE = 60;
const SURPRISE_MAX = 2;

/**
 * Skills scoring 60 or more whose every evidence line comes from somewhere other than the resume, the ones the top role
 * asks for first, then by score. Null when there is no readable resume, or nothing qualifies.
 */
export function notOnResume(profile: Profile): NotOnResume | null {
  const resume = profile.sources.find((s) => s.kind === 'resume' && s.ok);
  if (!resume) return null;
  const sources = new Map(profile.sources.map((s) => [s.id, s]));
  const role = rankRoles(profile)[0];
  const wanted = new Set(role?.requirements.map((q) => q.skillId).filter((id): id is string => !!id));
  const items: Surprise[] = [];
  for (const skill of profile.skills) {
    if (skill.score < SURPRISE_MIN_SCORE || skill.evidence.length === 0) continue;
    if (skill.evidence.some((e) => e.sourceId === resume.id)) continue;
    const evidence = skill.evidence.find((e) => sources.get(e.sourceId)?.ok);
    const source = evidence ? sources.get(evidence.sourceId) : undefined;
    if (!evidence || !source) continue;
    items.push({ skill, evidence, source });
  }
  if (items.length === 0) return null;
  items.sort((a, b) => Number(wanted.has(b.skill.id)) - Number(wanted.has(a.skill.id)) || b.skill.score - a.skill.score);
  return { resume, items: items.slice(0, SURPRISE_MAX), role };
}

/* ───────────────────────── since last time ───────────────────────── */

export interface Delta {
  /** What moved: a domain, a role, the mastery, a source. */
  label: string;
  /** The old value, shown faint; empty when there was none. */
  was: string;
  /** The new value, shown plain. */
  now: string;
  note: string;
}

const DOMAIN_MIN_MOVE = 3;

const key = (s: string) => s.trim().toLowerCase().replace(/[^a-z0-9]+/g, ' ');

export function ordinal(n: number): string {
  const rem100 = n % 100;
  if (rem100 >= 11 && rem100 <= 13) return `${n}th`;
  switch (n % 10) {
    case 1:
      return `${n}st`;
    case 2:
      return `${n}nd`;
    case 3:
      return `${n}rd`;
    default:
      return `${n}th`;
  }
}

/** Two profiles of the same person, older then newer: at most four things that moved, the biggest first. */
export function deltas(prev: Profile, next: Profile): Delta[] {
  const out: Delta[] = [];

  // the domain that moved most
  const prevDomains = new Map(prev.domains.map((d) => [key(d.name), d]));
  let best: { name: string; was: number; now: number } | null = null;
  for (const d of next.domains) {
    const before = prevDomains.get(key(d.name)) ?? prev.domains.find((p) => p.id === d.id);
    if (!before) continue;
    const move = d.score - before.score;
    if (Math.abs(move) < DOMAIN_MIN_MOVE) continue;
    if (!best || Math.abs(move) > Math.abs(best.now - best.was)) best = { name: d.name, was: before.score, now: d.score };
  }
  if (best) {
    const move = best.now - best.was;
    out.push({ label: best.name, was: String(best.was), now: String(best.now), note: move > 0 ? `up ${move} since the last analysis` : `down ${-move} since the last analysis` });
  }

  // a role that changed rank, the new top role first
  const prevRoles = rankRoles(prev);
  const nextRoles = rankRoles(next);
  for (const [i, role] of nextRoles.entries()) {
    const j = prevRoles.findIndex((r) => key(r.title) === key(role.title));
    if (j === -1 || j === i) continue;
    out.push({
      label: role.title,
      was: ordinal(j + 1),
      now: ordinal(i + 1),
      note: i === 0 ? `now your most probable next role · ${role.alignment}% aligned` : `${role.alignment}% aligned · ${role.eta}`,
    });
    break;
  }

  // role mastery
  if (next.mastery.score !== prev.mastery.score) {
    out.push({ label: 'Role mastery', was: String(prev.mastery.score), now: String(next.mastery.score), note: next.mastery.insight });
  }

  // a source that was not there before
  const had = new Set(prev.sources.map((s) => key(s.url ?? s.label)));
  const added = next.sources.filter((s) => s.ok && !had.has(key(s.url ?? s.label)));
  if (added.length) {
    const first = added[0];
    out.push({ label: 'New source', was: '', now: first.label, note: added.length > 1 ? `and ${added.length - 1} more · ${first.sub}` : first.sub });
  }

  // the gap, whether or not it moved
  if (out.length < 4 && next.domains.length) {
    const gap = next.domains.reduce((a, b) => (b.score < a.score ? b : a));
    const before = prevDomains.get(key(gap.name));
    if (!best || key(best.name) !== key(gap.name)) {
      out.push({ label: gap.name, was: before && before.score !== gap.score ? String(before.score) : '', now: String(gap.score), note: before && before.score === gap.score ? 'the gap · unchanged' : 'the gap' });
    }
  }
  return out.slice(0, 4);
}

/* ───────────────────────── ask about yourself ───────────────────────── */

export function askQuestions(profile: Profile): string[] {
  const top = rankRoles(profile)[0];
  const gap = profile.domains.length ? profile.domains.reduce((a, b) => (b.score < a.score ? b : a)) : undefined;
  return [
    'What would surprise a recruiter about me?',
    'Which of my claims is weakest, and why?',
    top ? `What closes the gap to ${top.title} fastest?` : gap ? `How do I close the gap in ${gap.name}?` : 'What should I build next?',
    top ? `Read me as a hiring manager for ${top.title} would.` : 'Read me as a hiring manager would.',
  ];
}
