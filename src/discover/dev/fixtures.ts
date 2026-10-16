/**
 * Fixtures for the dev gallery (`/dev/discover`): the Discover contract (`shared/discover.ts`) filled with a made-up
 * world, so every state can be seen with no server and no account. All employers are invented; the places are real
 * names from the registry the backend knows, with rough straight-line distances.
 *
 * The Bengaluru result is a small search engine over a table of 40 listings (`riyaResponse`): the lens really filters
 * and re-counts it, so the stops, groups, lanes and suggestion move together the way the server's would. Every other
 * state is a fixed answer. Times are relative to now, so freshness reads true whenever the gallery is opened.
 */
import type {
  CompanyLead,
  DiscoverArea,
  DiscoverCity,
  DiscoverCoverage,
  DiscoverResponse,
  DiscoverSource,
  DiscoverWorkMode,
  Group,
  LensStop,
  ListingCard,
  QuickFitWord,
  Reason,
} from '../../../shared/discover';
import { REACH_ORDER } from '../model';
import type { DiscoverQuery } from '../route';
import { effectiveReach } from '../route';
import type { TrackedRole } from '../tracker';

const DAY = 86_400_000;
export const ago = (days: number, hours = 0) => new Date(Date.now() - days * DAY - hours * 3_600_000).toISOString();

/** The freshness phrase the server writes, from how many days ago the employer posted it. */
export function freshnessOf(days?: number): string {
  if (days === undefined) return "On the employer's board now";
  if (days <= 0) return 'Posted today';
  if (days === 1) return 'Posted yesterday';
  if (days <= 14) return `Posted ${days} days ago`;
  if (days <= 45) return `Open ${Math.round(days / 7)} weeks`;
  if (days <= 90) return "Worth checking it's still hiring";
  return 'Open over 3 months';
}

const checkedPhrase = (days: number) => (days === 0 ? 'today' : days === 1 ? 'yesterday' : `${days} days ago`);

/* ───────────────────────── places ───────────────────────── */

export const CITIES: DiscoverCity[] = [
  { name: 'Bengaluru', localities: ['Koramangala', 'HSR Layout', 'Indiranagar', 'Whitefield', 'Bellandur', 'Marathahalli', 'Electronic City', 'Jayanagar', 'JP Nagar', 'BTM Layout', 'MG Road', 'Hebbal', 'Manyata Tech Park', 'Yeshwanthpur', 'Malleshwaram', 'Sarjapur Road', 'Domlur', 'Rajajinagar'] },
  { name: 'Hyderabad', localities: ['HITEC City', 'Gachibowli', 'Madhapur', 'Kondapur', 'Financial District', 'Banjara Hills', 'Jubilee Hills', 'Begumpet', 'Kukatpally', 'Secunderabad'] },
  { name: 'Pune', localities: ['Hinjewadi', 'Kharadi', 'Viman Nagar', 'Baner', 'Koregaon Park', 'Magarpatta', 'Hadapsar', 'Kothrud', 'Wakad', 'Aundh', 'Yerawada', 'Shivajinagar'] },
  { name: 'Chennai', localities: ['Sholinganallur', 'Perungudi', 'Guindy', 'Velachery', 'T. Nagar', 'Adyar', 'Anna Nagar', 'Nungambakkam', 'Ambattur'] },
  { name: 'Mumbai', localities: ['Andheri', 'Bandra Kurla Complex', 'Lower Parel', 'Powai', 'Goregaon', 'Malad', 'Vikhroli', 'Worli', 'Thane', 'Navi Mumbai'] },
  { name: 'Delhi NCR', localities: ['Connaught Place', 'Saket', 'Okhla', 'Nehru Place', 'Aerocity', 'Dwarka', 'Gurugram Cyber City', 'Noida Sector 62', 'Noida Sector 18'] },
  { name: 'Kolkata', localities: ['Salt Lake Sector V', 'New Town', 'Park Street', 'Ballygunge'] },
  { name: 'Ahmedabad', localities: ['SG Highway', 'Prahlad Nagar', 'Navrangpura', 'GIFT City'] },
  { name: 'Coimbatore', localities: ['Peelamedu', 'RS Puram', 'Saravanampatti', 'Gandhipuram'] },
  { name: 'Kochi', localities: ['Kakkanad', 'Edappally', 'Vyttila'] },
  { name: 'Thiruvananthapuram', localities: ['Technopark', 'Kazhakoottam'] },
  { name: 'Jaipur', localities: ['Malviya Nagar', 'Sitapura', 'C-Scheme'] },
  { name: 'Chandigarh', localities: ['IT Park', 'Sector 17', 'Mohali'] },
  { name: 'Indore', localities: ['Vijay Nagar'] },
];

/* ───────────────────────── one listing ───────────────────────── */

interface Spec {
  id: string;
  title: string;
  employer: string;
  /** Where the apply link goes. */
  host: string;
  source?: DiscoverSource;
  city: string;
  locality?: string;
  /** Straight-line km from the person's area; absent when either end is only known to the city. */
  km?: number;
  mode: DiscoverWorkMode;
  word: QuickFitWord;
  named: number;
  matched: number;
  notFound: string[];
  yearsAsked?: number;
  yearsYou?: number;
  reasons: Reason[];
  /** Days since the employer posted it; absent = the employer gives no date. */
  days?: number;
  salary?: string;
  /** Which group it lives in. */
  group: string;
  /** 0 = reads like the work they do now, 1 = a next step, 2 = a stretch. */
  lvl: 0 | 1 | 2;
  /** Also reads as the work they do now (shown under "Roles like yours" when the lens is narrowest). */
  cur?: boolean;
  also?: string[];
  checkedDays?: number;
}

const slug = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');

