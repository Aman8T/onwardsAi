/**
 * Fixtures for the prep page, the referral note, the compare table and the applications list in the dev gallery, built
 * from the live run of 2026-10-16 (Riya Kapoor, a frontend engineer in Bengaluru with 2.4 years): Headout's Frontend
 * Engineer for the prep page and the referral note; Headout, SonicWall and Drivetrain for the compare table; and the
 * four roles she saved plus one the employer took down for the applications list.
 *
 * Every quote is the one the run returned, word for word. The three lines of the referral note are the ones on board 11
 * of the canvas; the questions and their stories are board 8's.
 */
import type { AiJob, ReferralNote, RolePrep } from '../../../shared/jobsearch';
import type { TrackedJob } from '../applications/tracker';

const DAY = 86_400_000;

/** An ISO time `days` days before now. */
const ago = (days: number) => new Date(Date.now() - days * DAY).toISOString();

/** `YYYY-MM-DD`, `days` days before now. */
const dayAgo = (days: number) => {
  const d = new Date(Date.now() - days * DAY);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
};

const noFit = { word: 'cant_tell' as const, named: 0, matched: 0, notFound: [], reasons: [] };

const REACT_REBUILD = 'Rebuilt Kitebird checkout in React; owns 42-component Kite UI';
const LCP = 'Checkout LCP 4.1s to 1.9s at Kitebird';
const KITE_UI = 'Owns Kite UI: 42 components, 3 squads, Storybook';
const TS_COMMITS = '196 of 412 recent commits in TypeScript';
const MENTORED = 'Mentored 2 interns, both hired full-time; reviews for 4-person team';

const ASK_REACT = 'Strong proficiency in React (must-have), along with JavaScript and TypeScript';
const ASK_PERF = 'Experience with Git and basic performance optimization';
const ASK_CSS = 'Solid understanding of HTML, CSS, responsive design, and cross-browser compatibility';

/** Headout's Frontend Engineer, as the search showed it (the card's data). */
export const HEADOUT_JOB: AiJob = {
  id: 'headout-frontend',
  title: 'Frontend Engineer',
  employer: 'Headout',
  place: { label: 'Bengaluru', city: 'Bengaluru', precision: 'city' },
  workMode: 'unknown',
  postedAt: ago(32),
  checkedAt: ago(0),
  applyUrl: 'https://boards.greenhouse.io/headoutcareers/jobs/4707747006',
  applyHost: 'boards.greenhouse.io',
  foundVia: 'web',
  foundBy: 'Software Engineer Frontend TypeScript Bengaluru',
  fit: 'good',
  level: 'at',
  levelQuote: '1-3 years of frontend development experience',
  met: [
    { ask: ASK_REACT, cite: { source: 'Resume', text: REACT_REBUILD }, partly: false },
    { ask: ASK_PERF, cite: { source: 'Resume', text: LCP }, partly: false },
  ],
  gaps: [ASK_CSS],
  asks: { met: 2, partly: 0, notShown: 1 },
  quickFit: noFit,
};

export const SONICWALL_FRONTEND_JOB: AiJob = {
  id: 'sonicwall-frontend',
  title: 'Front End Engineer, Vue3.js, Node.js and TypeScript',
  employer: 'SonicWall',
  place: { label: 'Remote, India', precision: 'country' },
  workMode: 'remote',
  postedAt: ago(15),
  checkedAt: ago(0),
  applyUrl: 'https://job-boards.greenhouse.io/sonicwall',
  applyHost: 'job-boards.greenhouse.io',
  foundVia: 'web',
  foundBy: 'Software Engineer Frontend TypeScript Bengaluru',
  fit: 'good',
  level: 'at',
  levelQuote: '2+ years of software development experience building web applications',
  met: [
    { ask: 'Work experience with TypeScript development', cite: { source: 'GitHub', text: TS_COMMITS }, partly: false },
    { ask: 'Understanding of RESTful APIs and web application architecture', cite: { source: 'Resume', text: 'Owned REST and GraphQL contracts with 2 backend teams' }, partly: false },
    { ask: 'Design, develop, test, and maintain web applications using Vue3.js, Node.js, and TypeScript', cite: { source: 'GitHub', text: TS_COMMITS }, partly: true },
  ],
  gaps: ['Work experience in developing frontend applications using Vue3'],
  asks: { met: 2, partly: 1, notShown: 1 },
  quickFit: noFit,
};

