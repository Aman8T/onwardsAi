/**
 * Fixtures for the dev page: Ravi K., the canvas's sample person (not a real person), as the owner's `ReportView` would
 * hold him. The employer "Kestrel Infotech" and every other name here are made up; the slug is made up. `viewFor` plays
 * the server's part in miniature (it is not the composer): it redacts the employer while names are off, flags hidden
 * quotes in `offered`, and leaves out of `public` whatever the choices keep private, so the preview behaves as the
 * real one does.
 */
import type { ReportArea, ReportBlocked, ReportChoices, ReportContent, ReportNearMiss, ReportRole, ReportView } from '../../../shared/report';

export const SLUG = 'p4n8x2kq7w3m';
const ORIGIN = 'https://tayyari.fastapicloud.dev';
const UPDATED = '2026-11-17T10:15:00Z';
/** A stand-in for `TAYYARI_REPORT__OFFER_URL`: the paid step's page. */
export const OFFER_URL = 'https://example.com/leafout/day-one';

const FULL_NAME_SHORT = 'Ravi K.';
const LINE = 'Senior Software Engineer · 7 years · Pune';
/** The employer the resume blocks name: replaced in quotes while employer names are off. */
const ORG = /Kestrel Infotech/g;

export const AREAS: ReportArea[] = [
  { name: 'Java backend', short: 'Java backend', score: 78 },
  { name: 'Test automation', short: 'Test automation', score: 72 },
  { name: 'Cloud', short: 'Cloud', score: 34 },
  { name: 'Data and SQL', short: 'Data and SQL', score: 58 },
  { name: 'Delivery with clients', short: 'Delivery', score: 66 },
  { name: 'Mentoring', short: 'Mentoring', score: 52 },
];

/** The roles as written in the resume, before redaction (`hidden` is set by `viewFor`). */
export const ROLES: ReportRole[] = [
  {
    id: 'qa-automation-lead',
    title: 'QA Automation Lead',
    have: 5,
    total: 6,
    ready: true,
    eta: '~1 month',
    quotes: [
      {
        id: 'q-qa-1',
        text: "Led the move of Kestrel Infotech's payments regression suite from Selenium to Playwright, cutting the nightly run from 6 hours to 90 minutes.",
        source: 'Resume',
        hidden: false,
      },
      { id: 'q-qa-2', text: 'Trained four new joiners on the test framework.', source: 'Resume', hidden: false },
    ],
    missing: ['Performance testing at scale'],
  },
  {
    id: 'backend-engineer-gcc',
    title: 'Backend Engineer, at a GCC',
    have: 4,
    total: 6,
    ready: false,
    eta: '~2 months',
    quotes: [
      { id: 'q-be-1', text: "Built and ran the Spring Boot services behind a bank client's loan portal.", source: 'Resume', hidden: false },
      { id: 'q-be-2', text: 'Wrote the SQL behind the monthly settlement reports.', source: 'Resume', hidden: false },
    ],
    missing: ['Kubernetes in production', 'System design for a product team'],
  },
  {
    id: 'platform-engineer',
    title: 'Platform Engineer',
    have: 3,
    total: 6,
    ready: false,
    eta: '~4 months',
    quotes: [{ id: 'q-pe-1', text: "Moved Kestrel Infotech's Jenkins builds to GitHub Actions.", source: 'GitHub', hidden: false }],
    missing: ['Terraform', 'Running services on a cloud', 'On-call for production'],
  },
];

export const NEAR_MISSES: ReportNearMiss[] = [
  { id: 'engineering-manager', title: 'Engineering Manager', missing: ['People management with reports', 'Hiring'] },
  { id: 'solutions-architect', title: 'Solutions Architect', missing: ['Cloud architecture', 'Pre-sales with clients'] },
];

/** The defaults the spec sets: name on, employer names off, quotes on, open to work off, near misses off. */
export const DEFAULTS: ReportChoices = {
  showName: true,
  showOrgs: false,
  showQuotes: true,
  openToWork: false,
  showNearMisses: false,
  hiddenQuotes: [],
};

export const ALL_OFF: ReportChoices = {
  showName: false,
  showOrgs: false,
  showQuotes: false,
  openToWork: false,
  showNearMisses: false,
  hiddenQuotes: [],
};

export interface ViewOptions {
  roles?: ReportRole[];
  nearMisses?: ReportNearMiss[];
  /** How many employer names the resume blocks hold (0: the person never opened /resume). */
  orgsFound?: number;
  offerUrl?: string;
  blocked?: ReportBlocked;
}

const EMPTY: ReportContent = { person: { line: '' }, areas: [], roles: [], nearMisses: [], openToWork: false };

/** The answer of `GET` / `PUT /api/report` for these choices. */
export function viewFor(shared: boolean, choices: ReportChoices, opts: ViewOptions = {}): ReportView {
  if (opts.blocked) {
    return { choices: DEFAULTS, offered: EMPTY, public: EMPTY, orgsFound: 0, blocked: opts.blocked };
  }
  const orgsFound = opts.orgsFound ?? 1;
  const redact = (t: string) => (choices.showOrgs || orgsFound === 0 ? t : t.replace(ORG, 'their employer'));
  const roles = (opts.roles ?? ROLES).map((r) => ({
    ...r,
    quotes: r.quotes.map((q) => ({ ...q, text: redact(q.text), hidden: choices.hiddenQuotes.includes(q.id) })),
  }));
  const nearMisses = opts.nearMisses ?? NEAR_MISSES;
  const person = { ...(choices.showName ? { name: FULL_NAME_SHORT } : {}), line: LINE };

  const offered: ReportContent = { person, areas: AREAS, roles, nearMisses, openToWork: choices.openToWork };
  // with no employer known and names off, nothing could be redacted, so the public page quotes nothing
  const quotesPublic = choices.showQuotes && (choices.showOrgs || orgsFound > 0);
  const pub: ReportContent = {
    person,
    areas: AREAS,
    roles: roles.map((r) => ({ ...r, quotes: quotesPublic ? r.quotes.filter((q) => !q.hidden) : [] })),
    nearMisses: choices.showNearMisses ? nearMisses : [],
    openToWork: choices.openToWork,
  };

  const view: ReportView = { choices, offered, public: pub, orgsFound,...(opts.offerUrl ? { offerUrl: opts.offerUrl } : {}) };
  if (!shared) return view;
  return {
    ...view,
    slug: SLUG,
    pageUrl: `${ORIGIN}/next/${SLUG}`,
    ogUrl: `${ORIGIN}/og/next/${SLUG}-8c21f0ab.png`,
    updatedAt: UPDATED,
  };
}