function cardOf(s: Spec, area?: DiscoverArea): ListingCard {
  const checked = s.checkedDays ?? 0;
  const label = s.city === '' ? 'Remote, India' : s.locality ? `${s.locality}, ${s.city}` : s.city;
  return {
    id: s.id,
    title: s.title,
    employer: s.employer,
    employerId: slug(s.employer),
    source: s.source ?? (s.host.includes('lever') ? 'lever' : 'greenhouse'),
    place: {
      label,
      ...(s.city ? { city: s.city } : {}),
      ...(s.locality ? { locality: s.locality } : {}),
      precision: s.city === '' ? 'country' : s.locality ? 'locality' : 'city',
      ...(s.km !== undefined && area?.locality ? { km: s.km } : {}),
    },
    workMode: s.mode,
    ...(s.days !== undefined ? { postedAt: ago(s.days) } : {}),
    firstSeenAt: ago(s.days ?? 12),
    checkedAt: ago(checked),
    freshness: freshnessOf(s.days),
    applyUrl: `https://${s.host}/${slug(s.employer)}/${s.id}`,
    applyHost: s.host,
    confidence: 'employer',
    sourceLabel: `Employer's own listing, checked ${checkedPhrase(checked)}`,
    ...(s.salary ? { salary: { text: s.salary } } : {}),
    fit: {
      word: s.word,
      named: s.named,
      matched: s.matched,
      notFound: s.notFound,
      ...(s.yearsAsked !== undefined ? { yearsAsked: s.yearsAsked } : {}),
      ...(s.yearsYou !== undefined ? { yearsYou: s.yearsYou } : {}),
      reasons: s.reasons,
    },
    groupId: s.group,
    alsoFits: s.also ?? [],
  };
}

/* ───────────────────────── Riya, Bengaluru ───────────────────────── */

const cite = (source: string, text: string) => ({ source, text });
const R = {
  react: { text: 'You show React and TypeScript.', cite: cite('resume', 'lines 9–12') },
  react1: { text: 'You show React.', cite: cite('resume', 'line 9') },
  ts: { text: 'You show TypeScript.', cite: cite('resume', 'line 10') },
  ds: { text: 'You show design systems work.', cite: cite('GitHub', 'riya/ui-kit') },
  css: { text: 'You show CSS in your ui-kit.', cite: cite('GitHub', 'riya/ui-kit') },
  jest: { text: 'You show Jest testing.', cite: cite('resume', 'line 14') },
  a11y: { text: 'You show accessibility work.', cite: cite('resume', 'line 16') },
  node: { text: 'You mention Node once.', cite: cite('resume', 'line 18') },
} satisfies Record<string, Reason>;

const SFE = 'next_role:senior-frontend-engineer';
const NODE = 'skill_bridge:node-js';
const DE = 'next_role:design-engineer';
const CUR = 'current';

/** Rough straight-line km from HSR Layout to each locality. */
const KM: Record<string, number> = {
  'HSR Layout': 1,
  'BTM Layout': 3,
  Koramangala: 4,
  'Sarjapur Road': 4,
  Bellandur: 5,
  Jayanagar: 6,
  'JP Nagar': 8,
  Domlur: 8,
  Indiranagar: 8,
  'MG Road': 9,
  Marathahalli: 9,
  'Electronic City': 10.4,
  Malleshwaram: 13,
  Yeshwanthpur: 14,
  Whitefield: 16,
  'Manyata Tech Park': 17,
  Hebbal: 19,
};

type Row = [id: string, title: string, employer: string, host: string, locality: string | undefined, mode: DiscoverWorkMode, word: QuickFitWord, named: number, matched: number, notFound: string[], reasons: Reason[], group: string, lvl: 0 | 1 | 2, days: number | undefined, extra?: Partial<Spec>];

const LEVER = 'jobs.lever.co';
const GH = 'job-boards.greenhouse.io';

