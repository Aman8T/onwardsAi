/**
 * Pure helpers for the top of the dashboard, the person's own space:
 *
 *   readLines       the read's one-liners (each in a tone), the ones the top of the page rotates through
 *   achievements    what is worth celebrating, counted from the graph and what the features store
 *   notOnResume     the strongest skills the other sources proved that the resume never mentions
 *   deltas          what moved between two analyses of the same person
 *   askQuestions    four questions worth asking about yourself, from the top role and the gap
 *
 * Everything here is counted from stored data; the read's lines are the one thing a model phrased, at analysis time.
 * No DOM, no network.
 */
import type { Evidence, Profile, ReadLine, ReadTone, Role, Skill, SourceSummary } from '../../../shared/types';
import type { PracticeHistory } from '../../../shared/interview';
import type { PreparePlanSummary } from '../../../shared/prepare';
import type { TrackedJob } from '../../discover/applications/tracker';
import type { MatchSummary } from '../../match/types';
import { rankRoles } from '../../lib/layout';

/* ───────────────────────── the read ───────────────────────── */

export const TONE_LABEL: Record<ReadTone, string> = {
  proud: 'Proud',
  roast: 'Roast',
  motivational: 'Next move',
  quirky: 'Quirky',
  sarcastic: 'Dry',
  plain: 'Plainly',
};

/** The lines of the read that carry a tone; a profile written before the lines existed has none. */
export function readLines(profile: Profile): Array<ReadLine & { tone: ReadTone }> {
  return (profile.read ?? []).filter((l): l is ReadLine & { tone: ReadTone } => !!l.tone && !!l.text.trim());
}

/* ───────────────────────── worth celebrating ───────────────────────── */

export type AchievementKind = 'graph' | 'moved' | 'applications' | 'practice' | 'match' | 'prepare' | 'weekly';

export interface Achievement {
  id: string;
  kind: AchievementKind;
  title: string;
  detail: string;
  /** ISO time it happened, when known; the graph's own are dated by the analysis. */
  at?: string;
  /** Within the last week: the shelf marks it new. */
  fresh: boolean;
}

export interface AchievementInput {
  profile: Profile;
  previous: Profile | null;
  tracked: readonly TrackedJob[];
  history: PracticeHistory | null;
  matches: MatchSummary[];
  plans: PreparePlanSummary[];
  subscribed: boolean;
  now: number;
}

const FRESH_MS = 7 * 24 * 3600 * 1000;
const SHELF_MAX = 6;
const EXPERT_DOMAIN = 80;
const EXPERT_SKILL = 90;
const MASTERY_HIGH = 75;
const DOMAIN_UP = 5;

const when = (iso: string | undefined): number => {
  const t = iso ? Date.parse(iso) : NaN;
  return Number.isFinite(t) ? t : 0;
};

/**
 * At most six things worth celebrating, newest first: expert domains and skills, a domain that climbed, an offer or an
 * interview, a first application, practice sessions where every claim held, a strong posting match, a running plan,
 * reading the Weekly. Nothing is scored or phrased by a model; a "first" is counted from the stored records.
 */
