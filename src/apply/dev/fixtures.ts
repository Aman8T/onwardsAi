/**
 * Stored states for the auto-apply gallery (`/dev/apply`): the status, the applications list, Your details, the check
 * results (offered and one per block reason), a match and the admin metrics. Companies and people are invented, as in
 * the canvas. Times are fixed against `NOW`, so "since" and "No reply in 3 weeks" render the same every time.
 */
import type {
  ApplicationDetail,
  ApplicationSummary,
  ApplyBlockReason,
  ApplyCheck,
  ApplyMetrics,
  ApplyStatus,
  EmailClassification,
  YourDetails,
} from '../../../shared/apply';
import type { StartMatch } from '../StartView';

export const NOW = Date.parse('2026-11-08T10:00:00Z');

const ago = (ms: number) => new Date(NOW - ms).toISOString();
const MIN = 60_000;
const HOUR = 60 * MIN;
const DAY = 24 * HOUR;

export const STATUS: ApplyStatus = {
  enabled: true,
  signedIn: true,
  firstCheckDone: true,
  consentAt: ago(2 * DAY),
  freeLeft: 7,
};

export const STATUS_PRICED: ApplyStatus = { ...STATUS, price: 1, freeLeft: 0 };
export const STATUS_FIRST_TIME: ApplyStatus = { ...STATUS, firstCheckDone: false, consentAt: undefined, freeLeft: 10 };

function app(over: Partial<ApplicationSummary> & Pick<ApplicationSummary, 'id' | 'stage'>): ApplicationSummary {
  return {
    source: 'match',
    ats: 'greenhouse',
    domain: 'job-boards.greenhouse.io',
    jobUrl: 'https://job-boards.greenhouse.io/kestrelpay/jobs/4021',
    createdAt: ago(2 * HOUR),
    updatedAt: ago(10 * MIN),
    needsCount: 0,
    hasScreenshot: false,
    matchId: 'm_kestrel',
    resumeChoice: 'tailored',
    ...over,
  };
}

export const APPLICATIONS: ApplicationSummary[] = [
  app({
    id: 'a_kestrel',
    stage: 'waiting_for_you',
    title: 'Backend Engineer, Payments',
    company: 'Kestrel Pay',
    needsCount: 2,
    stopReason: 'questions',
    stageReason: 'Your notice period buyout, and your signature',
    hasScreenshot: true,
    creditState: 'held',
  }),
  app({
    id: 'a_corvid',
    stage: 'waiting_for_you',
    title: 'Software Engineer, Platform',
    company: 'Corvid Labs',
    ats: 'lever',
    domain: 'jobs.lever.co',
    updatedAt: ago(1 * HOUR),
    hasScreenshot: true,
    creditState: 'held',
  }),
  app({
    id: 'a_hollis',
    stage: 'needs_signin',
    title: 'Data Engineer',
    company: 'Hollis Health',
    ats: 'ashby',
    domain: 'jobs.ashbyhq.com',
    resumeChoice: 'saved',
    stopReason: 'challenge',
    updatedAt: ago(1 * DAY + 2 * HOUR),
    creditState: 'held',
  }),
  app({
    id: 'a_tamarind',
    stage: 'filling',
    title: 'Platform Engineer',
    company: 'Tamarind',
    updatedAt: ago(1 * MIN),
    progress: { sections: [{ label: 'You', state: 'done', filled: 6, total: 6 }, { label: 'Questions', state: 'now', filled: 3, total: 8 }], filled: 9, total: 14 },
    creditState: 'held',
  }),
  app({ id: 'a_ostrava_q', stage: 'queued', title: 'Site Reliability Engineer', company: 'Ostrava Systems', updatedAt: ago(30 * 1000), creditState: 'held' }),
  app({ id: 'a_brightline', stage: 'sent', title: 'Backend Engineer', company: 'Brightline', updatedAt: ago(3 * DAY), creditState: 'charged', hasConfirmation: true }),
  app({ id: 'a_halden', stage: 'sent', title: 'Software Engineer II', company: 'Halden Bank', updatedAt: ago(25 * DAY), creditState: 'charged', hasConfirmation: true }),
  app({ id: 'a_quill', stage: 'viewed', title: 'API Engineer', company: 'Quill Freight', updatedAt: ago(6 * DAY), creditState: 'charged' }),
  app({ id: 'a_ostrava', stage: 'interviewing', title: 'Backend Engineer', company: 'Ostrava Systems', ats: 'lever', domain: 'jobs.lever.co', updatedAt: ago(4 * DAY), creditState: 'charged' }),
  app({ id: 'a_marigold', stage: 'rejected', title: 'Payments Engineer', company: 'Marigold', updatedAt: ago(7 * DAY), creditState: 'charged' }),
  app({
    id: 'a_juniper',
    stage: 'check_this_one',
    title: 'Backend Developer',
    company: 'Juniper Grid',
    stageReason: 'We clicked send and the page showed no confirmation',
    updatedAt: ago(5 * HOUR),
    creditState: 'held',
  }),
  app({ id: 'a_pebble', stage: 'failed', title: 'Software Engineer', company: 'Pebble Works', stopReason: 'fill_error', updatedAt: ago(2 * DAY), creditState: 'released' }),
  app({ id: 'a_saffron', stage: 'closed', title: 'Engineer, Ledger', company: 'Saffron Credit', updatedAt: ago(3 * DAY), creditState: 'released' }),
  app({ id: 'a_willow', stage: 'cancelled', title: 'Full-stack Engineer', company: 'Willow Retail', updatedAt: ago(4 * DAY), creditState: 'released' }),
];