const ROWS: Row[] = [
  // within 3 km
  ['j2', 'Frontend Engineer II', 'Northwind Analytics', LEVER, 'HSR Layout', 'onsite', 'looks_close', 7, 6, ['D3'], [R.react1, R.jest], SFE, 0, 9, { cur: true, salary: '₹28–36 lakh a year' }],
  ['j3', 'Senior Frontend Engineer, Mobile web', 'Quill & Co', 'quillandco.in', 'HSR Layout', 'hybrid', 'worth_a_look', 8, 5, ['React Native', 'Swift', 'Kotlin'], [R.a11y, R.css], SFE, 0, 30, {}],
  ['d1', 'Design Engineer', 'Lotus Ledger', GH, 'HSR Layout', 'hybrid', 'looks_close', 6, 5, ['Framer Motion'], [R.a11y, R.css], DE, 1, 3, {}],
  ['j11', 'Frontend Engineer', 'Cinder Cloud', GH, 'BTM Layout', 'hybrid', 'looks_close', 8, 7, ['Vue'], [R.react, R.ts], SFE, 0, 2, { cur: true }],
  // 3 to 5 km
  ['j1', 'Senior Frontend Engineer', 'Kestrel Pay', 'kestrelpay.in', 'Koramangala', 'hybrid', 'looks_close', 9, 7, ['GraphQL', 'Relay'], [R.react, R.ds], SFE, 0, 3, { cur: true }],
  ['j6', 'Product Engineer', 'Saffron Logistics', LEVER, 'Koramangala', 'onsite', 'worth_a_look', 9, 5, ['AWS', 'PostgreSQL', 'Docker', 'Terraform'], [R.ts, R.react1], NODE, 1, 5, { salary: '₹22–30 lakh a year' }],
  ['j12', 'Senior Frontend Engineer', 'Marigold Mobility', GH, 'Koramangala', 'hybrid', 'worth_a_look', 10, 6, ['Next.js', 'Storybook', 'GraphQL'], [R.react, R.jest], SFE, 0, 12, {}],
  ['f1', 'Full-stack Engineer', 'Fernhill Studio', 'fernhillstudio.in', 'Sarjapur Road', 'hybrid', 'worth_a_look', 10, 6, ['Node.js in production', 'PostgreSQL', 'Redis'], [R.react1, R.node], NODE, 1, 8, {}],
  ['j5', 'Full-stack Engineer', 'Tidepool Health', 'jobs.ashbyhq.com', 'Bellandur', 'hybrid', 'worth_a_look', 11, 6, ['Node.js in production', 'PostgreSQL', 'HL7', 'AWS'], [R.react1, R.node], NODE, 1, 14, { source: 'greenhouse', also: [SFE] }],
  // 5 to 10 km
  ['j13', 'Senior Frontend Engineer', 'Orchid Systems', GH, 'Jayanagar', 'onsite', 'looks_close', 7, 6, ['Angular'], [R.react, R.ts], SFE, 0, 6, {}],
  ['d2', 'Design Engineer', 'Pixel Pier', LEVER, 'Jayanagar', 'hybrid', 'worth_a_look', 8, 5, ['Figma plugins', 'WebGL', 'Three.js'], [R.css, R.a11y], DE, 1, 20, {}],
  ['f2', 'Full-stack Engineer', 'Basil Bio', GH, 'JP Nagar', 'onsite', 'worth_a_look', 9, 5, ['Python', 'Django', 'PostgreSQL', 'AWS'], [R.react1, R.ts], NODE, 1, 33, {}],
  ['j14', 'UI Engineer', 'Kite Commerce', GH, 'Domlur', 'hybrid', 'looks_close', 7, 6, ['Storybook'], [R.react, R.jest], SFE, 0, 1, {}],
  ['j4', 'Senior UI Engineer', 'Banyan Bank', 'careers.banyanbank.in', 'Indiranagar', 'hybrid', 'worth_a_look', 10, 6, ['Angular', 'RxJS', 'Java'], [R.ts, R.react1], SFE, 0, 21, { source: 'greenhouse' }],
  ['j7', 'Design Engineer', 'Monsoon Labs', 'monsoonlabs.com', 'Indiranagar', 'hybrid', 'looks_close', 6, 5, ['Figma plugins'], [R.a11y, R.css], DE, 1, 6, { source: 'greenhouse' }],
  ['f3', 'Full-stack Engineer', 'Juniper Maps', LEVER, 'Indiranagar', 'hybrid', 'worth_a_look', 10, 5, ['Go', 'PostGIS', 'Kafka', 'AWS'], [R.react1, R.ts], NODE, 1, 4, { salary: '₹30–42 lakh a year' }],
  ['j15', 'Frontend Engineer', 'Sable Security', GH, 'Indiranagar', 'hybrid', 'looks_close', 8, 6, ['WebAssembly', 'Rust'], [R.react, R.jest], SFE, 0, 9, {}],
  ['j16', 'Senior Frontend Engineer', 'Willow Works', 'willowworks.io', 'MG Road', 'onsite', 'worth_a_look', 9, 5, ['Next.js', 'GraphQL', 'Cypress'], [R.react, R.ts], SFE, 0, 38, { cur: true }],
  ['f4', 'Software Engineer, Web', 'Ember Games', GH, 'MG Road', 'hybrid', 'worth_a_look', 11, 6, ['Node.js in production', 'WebSockets', 'Redis', 'AWS'], [R.react1, R.node], NODE, 1, 15, {}],
  ['j17', 'Frontend Engineer', 'Atlas Apps', LEVER, 'Marathahalli', 'hybrid', 'looks_close', 7, 6, ['Redux Saga'], [R.react, R.jest], SFE, 0, 7, {}],
  ['f5', 'Full-stack Engineer', 'Nimbus Notes', GH, 'Marathahalli', 'hybrid', 'worth_a_look', 9, 5, ['Node.js in production', 'GraphQL', 'MongoDB'], [R.react1, R.node], NODE, 1, 10, {}],
  ['d3', 'Design Engineer, Systems', 'Granite Grid', GH, 'Marathahalli', 'hybrid', 'looks_close', 6, 5, ['Tokens Studio'], [R.ds, R.a11y], DE, 1, 11, {}],
  // 10 to 25 km
  ['d4', 'Design Engineer', 'Aster Insure', 'asterinsure.in', 'Electronic City', 'hybrid', 'worth_a_look', 7, 4, ['Figma plugins', 'Lottie', 'Sketch'], [R.css, R.a11y], DE, 1, 26, { source: 'greenhouse' }],
  ['j18', 'Senior Frontend Engineer', 'Pebble Pay', GH, 'Malleshwaram', 'hybrid', 'looks_close', 8, 7, ['GraphQL'], [R.react, R.ds], SFE, 0, 2, { cur: true }],
  ['f6', 'Full-stack Engineer', 'Cobalt Commerce', LEVER, 'Yeshwanthpur', 'onsite', 'worth_a_look', 10, 5, ['Java', 'Spring', 'Kafka', 'Kubernetes'], [R.react1, R.ts], NODE, 1, 18, {}],
  ['j9', 'UI Engineer', 'Indigo Grid', 'indigogrid.in', 'Whitefield', 'hybrid', 'looks_close', 7, 6, ['Storybook'], [R.react, R.jest], SFE, 0, 1, { source: 'greenhouse' }],
  ['j19', 'Senior Frontend Engineer', 'Drift Dock', GH, 'Whitefield', 'onsite', 'worth_a_look', 9, 5, ['Next.js', 'GraphQL', 'Jenkins'], [R.react, R.ts], SFE, 0, 44, {}],
  ['d5', 'Design Engineer', 'Ivory Edu', GH, 'Whitefield', 'hybrid', 'looks_close', 6, 5, ['Figma plugins'], [R.a11y, R.css], DE, 1, 5, {}],
  ['f7', 'Full-stack Engineer', 'Maple Mobility', GH, 'Manyata Tech Park', 'onsite', 'worth_a_look', 10, 5, ['Node.js in production', 'PostgreSQL', 'AWS', 'Docker'], [R.react1, R.node], NODE, 1, 52, {}],
  ['j20', 'Frontend Engineer', 'Teal Telehealth', LEVER, 'Hebbal', 'hybrid', 'looks_close', 8, 6, ['Next.js'], [R.react, R.ts], SFE, 0, 3, {}],
  // the city only: no locality, so no distance
  ['j21', 'Senior Frontend Engineer', 'Rivet Robotics', GH, undefined, 'unknown', 'looks_close', 8, 6, ['Three.js'], [R.react, R.ds], SFE, 0, 4, {}],
  ['f8', 'Full-stack Engineer', 'Sundial Data', GH, undefined, 'unknown', 'worth_a_look', 9, 5, ['Python', 'Airflow', 'AWS', 'Node.js in production'], [R.react1, R.node], NODE, 1, 27, {}],
  ['d6', 'Design Engineer', 'Tern Travel', LEVER, undefined, 'unknown', 'worth_a_look', 7, 4, ['Figma plugins', 'Lottie', 'Rive'], [R.css, R.a11y], DE, 1, undefined, {}],
  // stretches
  ['j10', 'Staff Frontend Engineer', 'Kestrel Pay', 'kestrelpay.in', 'Koramangala', 'hybrid', 'stretch', 8, 6, ['GraphQL'], [R.react, R.ds], SFE, 2, 3, { yearsAsked: 8, yearsYou: 2.4 }],
  ['j8', 'Full-stack Engineer', 'Parcelpoint', 'parcelpoint.in', 'Electronic City', 'onsite', 'stretch', 12, 4, ['Go', 'Kubernetes', 'AWS', 'Terraform', 'gRPC'], [R.react1, R.ts], NODE, 2, 70, { source: 'greenhouse' }],
  ['j22', 'Lead Frontend Architect', 'Umber Ads', GH, 'Whitefield', 'hybrid', 'stretch', 9, 6, ['Micro-frontends', 'Module Federation'], [R.react, R.ds], SFE, 2, 6, { yearsAsked: 9, yearsYou: 2.4 }],
];