export function achievements(input: AchievementInput): Achievement[] {
  const { profile, previous, tracked, history, matches, plans, subscribed, now } = input;
  const out: Achievement[] = [];
  const add = (a: Omit<Achievement, 'fresh'>) => {
    const t = when(a.at);
    out.push({ ...a, fresh: t > 0 && now - t < FRESH_MS });
  };
  const analysed = profile.createdAt;

  // the graph itself
  for (const d of profile.domains) {
    if (d.score >= EXPERT_DOMAIN) add({ id: `domain:${d.id}`, kind: 'graph', title: `Expert in ${d.name}`, detail: `${d.score} of 100, from your evidence`, at: analysed });
  }
  for (const s of profile.skills) {
    if (s.score >= EXPERT_SKILL) add({ id: `skill:${s.id}`, kind: 'graph', title: `${s.name} at ${s.score}`, detail: s.evidence[0]?.text ?? 'Strong public and professional proof', at: analysed });
  }
  if (profile.mastery.score >= MASTERY_HIGH) {
    add({ id: 'mastery', kind: 'graph', title: `Role mastery ${profile.mastery.score}`, detail: `For a ${profile.person.currentRole} at your level`, at: analysed });
  }
  const readSources = profile.sources.filter((s) => s.ok);
  if (readSources.length >= 3) {
    add({ id: 'sources', kind: 'graph', title: `${readSources.length} sources read`, detail: readSources.map((s) => s.label).join(' · '), at: analysed });
  }

  // what moved since the last analysis
  if (previous) {
    const byName = new Map(previous.domains.map((d) => [d.name.trim().toLowerCase(), d]));
    for (const d of profile.domains) {
      const before = byName.get(d.name.trim().toLowerCase());
      if (before && d.score - before.score >= DOMAIN_UP) {
        add({ id: `up:${d.id}`, kind: 'moved', title: `${d.name} up ${d.score - before.score}`, detail: `${before.score} → ${d.score} since your last analysis`, at: analysed });
      }
    }
    const top = rankRoles(profile)[0];
    const prevTop = rankRoles(previous)[0];
    if (top && prevTop && top.title.trim().toLowerCase() !== prevTop.title.trim().toLowerCase()) {
      add({ id: `top:${top.id}`, kind: 'moved', title: `${top.title} is now your closest role`, detail: `${top.alignment}% aligned · ${top.eta}`, at: analysed });
    }
  }

  // applications
  const offers = tracked.filter((t) => t.stage === 'offer');
  for (const t of offers) add({ id: `offer:${t.job.id}`, kind: 'applications', title: `An offer from ${t.job.employer}`, detail: t.job.title, at: t.appliedAt ?? t.savedAt });
  const interviewing = tracked.filter((t) => t.stage === 'interviewing');
  for (const t of interviewing.slice(0, 2)) add({ id: `interview:${t.job.id}`, kind: 'applications', title: `Interviewing at ${t.job.employer}`, detail: t.job.title, at: t.appliedAt ?? t.savedAt });
  const applied = tracked.filter((t) => t.stage === 'applied' || t.stage === 'interviewing' || t.stage === 'offer').sort((a, b) => when(a.appliedAt ?? a.savedAt) - when(b.appliedAt ?? b.savedAt));
  if (applied.length >= 5) add({ id: 'applied:5', kind: 'applications', title: `${applied.length} applications sent`, detail: `Latest: ${applied[applied.length - 1].job.employer}`, at: applied[applied.length - 1].appliedAt ?? applied[applied.length - 1].savedAt });
  else if (applied.length >= 1) add({ id: 'applied:first', kind: 'applications', title: 'First application sent', detail: `${applied[0].job.title} · ${applied[0].job.employer}`, at: applied[0].appliedAt ?? applied[0].savedAt });

  // practice
  if (history && history.sessions.length) {
    const sessions = history.sessions.slice().sort((a, b) => when(a.createdAt) - when(b.createdAt));
    const clean = sessions.filter((s) => s.counts && s.counts.defended > 0 && s.counts.shaky === 0 && s.counts.couldnt === 0);
    const last = clean[clean.length - 1];
    if (last) add({ id: `clean:${last.id}`, kind: 'practice', title: 'Defended every claim', detail: `${last.title} · ${last.counts!.defended} of ${last.counts!.defended}`, at: last.createdAt });
    if (sessions.length >= 5) add({ id: 'sessions:5', kind: 'practice', title: `${sessions.length} practice sessions`, detail: `Latest: ${sessions[sessions.length - 1].title}`, at: sessions[sessions.length - 1].createdAt });
    else add({ id: 'sessions:first', kind: 'practice', title: 'First practice session', detail: sessions[0].title, at: sessions[0].createdAt });
    const defended = history.claims.filter((c) => c.best === 'defended').length;
    if (defended >= 5) add({ id: `claims:${defended}`, kind: 'practice', title: `${defended} claims you can defend`, detail: 'Asked about, out loud, and held up', at: sessions[sessions.length - 1].createdAt });
  }

  // posting matches
  const strong = matches.filter((m) => m.band === 'strong').sort((a, b) => when(b.createdAt) - when(a.createdAt));
  if (strong[0]) add({ id: `match:${strong[0].id}`, kind: 'match', title: 'A strong match', detail: [strong[0].title, strong[0].company].filter(Boolean).join(' at '), at: strong[0].createdAt });

  // prepare
  const firstPlan = plans.slice().sort((a, b) => when(a.createdAt) - when(b.createdAt))[0];
  if (firstPlan) add({ id: `plan:${firstPlan.id}`, kind: 'prepare', title: `A plan for ${firstPlan.roleTitle}`, detail: plans.length > 1 ? `${plans.length} plans running` : 'Where you stand, the one move, the weeks', at: firstPlan.createdAt });

  if (subscribed) add({ id: 'weekly', kind: 'weekly', title: 'Reading the Weekly', detail: 'Every line cited, every Monday' });

  // what happened outranks what the graph says; the graph's own facts take at most three places, so the shelf is not all graph
  const rank = (a: Achievement) => KIND_RANK[a.kind];
  const sorted = out.sort((a, b) => Number(b.fresh) - Number(a.fresh) || rank(a) - rank(b) || when(b.at) - when(a.at));
  let graphLeft = GRAPH_MAX;
  return sorted
    .filter((a) => {
      if (a.kind !== 'graph') return true;
      graphLeft -= 1;
      return graphLeft >= 0;
    })
    .slice(0, SHELF_MAX);
}

const KIND_RANK: Record<AchievementKind, number> = { applications: 0, practice: 1, moved: 2, match: 3, prepare: 4, weekly: 5, graph: 6 };
const GRAPH_MAX = 3;

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