export const DETAIL: ApplicationDetail = {
  ...APPLICATIONS[0],
  needs: [],
  events: [],
  applyUrl: 'https://job-boards.greenhouse.io/kestrelpay/jobs/4021',
};

export const DETAILS: YourDetails = {
  answers: [
    { questionKey: 'fullName', question: 'Name', source: 'resume', askEachTime: false, answeredAt: ago(30 * DAY), value: 'Asha Verma', group: 'you' },
    { questionKey: 'email', question: 'Email', source: 'resume', askEachTime: false, answeredAt: ago(30 * DAY), value: 'asha.verma@example.com', group: 'you' },
    { questionKey: 'phone', question: 'Mobile', source: 'resume', askEachTime: false, answeredAt: ago(30 * DAY), value: '+91 98000 00000', group: 'you' },
    { questionKey: 'location', question: 'City', source: 'resume', askEachTime: false, answeredAt: ago(30 * DAY), value: 'Pune', group: 'you' },
    {
      questionKey: 'q:languages',
      question: 'Languages you can work in',
      source: 'form',
      answeredOn: 'Tamarind',
      askEachTime: false,
      answeredAt: ago(6 * DAY),
      value: 'English, Hindi, Marathi',
      group: 'you',
    },
    { questionKey: 'currentTitle', question: 'Current role', source: 'resume', askEachTime: false, answeredAt: ago(30 * DAY), value: 'Software Engineer, Finlit Technologies', group: 'work' },
    { questionKey: 'experience', question: 'Experience', source: 'resume', askEachTime: false, answeredAt: ago(30 * DAY), value: '1 yr 4 mo full-time', group: 'work' },
    { questionKey: 'noticePeriod', question: 'Notice period', source: 'first_check', askEachTime: false, answeredAt: ago(95 * DAY), value: '30 days', group: 'work', stale: true },
    { questionKey: 'currentCtc', question: 'Current CTC', source: 'form', answeredOn: 'Hollis Health', askEachTime: false, answeredAt: ago(1 * DAY), group: 'work', private: true },
    { questionKey: 'expectedCtc', question: 'Expected CTC', source: 'default', askEachTime: false, answeredAt: ago(30 * DAY), value: 'Open to discussion', group: 'work' },
    { questionKey: 'degree', question: 'Degree', source: 'resume', askEachTime: false, answeredAt: ago(30 * DAY), value: 'B.Tech CSE, COEP, 2025', group: 'education' },
    { questionKey: 'graduationScore', question: 'Graduation score', source: 'resume', askEachTime: false, answeredAt: ago(30 * DAY), value: '8.1 CGPA', group: 'education' },
    { questionKey: 'class12', question: 'Class 12', source: 'form', answeredOn: 'Hollis Health', askEachTime: false, answeredAt: ago(1 * DAY), value: '88.4%', group: 'education' },
    { questionKey: 'relocation', question: 'Relocation', source: 'default', askEachTime: true, answeredAt: ago(30 * DAY), group: 'where_how' },
    { questionKey: 'serviceBond', question: 'Service bond', source: 'default', askEachTime: true, answeredAt: ago(30 * DAY), group: 'where_how' },
    { questionKey: 'eeoGender', question: 'Gender', source: 'default', askEachTime: false, answeredAt: ago(30 * DAY), value: 'Prefer not to say', group: 'self_id' },
  ],
  notAskedYet: [
    { questionKey: 'dateOfBirth', question: 'Date of birth', group: 'you', kind: 'date' },
    { questionKey: 'class10', question: 'Class 10', group: 'education', kind: 'text' },
    { questionKey: 'backlogs', question: 'Backlogs', group: 'education', kind: 'select', options: ['None', '1', '2 or more'] },
    { questionKey: 'shifts', question: 'Shifts', group: 'where_how', kind: 'radio', options: ['Day', 'Rotational', 'Night'] },
    { questionKey: 'drivingLicence', question: 'Driving licence', group: 'registrations', kind: 'radio', options: ['Yes', 'No'] },
  ],
  workAuthorization: [{ country: 'IN', authorised: 'Yes, citizen', needsSponsorship: 'No' }],
  selfIdOnForms: false,
  neverStored: [
    'PAN, Aadhaar, bank or any ID number',
    'caste or category, religion, marital status, father’s name',
    'signatures and declarations',
    'referrals and “do you know anyone here”',
  ],
  hasResume: true,
  firstCheckDone: true,
  resumeName: 'asha-verma-resume.pdf',
  updatedAt: ago(5 * DAY),
};