const SPECS: Spec[] = ROWS.map(([id, title, employer, host, locality, mode, word, named, matched, notFound, reasons, group, lvl, days, extra]) => ({
  id,
  title,
  employer,
  host,
  city: 'Bengaluru',
  locality,
  ...(locality ? { km: KM[locality] } : {}),
  mode,
  word,
  named,
  matched,
  notFound,
  reasons,
  group,
  lvl,
  days,
  ...extra,
}));

const REMOTE_SPECS: Spec[] = (
  [
    ['r1', 'Frontend Engineer (remote, India)', 'Lattice & Loom', LEVER, 'looks_close', 8, 6, ['Next.js in production'], [R.react, R.css], SFE, 0, 4, { salary: '$40–55k a year' }],
    ['r2', 'Senior Frontend Engineer', 'Orbit Docs', GH, 'looks_close', 9, 7, ['GraphQL', 'Relay'], [R.react, R.ds], SFE, 0, 2, {}],
    ['r3', 'React Engineer', 'Fathom Finance', GH, 'worth_a_look', 8, 5, ['Next.js', 'Stripe', 'Playwright'], [R.react, R.jest], SFE, 0, 11, {}],
    ['r4', 'Design Engineer', 'Glasshouse', LEVER, 'looks_close', 6, 5, ['Figma plugins'], [R.a11y, R.css], DE, 1, 6, { salary: '$45–60k a year' }],
    ['r5', 'Full-stack Engineer', 'Plover Health', GH, 'worth_a_look', 10, 5, ['Node.js in production', 'PostgreSQL', 'AWS'], [R.react1, R.node], NODE, 1, 19, {}],
    ['r6', 'Frontend Developer', 'Quarry Learning', GH, 'worth_a_look', 7, 4, ['Vue', 'Nuxt', 'Sass'], [R.css, R.react1], SFE, 0, 33, {}],
  ] as [string, string, string, string, QuickFitWord, number, number, string[], Reason[], string, 0 | 1 | 2, number, Partial<Spec>][]
).map(([id, title, employer, host, word, named, matched, notFound, reasons, group, lvl, days, extra]) => ({
  id,
  title,
  employer,
  host,
  city: '',
  mode: 'remote' as const,
  word,
  named,
  matched,
  notFound,
  reasons,
  group,
  lvl,
  days,
  ...extra,
}));

const CITY_SPECS: Spec[] = [
  { id: 'p1', title: 'Senior Frontend Engineer', employer: 'Lumen Retail', host: GH, city: 'Pune', locality: 'Kharadi', mode: 'hybrid', word: 'looks_close', named: 8, matched: 7, notFound: ['GraphQL'], reasons: [R.react, R.ds], group: SFE, lvl: 0, days: 3 },
  { id: 'p2', title: 'Frontend Engineer', employer: 'Cedar Cloud', host: LEVER, city: 'Pune', locality: 'Hinjewadi', mode: 'onsite', word: 'looks_close', named: 7, matched: 6, notFound: ['Vue'], reasons: [R.react, R.jest], group: SFE, lvl: 0, days: 9 },
  { id: 'p3', title: 'Design Engineer', employer: 'Harbourline', host: GH, city: 'Pune', locality: 'Baner', mode: 'hybrid', word: 'looks_close', named: 6, matched: 5, notFound: ['Lottie'], reasons: [R.a11y, R.css], group: DE, lvl: 1, days: 5 },
];