export const DRIVETRAIN_JOB: AiJob = {
  id: 'drivetrain-frontend',
  title: 'Frontend Engineer',
  employer: 'Drivetrain',
  place: { label: 'Remote, India', precision: 'country' },
  workMode: 'remote',
  postedAt: ago(700),
  checkedAt: ago(0),
  applyUrl: 'https://jobs.lever.co/drivetrain',
  applyHost: 'jobs.lever.co',
  foundVia: 'web',
  foundBy: 'Frontend Engineer remote India',
  fit: 'good',
  level: 'at',
  levelQuote: '1 to 3 years of relevant work experience',
  met: [
    { ask: 'ReactJS and Data structure skills are important', cite: { source: 'Resume', text: REACT_REBUILD }, partly: true },
    { ask: 'Be able to write clean and maintainable code with attention to performance', cite: { source: 'Resume', text: LCP }, partly: true },
    { ask: 'sound knowledge in Javascript, HTML and CSS, and a strong eye for design', cite: { source: 'Resume', text: REACT_REBUILD }, partly: true },
  ],
  gaps: [],
  asks: { met: 0, partly: 3, notShown: 0 },
  openLong: true,
  quickFit: noFit,
};

export const GITLAB_SENIOR_JOB: AiJob = {
  id: 'gitlab-senior-frontend',
  title: 'Senior Frontend Engineer',
  employer: 'GitLab',
  place: { label: 'Bengaluru', city: 'Bengaluru', precision: 'city' },
  workMode: 'unknown',
  postedAt: ago(22),
  checkedAt: ago(0),
  applyUrl: 'https://job-boards.greenhouse.io/gitlab',
  applyHost: 'job-boards.greenhouse.io',
  foundVia: 'tayyari',
  fit: 'stretch',
  level: 'step_up',
  levelQuote: 'Senior Frontend Engineer',
  met: [
    { ask: 'Mentor Associate and Intermediate Engineers, share knowledge, remove technical blockers, and improve team delivery practices', cite: { source: 'Resume', text: MENTORED }, partly: false },
    { ask: 'Design and ship moderately sized frontend features and improvements with minimal guidance, making pragmatic architectural choices', cite: { source: 'Resume', text: REACT_REBUILD }, partly: true },
  ],
  gaps: [],
  asks: { met: 1, partly: 1, notShown: 0 },
  quickFit: noFit,
};

/** The three roles of the compare board. */
export const COMPARE_FIXTURE_JOBS: readonly AiJob[] = [HEADOUT_JOB, SONICWALL_FRONTEND_JOB, DRIVETRAIN_JOB];

/** The prep page the run wrote for Headout's Frontend Engineer (board 8). */
export const ROLE_PREP_FIXTURE: RolePrep = {
  jobId: 'headout-frontend',
  title: 'Frontend Engineer',
  employer: 'Headout',
  lead: [
    { ask: ASK_REACT, cite: { source: 'Resume', text: REACT_REBUILD }, partly: false },
    { ask: ASK_PERF, cite: { source: 'Resume', text: LCP }, partly: false },
  ],
  gaps: [
    {
      gap: ASK_CSS,
      nearest: { source: 'Resume', text: KITE_UI },
      step: 'Write two lines on how Kite UI handles responsive layouts and which browsers you test, so the gap has an answer.',
    },
  ],
  questions: [
    {
      question: 'Walk us through a React performance problem you fixed.',
      ask: ASK_PERF,
      story: [
        { label: 'Situation', placeholder: '[add: what was slow, and how you noticed]' },
        { label: 'What you did', cite: { source: 'Resume', text: REACT_REBUILD }, placeholder: '[add: what you changed first, and how you measured it]' },
        { label: 'Result', cite: { source: 'Resume', text: LCP }, placeholder: '[add: what it changed for users or the team]' },
        { label: 'Looking back', placeholder: '[add: what you would do differently]' },
      ],
    },
    {
      question: 'How do you use TypeScript when you build React components?',
      ask: ASK_REACT,
      story: [
        { label: 'Situation', cite: { source: 'Resume', text: KITE_UI } },
        { label: 'What you did', cite: { source: 'GitHub', text: TS_COMMITS }, placeholder: '[add: one type or pattern that made a component harder to misuse]' },
        { label: 'Result', placeholder: '[add: what changed for the squads that use Kite UI]' },
        { label: 'Looking back', placeholder: '[add: what you would do differently]' },
      ],
    },
    {
      question: 'How do you make a layout hold up on different screens and browsers?',
      ask: ASK_CSS,
      story: [
        { label: 'Situation', cite: { source: 'Resume', text: KITE_UI } },
        { label: 'What you did', placeholder: '[add: how Kite UI handles responsive layouts]' },
        { label: 'Result', placeholder: '[add: which browsers you test, and one thing that broke]' },
        { label: 'Looking back', placeholder: '[add: what you would do differently]' },
      ],
    },
    {
      question: 'How does your team use Git, and what do you look for in a review?',
      ask: ASK_PERF,
      story: [
        { label: 'Situation', cite: { source: 'Resume', text: MENTORED } },
        { label: 'What you did', placeholder: '[add: how your team branches, reviews and merges]' },
        { label: 'Result', placeholder: '[add: one review comment that changed a design]' },
        { label: 'Looking back', placeholder: '[add: what you would do differently]' },
      ],
    },
  ],
  createdAt: '2026-10-16T14:01:00Z',
};