/** Before the first check: only what the resume gave. */
export const DETAILS_FIRST_TIME: YourDetails = {
  ...DETAILS,
  answers: DETAILS.answers.filter((a) => a.source === 'resume'),
  workAuthorization: [],
  firstCheckDone: false,
};

export const DETAILS_NO_RESUME: YourDetails = { ...DETAILS, hasResume: false, resumeName: undefined };

export const MATCH: StartMatch = {
  id: 'm_kestrel',
  title: 'Backend Engineer, Payments',
  company: 'Kestrel Pay',
  location: 'Pune',
  workMode: 'hybrid',
  band: 'good',
};

export const CHECK_OFFERED: ApplyCheck = { offered: true, host: 'job-boards.greenhouse.io', ats: 'greenhouse' };

export function checkFailing(reason: ApplyBlockReason): ApplyCheck {
  const check: ApplyCheck = { offered: false, reason, host: 'job-boards.greenhouse.io', ats: 'greenhouse' };
  if (reason === 'own_account_ats') return { ...check, host: 'hollis.wd3.myworkdayjobs.com', ats: 'workday' };
  if (reason === 'not_fillable') return { ...check, host: 'careers.kestrelpay.example', ats: 'unknown' };
  if (reason === 'manual_only') return { ...check, host: 'www.linkedin.com', ats: 'unknown' };
  if (reason === 'blocked_by_requirement') return { ...check, gateReasons: ['5+ years of backend work (your resume shows 1 year 4 months)'] };
  if (reason === 'already_applied') return { ...check, applicationId: 'a_kestrel' };
  return check;
}

export const BLOCK_REASONS: readonly ApplyBlockReason[] = [
  'closed',
  'unknown_liveness',
  'not_fillable',
  'own_account_ats',
  'manual_only',
  'blocked_by_requirement',
  'already_applied',
  'no_credit',
  'signed_out',
];

export const EMPLOYER_URL = 'https://job-boards.greenhouse.io/kestrelpay/jobs/4021';

export const METRICS: ApplyMetrics = {
  since: ago(30 * DAY),
  total: { ats: 'all', runs: 41, playbookRuns: 33, loopRuns: 8, successes: 29, questions: 9, needsSignin: 0, closed: 0, failures: 3, checkThisOne: 0, interrupted: 0, deferred: 0, loopShare: 0.2, successRate: 0.71, avgSteps: 14, costMicroUsd: 61_000 },
  byAts: [
    { ats: 'greenhouse', runs: 22, playbookRuns: 20, loopRuns: 2, successes: 17, questions: 4, needsSignin: 0, closed: 0, failures: 1, checkThisOne: 0, interrupted: 0, deferred: 0, loopShare: 0.09, successRate: 0.77, avgSteps: 13, costMicroUsd: 21_000 },
    { ats: 'lever', runs: 12, playbookRuns: 9, loopRuns: 3, successes: 8, questions: 3, needsSignin: 0, closed: 0, failures: 1, checkThisOne: 0, interrupted: 0, deferred: 0, loopShare: 0.25, successRate: 0.67, avgSteps: 15, costMicroUsd: 19_000 },
    { ats: 'ashby', runs: 7, playbookRuns: 4, loopRuns: 3, successes: 4, questions: 2, needsSignin: 0, closed: 0, failures: 1, checkThisOne: 0, interrupted: 0, deferred: 0, loopShare: 0.43, successRate: 0.57, avgSteps: 16, costMicroUsd: 21_000 },
  ],
  failureReasons: [{ reason: 'fill_error', count: 2 }, { reason: 'guard_probe', count: 1 }],
  playbooks: [],
};

export const EMAIL_RESULT: EmailClassification = {
  stage: 'interviewing',
  confidence: 'high',
  applied: true,
  quote: 'We would like to invite you to a first-round interview.',
  applicationId: 'a_ostrava',
};