const GROUP_META: Record<string, { kind: Group['kind']; title: string; reason: (n: number) => Reason }> = {
  [SFE]: { kind: 'next_role', title: 'Senior frontend engineer', reason: () => ({ text: 'Your next step. React and TypeScript show up in 3 of your projects.', cite: cite('resume', 'lines 9–12') }) },
  [NODE]: { kind: 'skill_bridge', title: 'One skill away: Node.js', reason: (n) => ({ text: `Node.js shows up in ${n} of these. Your evidence doesn't show it yet.` }) },
  [DE]: { kind: 'next_role', title: 'Design engineer', reason: () => ({ text: 'Your next step. Your accessibility audit and CSS work match what these ask for.', cite: cite('resume', 'line 16') }) },
  [CUR]: { kind: 'current', title: 'Roles like yours', reason: () => ({ text: 'The work you do now.', cite: cite('resume', 'lines 9–12') }) },
};
const GROUP_ORDER = [SFE, DE, NODE, CUR];

const FIT_RANK: Record<QuickFitWord, number> = { looks_close: 0, worth_a_look: 1, stretch: 2, cant_tell: 3 };
const BREADTH_ORDER = ['role', 'next', 'stretch'] as const;
const RIYA_AREA: DiscoverArea = { city: 'Bengaluru', cityFrom: 'resume', resumeText: 'Bengaluru, Karnataka', locality: 'HSR Layout' };

const RIYA_COMPANIES: CompanyLead[] = [
  { employerId: 'indigo-grid', name: 'Indigo Grid', city: 'Whitefield', reason: 'Hiring 4 frontend roles in Bengaluru, nothing near you now.', careersUrl: 'https://indigogrid.in/careers' },
  { employerId: 'quill-and-co', name: 'Quill & Co', city: 'HSR Layout', reason: 'Hiring 2 design engineer roles in Bengaluru, nothing near you now.', careersUrl: 'https://quillandco.in/jobs' },
  { employerId: 'parcelpoint', name: 'Parcelpoint', city: 'Electronic City', reason: 'Hiring 3 full-stack roles in Bengaluru, nothing near you now.', careersUrl: 'https://parcelpoint.in/careers' },
];

const riyaSignals = (area: DiscoverArea) => [
  'Your next roles: Senior frontend engineer, Full-stack engineer, Design engineer',
  'Skills the postings name that your evidence shows',
  'Years asked against your 2.4',
  ...(area.locality ? [`Distance from ${area.locality}, straight line`] : []),
  'How recently the employer listed it',
  "Only employers' own job boards",
];

type Lens = Pick<DiscoverQuery, 'breadth' | 'lane' | 'order'> & { reach?: DiscoverQuery['reach']; otherCity?: string; locality?: string; city?: string };

/** Riya's Bengaluru answer for any lens: filtered, grouped, ordered and counted from the table above. */
export function riyaResponse(lens: Lens): DiscoverResponse {
  const area: DiscoverArea = { ...RIYA_AREA, ...(lens.city && lens.city !== RIYA_AREA.city ? { city: lens.city, cityFrom: 'you' as const } : {}), ...('locality' in lens ? { locality: lens.locality } : {}) };
  const reach = effectiveReach({ ...lens, tab: 'roles', locality: area.locality } as DiscoverQuery);
  const km = (r: string) => (r === 'city' ? Infinity : Number(r));
  const bi = BREADTH_ORDER.indexOf(lens.breadth);

  const inBreadth = (s: Spec, b: number) => (b === 0 ? !!s.cur && s.lvl === 0 : s.lvl <= b);
  const nearAt = (r: string, b: number) => SPECS.filter((s) => inBreadth(s, b) && (r === 'city' || (s.km !== undefined && !!area.locality && s.km <= km(r))));
  const remoteAt = (b: number) => REMOTE_SPECS.filter((s) => inBreadth(s, b));
  const cityAt = () => (lens.otherCity ? CITY_SPECS : []);

  const pool = lens.lane === 'near' ? nearAt(reach, bi) : lens.lane === 'remote' ? remoteAt(bi) : cityAt();
  const homeOf = (s: Spec) => (bi === 0 && lens.lane !== 'city' ? CUR : s.group);

  const sorted = [...pool].sort((a, b) => {
    if (lens.order === 'posted') return (a.days ?? 999) - (b.days ?? 999);
    return FIT_RANK[a.word] - FIT_RANK[b.word] || GROUP_ORDER.indexOf(homeOf(a)) - GROUP_ORDER.indexOf(homeOf(b)) || (a.km ?? 99) - (b.km ?? 99) || (a.days ?? 999) - (b.days ?? 999) || a.title.localeCompare(b.title);
  });

  const listings = sorted.map((s) => {
    const home = homeOf(s);
    const card = cardOf({ ...s, group: home }, area);
    return { ...card, alsoFits: (s.also ?? []).filter((g) => pool.some((p) => homeOf(p) === g)) };
  });

  const groups: Group[] = GROUP_ORDER.flatMap((gid) => {
    const members = listings.filter((l) => l.groupId === gid);
    if (members.length === 0) return [];
    const meta = GROUP_META[gid];
    return [{ id: gid, kind: meta.kind, title: meta.title, count: members.length, reason: meta.reason(members.length), listingIds: members.map((m) => m.id) }];
  });

  const reachStops: LensStop[] = [
    ['3', '3 km'],
    ['5', '5 km'],
    ['10', '10 km'],
    ['25', '25 km'],
    ['city', 'Whole city'],
  ].map(([value, label]) => ({ value, label, count: nearAt(value, bi).length }));
  const breadthStops: LensStop[] = [
    ['role', 'Your role'],
    ['next', '+ next steps'],
    ['stretch', '+ stretch'],
  ].map(([value, label], i) => ({ value, label, count: lens.lane === 'near' ? nearAt(reach, i).length : lens.lane === 'remote' ? remoteAt(i).length : cityAt().length }));

  let suggestion: string | undefined;
  if (lens.lane === 'near') {
    const at = REACH_ORDER.indexOf(reach);
    const nextReach = REACH_ORDER[at + 1];
    if (nextReach) {
      const added = nearAt(nextReach, bi).filter((s) => !pool.includes(s));
      if (added.length > 0) {
        const top = new Map<string, number>();
        for (const s of added) top.set(homeOf(s), (top.get(homeOf(s)) ?? 0) + 1);
        const [gid, n] = [...top.entries()].sort((a, b) => b[1] - a[1])[0];
        suggestion = nextReach === 'city' ? `The whole city adds ${added.length} more.` : `Widening to ${nextReach} km adds ${added.length} ${added.length === 1 ? 'role' : 'roles'}, ${n} in ${GROUP_META[gid].title}.`;
      }
    }
  }

  const nearTotal = nearAt(reach, bi).length;
  const coverage: DiscoverCoverage = {
    employers: 38,
    listings: 412,
    ...(nearTotal < 5 && lens.lane === 'near'
      ? { note: "Few roles like yours are shared with us yet. We read employers' own job boards, which lean toward tech and startups. Found a posting somewhere else? Paste it and we'll match it." }
      : {}),
  };

  return {
    area,
    lane: lens.lane,
    reach,
    breadth: lens.breadth,
    order: lens.order,
    total: listings.length,
    reachStops,
    breadthStops,
    laneCounts: { near: nearTotal, remote: remoteAt(bi).length, city: cityAt().length },
    ...(suggestion ? { suggestion } : {}),
    groups,
    listings,
    companies: lens.lane === 'near' ? RIYA_COMPANIES : [],
    coverage,
    signals: riyaSignals(area),
    refreshedAt: ago(0, 3),
  };
}

