/**
 * Fixtures for the dev gallery (`/dev/discover`): the real run of 2026-10-16 for Riya Kapoor, a frontend engineer with
 * about 2.4 years in Bengaluru, as the Discover canvas drew it. Eight roles with their asks, the funnel and the trail.
 * Employers and quotes are the ones that run found; times are relative to now, so freshness reads true whenever the
 * gallery is opened.
 *
 * Nothing here is sent anywhere: the gallery's loaders and streams hand these to the real screens.
 */
import type { DiscoverCity } from '../../../shared/discover';
import type { AiJob, FunnelStep, JobSearchRun, TrailStep } from '../../../shared/jobsearch';
import type { TrackedJob } from '../applications/tracker';

const DAY = 86_400_000;
export const ago = (days: number, hours = 0) => new Date(Date.now() - days * DAY - hours * 3_600_000).toISOString();

/** Today at 7:31 pm, as the boards say; a time that has not come yet today still reads "today". */
export const searchedToday = (): string => {
  const d = new Date();
  d.setHours(19, 31, 0, 0);
  return d.toISOString();
};

export const CITIES: DiscoverCity[] = [
  { name: 'Bengaluru', localities: ['Koramangala', 'HSR Layout', 'Indiranagar', 'Whitefield'] },
  { name: 'Hyderabad', localities: ['HITEC City', 'Gachibowli'] },
  { name: 'Pune', localities: ['Hinjewadi', 'Kharadi'] },
  { name: 'Chennai', localities: ['Sholinganallur', 'Guindy'] },
  { name: 'Mumbai', localities: ['Andheri', 'Powai'] },
  { name: 'Delhi NCR', localities: ['Saket', 'Noida Sector 62'] },
  { name: 'Kolkata', localities: ['Salt Lake Sector V'] },
  { name: 'Coimbatore', localities: ['Peelamedu'] },
];

export const RUN_ID = 'dev-run-20261016';

const GH = 'job-boards.greenhouse.io';
const GH_OLD = 'boards.greenhouse.io';
const LEVER = 'jobs.lever.co';

const KITE = 'Rebuilt Kitebird checkout in React; owns 42-component Kite UI';
const KITE_UI = 'Owns Kite UI: 42 components, 3 squads, Storybook';

const base = {
  checkedAt: ago(0),
  quickFit: { word: 'worth_a_look' as const, named: 0, matched: 0, notFound: [], reasons: [] },
  foundVia: 'web' as const,
};

export const HEADOUT: AiJob = {
  ...base,
  id: 'headout-frontend',
  title: 'Frontend Engineer',
  employer: 'Headout',
  place: { label: 'Bengaluru', city: 'Bengaluru', precision: 'city' },
  workMode: 'unknown',
  postedAt: ago(32),
  applyUrl: 'https://boards.greenhouse.io/headoutcareers/jobs/4707747006',
  applyHost: GH_OLD,
  foundBy: 'Software Engineer Frontend TypeScript Bengaluru',
  fit: 'good',
  level: 'at',
  levelQuote: '1-3 years of frontend development experience',
  yearsAsked: 1,
  met: [
    { ask: 'Strong proficiency in React (must-have), along with JavaScript and TypeScript', cite: { source: 'Resume', text: KITE }, partly: false },
    { ask: 'Experience with Git and basic performance optimization', cite: { source: 'Resume', text: 'Checkout LCP 4.1s to 1.9s at Kitebird' }, partly: false },
  ],
  gaps: ['Solid understanding of HTML, CSS, responsive design, and cross-browser compatibility'],
  asks: { met: 2, partly: 0, notShown: 1 },
};

