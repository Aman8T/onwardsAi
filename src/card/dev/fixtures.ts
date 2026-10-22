/**
 * Fixtures for the dev page: Riya Kapoor (the sample persona) as the owner's `CardView` would hold her, by hand from
 * the canvas. The slug is made up; nothing here is a real card. `viewFor` builds the view the server would answer for a
 * given set of settings, link fields and the README snippet included.
 */
import type { CardArea, CardBlocked, CardRole, CardSettings, CardSkill, CardView } from '../../../shared/card';

export const SLUG = 'k7f2m9q4xw2a';
const ORIGIN = 'https://tayyari.fastapicloud.dev';
const UPDATED = '2026-10-22T09:30:00Z';

export const AREAS: CardArea[] = [
  { id: 'frontend', name: 'Frontend Engineering', short: 'Frontend', score: 81 },
  { id: 'design-systems', name: 'Design Systems & Motion', short: 'Design systems', score: 85 },
  { id: 'backend', name: 'Backend & APIs', short: 'Backend', score: 58 },
  { id: 'ai', name: 'AI & LLM Apps', short: 'AI & LLM', score: 36 },
  { id: 'practice', name: 'Engineering Practice', short: 'Practice', score: 55 },
  { id: 'product', name: 'Product & Collaboration', short: 'Product', score: 55 },
];

/** Eight areas with long names: the most a card draws. */
export const MANY_AREAS: CardArea[] = [
  { id: 'a1', name: 'Frontend Engineering and Accessibility', short: 'Frontend', score: 81 },
  { id: 'a2', name: 'Design Systems, Tokens & Motion', short: 'Design systems', score: 85 },
  { id: 'a3', name: 'Backend, APIs & Data Modelling', short: 'Backend', score: 58 },
  { id: 'a4', name: 'AI & LLM Applications in Production', short: 'AI & LLM', score: 36 },
  { id: 'a5', name: 'Engineering Practice & Testing', short: 'Practice', score: 55 },
  { id: 'a6', name: 'Product & Collaboration', short: 'Product', score: 55 },
  { id: 'a7', name: 'Performance & Observability', short: 'Performance', score: 47 },
  { id: 'a8', name: 'Security & Privacy Engineering', short: 'Security', score: 29 },
];

export const THIN_AREAS: CardArea[] = AREAS.slice(0, 2).map((a, i) => ({ ...a, score: i === 0 ? 72 : 64 }));

export const SKILLS: CardSkill[] = [
  { id: 'motion', name: 'Motion & animation', score: 91 },
  { id: 'react', name: 'React', score: 90 },
  { id: 'design-systems', name: 'Design systems', score: 87 },
  { id: 'typescript', name: 'TypeScript', score: 86 },
  { id: 'css', name: 'CSS architecture', score: 84 },
  { id: 'a11y', name: 'Accessibility', score: 78 },
  { id: 'testing', name: 'Component testing', score: 64 },
  { id: 'node', name: 'Node.js', score: 61 },
];

export const ROLES: CardRole[] = [
  { id: 'senior-frontend-engineer', title: 'Senior Frontend Engineer' },
  { id: 'design-engineer', title: 'Design Engineer' },
  { id: 'design-systems-engineer', title: 'Design Systems Engineer' },
];

/** What each role expects per area (AREAS order). */
const TARGETS: Record<string, number[]> = {
  'senior-frontend-engineer': [85, 75, 60, 30, 75, 60],
  'design-engineer': [80, 90, 45, 40, 50, 65],
  'design-systems-engineer': [78, 92, 50, 25, 70, 55],
};

export const OFF: CardSettings = { shared: false, skills: [], theme: 'light' };

export interface ViewOptions {
  areas?: CardArea[];
  blocked?: CardBlocked;
}

function snippet(settings: CardSettings): string {
  const page = `${ORIGIN}/u/${SLUG}`;
  const svg = `${ORIGIN}/r/${SLUG}.svg`;
  const compare = settings.roleId ? `compare=${settings.roleId}` : '';
  if (settings.theme === 'match') {
    const dark = `${svg}?theme=dark${compare ? `&${compare}` : ''}`;
    const light = `${svg}${compare ? `?${compare}` : ''}`;
    return `<a href="${page}">\n  <picture>\n    <source media="(prefers-color-scheme: dark)" srcset="${dark}">\n    <img alt="Skill shape" src="${light}">\n  </picture>\n</a>`;
  }
  const q = [settings.theme !== 'light' ? `theme=${settings.theme}` : '', compare].filter(Boolean).join('&');
  return `[![Skill shape](${svg}${q ? `?${q}` : ''})](${page})`;
}

/** The answer of `GET /api/card` / `PUT /api/card` for these settings. */
export function viewFor(settings: CardSettings, opts: ViewOptions = {}): CardView {
  if (opts.blocked) {
    return { settings: OFF, areas: [], skillsOffered: [], rolesOffered: [], blocked: opts.blocked };
  }
  const base = opts.areas ?? AREAS;
  const targets = settings.roleId ? TARGETS[settings.roleId] : undefined;
  const areas = base.map((a, i) => (targets && targets[i] !== undefined ? { ...a, roleTarget: targets[i] } : a));
  const view: CardView = { settings, areas, skillsOffered: SKILLS, rolesOffered: ROLES };
  if (settings.shared) {
    return {
      ...view,
      slug: SLUG,
      pageUrl: `${ORIGIN}/u/${SLUG}`,
      svgUrl: `${ORIGIN}/r/${SLUG}.svg`,
      ogUrl: `${ORIGIN}/og/${SLUG}-3f9a21c4.png`,
      markdown: snippet(settings),
      updatedAt: UPDATED,
    };
  }
  return view;
}