/** The referral note for the same role (board 11): three checked lines, the ask and the posting's link. */
export const REFERRAL_FIXTURE: ReferralNote = {
  jobId: 'headout-frontend',
  lines: [
    {
      part: 'company',
      text: 'Hi [name], Headout is hiring a Frontend Engineer in Bengaluru. Their aim to be “the easiest, fastest, and most delightful way to head out to a real-life experience” is what caught me.',
      sources: ['the posting'],
    },
    {
      part: 'you',
      text: "I rebuilt Kitebird's checkout in React and cut its LCP from 4.1s to 1.9s, close to the “high-performance web applications” they build.",
      sources: ['your resume', 'the posting'],
    },
    {
      part: 'role',
      text: "It's at my level, and I'd get to “build projects end-to-end”, the kind of ownership I have with Kite UI.",
      sources: ['the posting', 'your resume'],
    },
  ],
  ask: 'Would you be open to referring me?',
  link: 'https://boards.greenhouse.io/headoutcareers/jobs/4707747006',
  createdAt: '2026-10-16T14:01:00Z',
};

/** A different draft, for Rewrite. */
export const REFERRAL_REWRITE_FIXTURE: ReferralNote = {
  ...REFERRAL_FIXTURE,
  lines: [
    {
      part: 'company',
      text: 'Hi [name], I saw that Headout is hiring a Frontend Engineer in Bengaluru, and the way they talk about “high-performance web applications” stood out to me.',
      sources: ['the posting'],
    },
    {
      part: 'you',
      text: "At Kitebird I rebuilt the checkout in React, and its LCP went from 4.1s to 1.9s.",
      sources: ['your resume'],
    },
    {
      part: 'role',
      text: "The posting asks for 1-3 years of frontend experience, which is where I am, and Kite UI gave me real ownership of 42 components.",
      sources: ['the posting', 'your resume'],
    },
  ],
  createdAt: '2026-10-16T14:05:00Z',
};

const tracked = (job: AiJob, extra: Partial<TrackedJob> & Pick<TrackedJob, 'stage'>): TrackedJob => ({
  job,
  runId: 'dev-run',
  note: '',
  savedAt: ago(3),
  updatedAt: ago(1),
  ...extra,
});

/** The applications of the tracker board: Headout applied 14 Oct, SonicWall and Drivetrain saved, GitLab interviewing. */
export const TRACKED_FIXTURES: readonly TrackedJob[] = [
  tracked(HEADOUT_JOB, { stage: 'applied', note: 'Referred by Ananya', appliedAt: '2026-10-14', savedAt: ago(4) }),
  tracked(SONICWALL_FRONTEND_JOB, { stage: 'saved', savedAt: ago(3) }),
  tracked(DRIVETRAIN_JOB, { stage: 'saved', savedAt: ago(2) }),
  tracked(GITLAB_SENIOR_JOB, { stage: 'interviewing', note: 'Tech screen 21 Oct', appliedAt: dayAgo(5), savedAt: ago(6) }),
  tracked(
    {
      id: 'black-duck-frontend',
      title: 'Frontend Engineer',
      employer: 'Black Duck',
      place: { label: 'Bengaluru', city: 'Bengaluru', precision: 'city' },
      workMode: 'unknown',
      checkedAt: ago(5),
      applyUrl: 'https://jobs.lever.co/blackduck',
      applyHost: 'jobs.lever.co',
      foundVia: 'web',
      fit: 'good',
      level: 'at',
      met: [],
      gaps: [],
      asks: { met: 0, partly: 0, notShown: 0 },
      quickFit: noFit,
    },
    { stage: 'closed', takenDownAt: '2026-10-12', savedAt: ago(9) },
  ),
];