/* ───────────────────────── the other states ───────────────────────── */

const EMPTY_STOPS = (counts: number[]): LensStop[] => [
  { value: '3', label: '3 km', count: counts[0] },
  { value: '5', label: '5 km', count: counts[1] },
  { value: '10', label: '10 km', count: counts[2] },
  { value: '25', label: '25 km', count: counts[3] },
  { value: 'city', label: 'Whole city', count: counts[4] },
];
const BREADTH_STOPS = (counts: number[]): LensStop[] => [
  { value: 'role', label: 'Your role', count: counts[0] },
  { value: 'next', label: '+ next steps', count: counts[1] },
  { value: 'stretch', label: '+ stretch', count: counts[2] },
];

const BASE_COVERAGE: DiscoverCoverage = { employers: 38, listings: 412 };

/** The employers' boards have never been read: nothing to show, and the server says so. */
export const firstRefreshResponse = (): DiscoverResponse => ({
  area: { city: 'Bengaluru', cityFrom: 'resume', resumeText: 'Bengaluru, Karnataka', locality: 'HSR Layout' },
  lane: 'near',
  reach: '10',
  breadth: 'next',
  order: 'ranked',
  total: 0,
  reachStops: EMPTY_STOPS([0, 0, 0, 0, 0]),
  breadthStops: BREADTH_STOPS([0, 0, 0]),
  laneCounts: { near: 0, remote: 0, city: 0 },
  groups: [],
  listings: [],
  companies: [],
  coverage: { employers: 0, listings: 0, note: "Reading employers' job boards for the first time. This takes about a minute." },
  signals: [],
});

/** A resume that names a place we do not read job boards for yet (Aarav, Nagpur). */
export const noCityResponse = (): DiscoverResponse => ({
  ...firstRefreshResponse(),
  area: { cityFrom: 'none', resumeText: 'Nagpur, Maharashtra' },
  reach: 'city',
  coverage: { ...BASE_COVERAGE, note: "Pick a city and we'll show roles near you." },
  refreshedAt: ago(0, 3),
});

/** Vikram, Pune, Kothrud: nothing within 3 km; the wider stops and remote say what is there. */
export const emptyNearResponse = (): DiscoverResponse => ({
  area: { city: 'Pune', cityFrom: 'resume', resumeText: 'Pune, Maharashtra', locality: 'Kothrud' },
  lane: 'near',
  reach: '3',
  breadth: 'next',
  order: 'ranked',
  total: 0,
  reachStops: EMPTY_STOPS([0, 1, 4, 9, 14]),
  breadthStops: BREADTH_STOPS([0, 0, 1]),
  laneCounts: { near: 0, remote: 2, city: 0 },
  suggestion: 'Widening to 5 km adds 1 role, 1 in Instructional designer.',
  groups: [],
  listings: [],
  companies: [
    { employerId: 'lantern-learning', name: 'Lantern Learning', city: 'Baner', reason: 'Hiring 3 instructional design roles in Pune, nothing near you now.', careersUrl: 'https://lanternlearning.in/careers' },
    { employerId: 'schoolbridge', name: 'Schoolbridge', city: 'Hinjewadi', reason: 'Hiring 2 curriculum roles in Pune, nothing near you now.', careersUrl: 'https://jobs.lever.co/schoolbridge' },
  ],
  coverage: { employers: 38, listings: 412, note: "Few roles like yours are shared with us yet. We read employers' own job boards, which lean toward tech and startups. Found a posting somewhere else? Paste it and we'll match it." },
  signals: ['Your next roles: Instructional designer, Learning and development associate', 'Skills the postings name that your evidence shows', 'Years asked against your 6', 'Distance from Kothrud, straight line', 'How recently the employer listed it', "Only employers' own job boards"],
  refreshedAt: ago(0, 3),
});

/** Riya, breadth "+ next steps": nothing near fits well; 14 are a stretch. */
export const allWeakResponse = (): DiscoverResponse => ({
  ...riyaResponse({ breadth: 'next', lane: 'near', order: 'ranked', reach: '3' }),
  reach: '10',
  total: 0,
  groups: [],
  listings: [],
  reachStops: EMPTY_STOPS([0, 0, 0, 0, 0]),
  breadthStops: BREADTH_STOPS([0, 0, 14]),
  laneCounts: { near: 0, remote: 2, city: 0 },
  suggestion: undefined,
  companies: [],
});