export const SONICWALL: AiJob = {
  ...base,
  id: 'sonicwall-frontend',
  title: 'Front End Engineer, Vue3.js, Node.js and TypeScript',
  employer: 'SonicWall',
  place: { label: 'Remote, India', precision: 'country' },
  workMode: 'remote',
  postedAt: ago(15),
  applyUrl: 'https://job-boards.greenhouse.io/sonicwall/jobs/4802211007',
  applyHost: GH,
  foundBy: 'Software Engineer Frontend TypeScript Bengaluru',
  fit: 'good',
  level: 'at',
  levelQuote: '2+ years of software development experience building web applications',
  yearsAsked: 2,
  met: [
    { ask: 'Work experience with TypeScript development', cite: { source: 'GitHub', text: '196 of 412 recent commits in TypeScript' }, partly: false },
    { ask: 'Understanding of RESTful APIs and web application architecture', cite: { source: 'Resume', text: 'Owned REST and GraphQL contracts with 2 backend teams' }, partly: false },
    { ask: 'Design, develop, test, and maintain web applications using Vue3.js, Node.js, and TypeScript', cite: { source: 'GitHub', text: '196 of 412 recent commits in TypeScript' }, partly: true },
  ],
  gaps: ['Work experience in developing frontend applications using Vue3'],
  asks: { met: 2, partly: 1, notShown: 1 },
};

export const DRIVETRAIN: AiJob = {
  ...base,
  id: 'drivetrain-frontend',
  title: 'Frontend Engineer',
  employer: 'Drivetrain',
  place: { label: 'Remote, India', precision: 'country' },
  workMode: 'remote',
  applyUrl: 'https://jobs.lever.co/drivetrain/6f1c7e0a-93b4-4d3e-8a55-0c1b2d3e4f50',
  applyHost: LEVER,
  foundBy: 'Frontend Engineer remote India',
  fit: 'good',
  level: 'at',
  levelQuote: '1 to 3 years of relevant work experience',
  yearsAsked: 1,
  met: [
    { ask: 'ReactJS and Data structure skills are important', cite: { source: 'Resume', text: KITE }, partly: true },
    { ask: 'Be able to write clean and maintainable code with attention to performance', cite: { source: 'Resume', text: 'Checkout LCP 4.1s to 1.9s at Kitebird' }, partly: true },
    { ask: 'sound knowledge in Javascript, HTML and CSS, and a strong eye for design', cite: { source: 'Resume', text: KITE }, partly: true },
  ],
  gaps: [],
  asks: { met: 0, partly: 3, notShown: 0 },
  openLong: true,
};

export const PARTICLE: AiJob = {
  ...base,
  id: 'particle41-frontend',
  title: 'Frontend Developer',
  employer: 'Particle41',
  place: { label: 'Remote, India', precision: 'country' },
  workMode: 'remote',
  applyUrl: 'https://job-boards.greenhouse.io/particle41/jobs/4655120006',
  applyHost: GH,
  foundBy: 'Front-End Developer React Bangalore',
  fit: 'good',
  level: 'at',
  levelQuote: 'minimum of 3 years of experience',
  yearsAsked: 3,
  met: [
    { ask: 'Proficiency in front-end technologies such as HTML5, CSS3, JavaScript, and modern frameworks/libraries like React.js or Angular', cite: { source: 'Resume', text: KITE }, partly: false },
    { ask: 'Strong understanding of server-side languages such as Node.js Python, or PHP and related frameworks like Express.js or Flask', cite: { source: 'Resume', text: 'Built Express BFF aggregating catalog, pricing, inventory APIs' }, partly: true },
    { ask: 'Experience with database technologies such as SQL (e.g., MySQL, PostgreSQL) and NoSQL (e.g., MongoDB) databases', cite: { source: 'GitHub', text: 'trpc-starter: Express, tRPC, Prisma, PostgreSQL with auth' }, partly: true },
  ],
  gaps: [],
  asks: { met: 1, partly: 2, notShown: 0 },
  openLong: true,
};

export const NEURON7: AiJob = {
  ...base,
  id: 'neuron7-frontend',
  title: 'Frontend Engineer',
  employer: 'Neuron7',
  place: { label: 'Bengaluru', city: 'Bengaluru', precision: 'city' },
  workMode: 'hybrid',
  postedAt: ago(9),
  applyUrl: 'https://jobs.lever.co/neuron7/2b7d9e4c-18aa-4f60-b3c1-7d5e6f708192',
  applyHost: LEVER,
  foundBy: 'Frontend Engineer Bengaluru',
  fit: 'stretch',
  level: 'unclear',
  met: [{ ask: 'strong UI/UX skills', cite: { source: 'Resume', text: KITE_UI }, partly: true }],
  gaps: ['Design, build, and enhance Angular-based features, web components, and reusable UI modules', 'strong in Angular, comfortable working in Java'],
  asks: { met: 0, partly: 1, notShown: 2 },
};