/** The same person after "Show them anyway": five listings, all a stretch. */
export const allWeakShownResponse = (): DiscoverResponse => {
  const weak: Spec[] = [
    { id: 'w1', title: 'Staff Frontend Engineer', employer: 'Kestrel Pay', host: 'kestrelpay.in', city: 'Bengaluru', locality: 'Koramangala', km: 4, mode: 'hybrid', word: 'stretch', named: 8, matched: 6, notFound: ['GraphQL', 'AWS'], yearsAsked: 8, yearsYou: 2.4, reasons: [R.react, R.ds], group: SFE, lvl: 2, days: 3 },
    { id: 'w2', title: 'Platform Engineer', employer: 'Parcelpoint', host: 'parcelpoint.in', city: 'Bengaluru', locality: 'Electronic City', km: 10, mode: 'onsite', word: 'stretch', named: 12, matched: 3, notFound: ['Go', 'Kubernetes', 'AWS', 'Terraform'], reasons: [R.ts, R.react1], group: SFE, lvl: 2, days: 70 },
    { id: 'w3', title: 'Principal UI Engineer', employer: 'Banyan Bank', host: 'careers.banyanbank.in', city: 'Bengaluru', locality: 'Indiranagar', km: 8, mode: 'hybrid', word: 'stretch', named: 9, matched: 5, notFound: ['GraphQL', 'AWS', 'Angular'], yearsAsked: 10, yearsYou: 2.4, reasons: [R.ts, R.react1], group: SFE, lvl: 2, days: 21 },
    { id: 'w4', title: 'Engineering Manager, Web', employer: 'Cinder Cloud', host: GH, city: 'Bengaluru', locality: 'BTM Layout', km: 3, mode: 'hybrid', word: 'stretch', named: 7, matched: 3, notFound: ['People management', 'AWS', 'GraphQL'], yearsAsked: 7, yearsYou: 2.4, reasons: [R.react1, R.jest], group: SFE, lvl: 2, days: 9 },
    { id: 'w5', title: 'Senior Full-stack Engineer', employer: 'Saffron Logistics', host: LEVER, city: 'Bengaluru', locality: 'Koramangala', km: 4, mode: 'onsite', word: 'stretch', named: 11, matched: 4, notFound: ['AWS', 'PostgreSQL', 'Kafka'], reasons: [R.ts, R.node], group: NODE, lvl: 2, days: 5 },
  ];
  const listings = weak.map((s) => cardOf(s, RIYA_AREA));
  const base = firstRefreshResponse();
  return {
    ...base,
    area: RIYA_AREA,
    reach: '10',
    breadth: 'stretch',
    total: listings.length,
    reachStops: EMPTY_STOPS([1, 5, 5, 5, 5]),
    breadthStops: BREADTH_STOPS([0, 0, 5]),
    laneCounts: { near: 5, remote: 2, city: 0 },
    groups: [
      { id: SFE, kind: 'next_role', title: 'Senior frontend engineer', count: 4, reason: GROUP_META[SFE].reason(4), listingIds: ['w1', 'w2', 'w3', 'w4'] },
      { id: NODE, kind: 'skill_bridge', title: 'One skill away: Node.js', count: 1, reason: GROUP_META[NODE].reason(1), listingIds: ['w5'] },
    ],
    listings,
    coverage: BASE_COVERAGE,
    signals: riyaSignals(RIYA_AREA),
    refreshedAt: ago(0, 3),
  };
};

/** Aarav, a final-year student with one page of resume: most words read "Can't tell yet". */
export const thinResponse = (): DiscoverResponse => {
  const area: DiscoverArea = { city: 'Pune', cityFrom: 'you', locality: 'Kharadi' };
  const mk = (id: string, title: string, employer: string, host: string, locality: string, km: number, named: number, matched: number, word: QuickFitWord, days: number): Spec => ({
    id,
    title,
    employer,
    host,
    city: 'Pune',
    locality,
    km,
    mode: 'onsite',
    word,
    named,
    matched,
    notFound: [],
    reasons: matched > 0 ? [{ text: 'You mention Python.', cite: cite('resume', 'line 6') }] : [],
    group: 'first_steps',
    lvl: 0,
    days,
  });
  const specs = [
    mk('t1', 'Software Trainee', 'Zinc Softworks', 'zincsoftworks.in', 'Kharadi', 3, 6, 2, 'cant_tell', 4),
    mk('t2', 'Graduate Engineer Trainee', 'Alder Systems', GH, 'Viman Nagar', 4, 2, 1, 'cant_tell', 8),
    mk('t3', 'Junior Python Developer', 'Birch Data', LEVER, 'Magarpatta', 6, 5, 2, 'cant_tell', 2),
    mk('t4', 'QA Trainee', 'Cypress Quality', GH, 'Hadapsar', 7, 4, 1, 'cant_tell', 15),
    mk('t5', 'Associate Software Engineer', 'Dune Digital', GH, 'Hinjewadi', 18, 7, 2, 'worth_a_look', 6),
    mk('t6', 'Support Engineer', 'Elm Cloud', LEVER, 'Baner', 12, 3, 0, 'cant_tell', 24),
  ].map((s) => ({ ...s, notFound: s.matched === 0 ? ['Linux'] : [] }));
  const listings = specs.map((s) => cardOf(s, area));
  return {
    area,
    lane: 'near',
    reach: '25',
    breadth: 'next',
    order: 'ranked',
    total: listings.length,
    reachStops: EMPTY_STOPS([1, 2, 4, 6, 6]),
    breadthStops: BREADTH_STOPS([0, 6, 6]),
    laneCounts: { near: 6, remote: 3, city: 0 },
    groups: [{ id: 'first_steps', kind: 'first_steps', title: 'First steps', count: 6, reason: { text: 'Entry roles that ask for what your evidence shows.' }, listingIds: listings.map((l) => l.id) }],
    listings,
    companies: [],
    coverage: BASE_COVERAGE,
    signals: ['Your next roles: Software engineer, Data analyst', 'Skills the postings name that your evidence shows', 'Distance from Kharadi, straight line', 'How recently the employer listed it', "Only employers' own job boards"],
    refreshedAt: ago(0, 3),
  };
};