export const SONICWALL_PDE: AiJob = {
  ...base,
  id: 'sonicwall-pde',
  title: 'Product Design Engineer',
  employer: 'SonicWall',
  place: { label: 'Bengaluru', city: 'Bengaluru', precision: 'city' },
  workMode: 'unknown',
  postedAt: ago(8),
  applyUrl: 'https://job-boards.greenhouse.io/sonicwall/jobs/4802977007',
  applyHost: GH,
  foundBy: 'Software Engineer Frontend TypeScript Bengaluru',
  fit: 'stretch',
  level: 'step_up',
  levelQuote: '4+ years of experience',
  yearsAsked: 4,
  met: [
    { ask: 'Build and maintain components, tokens, and patterns in the Unified Management Portal Figma design system', cite: { source: 'Resume', text: KITE_UI }, partly: true },
    { ask: 'Generate working interactive prototypes using AI tools such as Claude, v0, Figma Make, and similar tools', cite: { source: 'Lovable', text: 'Recipe Genie: OpenAI via Supabase Edge Function, structured JSON' }, partly: true },
  ],
  gaps: ['Own end-to-end design for one or more Unified Management Portal product areas'],
  asks: { met: 0, partly: 2, notShown: 1 },
};

export const GITLAB: AiJob = {
  ...base,
  id: 'gitlab-senior-frontend',
  title: 'Senior Frontend Engineer',
  employer: 'GitLab',
  place: { label: 'Bengaluru', city: 'Bengaluru', precision: 'city' },
  workMode: 'unknown',
  postedAt: ago(22),
  applyUrl: 'https://job-boards.greenhouse.io/gitlab/jobs/8217430002',
  applyHost: GH,
  foundVia: 'tayyari',
  fit: 'stretch',
  level: 'step_up',
  levelQuote: 'Senior Frontend Engineer',
  met: [
    { ask: 'Mentor Associate and Intermediate Engineers, share knowledge, remove technical blockers, and improve team delivery practices', cite: { source: 'Resume', text: 'Mentored 2 interns, both hired full-time; reviews for 4-person team' }, partly: false },
    { ask: 'Design and ship moderately sized frontend features and improvements with minimal guidance, making pragmatic architectural choices', cite: { source: 'Resume', text: KITE }, partly: true },
  ],
  gaps: [],
  asks: { met: 1, partly: 1, notShown: 0 },
};

export const OPENFX: AiJob = {
  ...base,
  id: 'openfx-senior-frontend',
  title: 'Senior Frontend Engineer (L5), Banking',
  employer: 'OpenFX',
  place: { label: 'Bengaluru', city: 'Bengaluru', precision: 'city' },
  workMode: 'unknown',
  postedAt: ago(74),
  applyUrl: 'https://job-boards.greenhouse.io/openfx/jobs/4390021005',
  applyHost: GH,
  foundBy: 'Software Engineer Frontend TypeScript Bengaluru',
  fit: 'stretch',
  level: 'step_up',
  levelQuote: 'Senior Frontend Engineer (L5)',
  met: [
    { ask: 'Fluency in a modern TypeScript and React frontend stack', cite: { source: 'Resume', text: KITE }, partly: false },
    { ask: 'Deep expertise in frontend architecture, component and design systems, and state management at scale', cite: { source: 'Resume', text: KITE_UI }, partly: true },
    { ask: 'Strong grasp of frontend performance, accessibility, and correctness', cite: { source: 'Portfolio', text: 'WCAG 2.1 AA checkout audit: 37 issues logged, 21 fixed' }, partly: true },
  ],
  gaps: [],
  asks: { met: 1, partly: 2, notShown: 0 },
};

/** The eight roles, in the order the screen shows them: good fits first, then the stretches. */
export const ROLES: AiJob[] = [HEADOUT, SONICWALL, DRIVETRAIN, PARTICLE, NEURON7, SONICWALL_PDE, GITLAB, OPENFX];