/** Kavya, B.Com 2026, Coimbatore: three roles, and the honest note about coverage. */
export const coimbatoreResponse = (): DiscoverResponse => {
  const area: DiscoverArea = { city: 'Coimbatore', cityFrom: 'resume', resumeText: 'Coimbatore, Tamil Nadu', locality: 'Peelamedu' };
  const base: Omit<Spec, 'id' | 'title' | 'employer' | 'host' | 'locality' | 'km' | 'named' | 'matched' | 'notFound' | 'reasons' | 'days'> = { city: 'Coimbatore', mode: 'onsite', word: 'worth_a_look', group: 'first_steps', lvl: 0 };
  const specs: Spec[] = [
    { ...base, id: 'k1', title: 'Accounts Trainee', employer: 'Nilgiri Mills', host: 'nilgirimills.in', locality: 'Peelamedu', km: 2, named: 6, matched: 4, notFound: ['Tally Prime', 'GST filing'], reasons: [{ text: 'You show financial accounting.', cite: cite('resume', 'line 7') }, { text: 'You show Excel.', cite: cite('resume', 'line 11') }], days: 4, word: 'looks_close' },
    { ...base, id: 'k2', title: 'Junior Accountant', employer: 'Kovai Auto Parts', host: LEVER, locality: 'Saravanampatti', km: 8, named: 7, matched: 4, notFound: ['Tally Prime', 'TDS', 'GST filing'], reasons: [{ text: 'You show financial accounting.', cite: cite('resume', 'line 7') }, { text: 'You show Excel.', cite: cite('resume', 'line 11') }], days: 9 },
    { ...base, id: 'k3', title: 'Audit Assistant', employer: 'Ramesh & Iyer Associates', host: 'rameshiyer.in', locality: 'RS Puram', km: 7, named: 5, matched: 3, notFound: ['Statutory audit', 'Tally Prime'], reasons: [{ text: 'You show financial accounting.', cite: cite('resume', 'line 7') }, { text: 'You show your internship at a CA firm.', cite: cite('resume', 'line 15') }], days: 16 },
  ];
  const listings = specs.map((s) => cardOf(s, area));
  return {
    area,
    lane: 'near',
    reach: '10',
    breadth: 'next',
    order: 'ranked',
    total: 3,
    reachStops: EMPTY_STOPS([1, 1, 3, 3, 4]),
    breadthStops: BREADTH_STOPS([0, 3, 3]),
    laneCounts: { near: 3, remote: 1, city: 0 },
    groups: [{ id: 'first_steps', kind: 'first_steps', title: 'First steps', count: 3, reason: { text: 'Entry roles that ask for what your evidence shows.' }, listingIds: listings.map((l) => l.id) }],
    listings,
    companies: [
      { employerId: 'chettinad-textiles', name: 'Chettinad Textiles', city: 'Chennai', reason: 'Hiring 2 accounts roles in Chennai, nothing near you now.', careersUrl: 'https://chettinadtextiles.in/careers' },
      { employerId: 'cauvery-foods', name: 'Cauvery Foods', city: 'Kochi', reason: 'Hiring 3 finance roles in Kochi, nothing near you now.', careersUrl: 'https://jobs.lever.co/cauveryfoods' },
      { employerId: 'palar-pumps', name: 'Palar Pumps', city: 'Chennai', reason: 'Hiring 2 accounts roles in Chennai, nothing near you now.', careersUrl: 'https://palarpumps.in/jobs' },
    ],
    coverage: { employers: 38, listings: 412, note: "Few roles like yours are shared with us yet. We read employers' own job boards, which lean toward tech and startups. Found a posting somewhere else? Paste it and we'll match it." },
    signals: ['Your next roles: Accounts executive, Junior accountant', 'Skills the postings name that your evidence shows', 'Distance from Peelamedu, straight line', 'How recently the employer listed it', "Only employers' own job boards"],
    refreshedAt: ago(0, 3),
  };
};

/** A few saved roles at different stages, for "Your applications". */
export const trackedFixtures = (): TrackedRole[] => {
  const mk = (id: string, status: TrackedRole['status'], savedDays: number, extra: Partial<TrackedRole> = {}): TrackedRole => {
    const s = SPECS.find((x) => x.id === id) ?? SPECS[0];
    return {
      id,
      status,
      savedAt: ago(savedDays),
      updatedAt: ago(Math.max(0, savedDays - 1)),
      title: s.title,
      employer: s.employer,
      placeLabel: s.locality ?? 'Bengaluru',
      workMode: s.mode,
      ...(s.km !== undefined ? { km: s.km } : {}),
      applyUrl: `https://${s.host}/${slug(s.employer)}/${s.id}`,
      applyHost: s.host,
      sourceLabel: "Employer's own listing, checked today",
      word: s.word,
      ...extra,
    };
  };
  return [
    mk('j1', 'applied', 4, { appliedAt: ago(3), matchId: 'demo-1', band: 'good' }),
    mk('j2', 'interviewing', 10, { appliedAt: ago(8), matchId: 'demo-2', band: 'strong', salaryText: '₹28–36 lakh a year' }),
    mk('j7', 'saved', 1),
    mk('j5', 'saved', 6),
    mk('j9', 'applied', 1, { appliedAt: ago(1) }),
    mk('d4', 'closed', 18, { gone: true, closedAt: ago(2), matchId: 'demo-3', band: 'stretch' }),
  ];
};