/** A closed role that was saved, for the applications list. */
export const BLACK_DUCK: AiJob = {
  ...base,
  id: 'blackduck-frontend',
  title: 'Frontend Engineer',
  employer: 'Black Duck',
  place: { label: 'Bengaluru', city: 'Bengaluru', precision: 'city' },
  workMode: 'unknown',
  postedAt: ago(40),
  checkedAt: ago(5),
  applyUrl: 'https://job-boards.greenhouse.io/blackduck/jobs/4521100003',
  applyHost: GH,
  foundBy: 'Frontend Engineer Bengaluru',
  fit: 'good',
  level: 'at',
  met: [{ ask: 'Experience building web applications with React', cite: { source: 'Resume', text: KITE }, partly: false }],
  gaps: [],
  asks: { met: 1, partly: 0, notShown: 0 },
};

/* ───────────── the trail and the funnel of that run ───────────── */

export const PLAN: TrailStep = {
  stage: 'plan',
  text: "Looking for Frontend Engineer and Design Engineer roles in Bengaluru and remote in India, at about 2 years of experience: 3 searches on employers' own job sites.",
};
export const SEARCH_1: TrailStep = {
  stage: 'search',
  text: 'Frontend Engineer in Bengaluru: 16 employer boards.',
  count: 16,
  queries: ['Frontend Engineer Bengaluru', 'Front-End Developer React Bangalore', 'Software Engineer Frontend TypeScript Bengaluru'],
};
export const SEARCH_2: TrailStep = {
  stage: 'search',
  text: 'Frontend Engineer remote in India: 13 employer boards.',
  count: 13,
  queries: ['Frontend Engineer remote India', 'Front-End Developer React remote India', 'Software Engineer II Frontend TypeScript India'],
};
export const SEARCH_3: TrailStep = {
  stage: 'search',
  text: 'Design Engineer in Bengaluru: 23 employer boards.',
  count: 23,
  queries: ['Design Engineer Bengaluru', 'Design Engineer II React Bangalore', 'UI Engineer motion animation Bengaluru'],
};
export const BOARDS: TrailStep = {
  stage: 'boards',
  text: "Read 30 employer boards found on the web: 439 open roles in India. Tayyari's own 51 employers: 1,377 open roles.",
};
export const NARROW: TrailStep = { stage: 'narrow', text: 'In Bengaluru or remote in India, your kind of role, within reach of your experience: 12 roles.', count: 12 };
export const READ: TrailStep = { stage: 'read', text: 'Read 12 postings against your evidence.', count: 12 };

export const TRAIL: TrailStep[] = [PLAN, SEARCH_1, SEARCH_2, SEARCH_3, BOARDS];

export const FUNNEL: FunnelStep[] = [
  { label: 'open roles in India on the boards read', count: 1816 },
  { label: 'in Bengaluru or remote in India', count: 1018 },
  { label: 'your kind of role', count: 32 },
  { label: 'within reach of your experience', count: 12 },
  { label: 'read against your evidence', count: 12 },
  { label: 'fit after the checks', count: 8 },
];

export const NOTHING_FUNNEL: FunnelStep[] = [...FUNNEL.slice(0, 5), { label: 'fit after the checks', count: 0 }];

/** The last finished search, as `GET /api/jobsearch/latest` answers it. */
export const lastRun = (over: Partial<JobSearchRun> = {}): JobSearchRun => ({
  runId: RUN_ID,
  createdAt: searchedToday(),
  city: 'Bengaluru',
  remote: true,
  jobs: ROLES,
  funnel: FUNNEL,
  trail: TRAIL,
  ...over,
});

/* ───────────── Your applications, as the boards show them ───────────── */

const tracked = (job: AiJob, stage: TrackedJob['stage'], over: Partial<TrackedJob> = {}): TrackedJob => ({
  job,
  runId: RUN_ID,
  stage,
  note: '',
  savedAt: ago(6),
  updatedAt: ago(2),
  ...over,
});

export const trackedFixtures = (): TrackedJob[] => [
  tracked(HEADOUT, 'applied', { appliedAt: `${new Date().getFullYear()}-10-14`, note: 'Referred by Ananya' }),
  tracked(SONICWALL, 'saved'),
  tracked(DRIVETRAIN, 'saved'),
  tracked(GITLAB, 'interviewing', { note: 'Tech screen 21 Oct' }),
  tracked(BLACK_DUCK, 'closed', { updatedAt: ago(4) }),
];
