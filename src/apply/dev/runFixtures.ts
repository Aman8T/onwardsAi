/**
 * Stored states for the run screens in the `/dev/apply` gallery: one application per stage of spec 3 and per stop
 * reason, with the canvas's sample data (Kestrel Pay, Hollis Health; invented companies and people). Times are fixed
 * against `NOW` from fixtures.ts, so every render is the same.
 */
import type { ApplicationDetail, ApplicationReview, FilledField, NeedsInput, ProgressSection } from '../../../shared/apply';
import type { ReviewMatch } from '../ReviewView';
import type { ReviewField } from '../ReviewGroup';
import { NOW } from './fixtures';

const at = (ms: number) => new Date(NOW - ms).toISOString();
const MIN = 60_000;
const HOUR = 60 * MIN;
const DAY = 24 * HOUR;

const DIGEST = 'd3c1a7e0b2f94c6e8a5d7b1c0e9f2a4b6c8d0e1f3a5b7c9d1e3f5a7b9c1d3e5f';

const f = (key: string, label: string, value: string, over: Partial<ReviewField> = {}): ReviewField => ({
  key,
  label,
  kind: 'text',
  required: true,
  value,
  source: 'details',
  selector: `[name=${key}]`,
  ...over,
});

let order = 0;
const at0 = (group: string) => (field: ReviewField): ReviewField => ({ ...field, group, order: order++ });

const PERSONAL = 'Personal information';
const ELIGIBILITY = 'Eligibility';
const QUESTIONS = 'Application questions';
const VOLUNTARY = 'Voluntary disclosures';
const DECLARATION = 'Declaration';

const WHY =
  'I want to work on payment systems that people rely on every day. At my internship I built the refund reconciliation job for UPI payments, which matched settlement files against our ledger each night. Kestrel Pay’s posting describes the same kind of work at a larger scale, and that is the work I have chosen to get better at.';

export const REVIEW_FIELDS: ReviewField[] = [
  ...[
    f('first_name', 'First name', 'Asha', { questionKey: 'firstName', profileKey: 'firstName' }),
    f('last_name', 'Last name', 'Verma', { questionKey: 'lastName', profileKey: 'lastName' }),
    f('email', 'Email', 'asha.verma@example.com', { kind: 'email', questionKey: 'email', locked: true }),
    f('phone', 'Phone', '+91 98000 00000', { kind: 'tel', questionKey: 'phone' }),
    f('location', 'Location', 'Pune, Maharashtra', { questionKey: 'location' }),
    f('linkedin', 'LinkedIn', 'linkedin.com/in/asha-verma', { kind: 'url', questionKey: 'linkedin', required: false, source: 'resume' }),
  ].map(at0(PERSONAL)),
  ...[
    f('wa_in', 'Are you legally authorised to work in India?', 'Yes', {
      kind: 'select',
      options: ['Yes', 'No'],
      optionValue: '1',
      questionKey: 'workAuthorization:IN',
    }),
    f('sp_in', 'Will you now or in the future require visa sponsorship?', 'No', {
      kind: 'radio',
      options: ['Yes', 'No'],
      questionKey: 'needsSponsorship:IN',
    }),
  ].map(at0(ELIGIBILITY)),
  ...[
    f('hear', 'How did you hear about this role?', 'Other: Leafout', {
      kind: 'select',
      options: ['LinkedIn', 'A friend', 'Company website', 'Other: Leafout'],
      source: 'default',
      questionKey: 'howDidYouHear',
    }),
    f('why', 'Why do you want to work at Kestrel Pay?', WHY, {
      kind: 'textarea',
      source: 'model',
      confidence: 'high',
      factIds: ['f_intern_upi', 'f_you_payments'],
      dropped: ['I have deep expertise in distributed ledgers.'],
      questionKey: 'q:why-kestrel',
    }),
    f('ctc', 'Expected CTC', 'Open to discussion', { source: 'default', questionKey: 'expectedCtc' }),
  ].map(at0(QUESTIONS)),
  ...[
    f('gender', 'Gender', 'Decline to answer', { kind: 'select', options: ['Female', 'Male', 'Non-binary', 'Decline to answer'], source: 'default', required: false }),
    f('disability', 'Disability', 'Decline to answer', { kind: 'select', options: ['Yes', 'No', 'Decline to answer'], source: 'default', required: false }),
    f('veteran', 'Veteran status', 'Decline to answer', { kind: 'select', options: ['I am a veteran', 'I am not a veteran', 'Decline to answer'], source: 'default', required: false }),
  ].map(at0(VOLUNTARY)),
];

const SIGNED: ReviewField = { ...f('signature', 'Signature: type your full name to confirm the information is true', 'Asha Verma', { source: 'user', attestation: true, questionKey: 'signature' }), group: DECLARATION, order: order++ };

const REVIEW: ApplicationReview = {
  digest: DIGEST,
  capturedAt: at(20 * MIN),
  mode: 'playbook',
  fields: [...REVIEW_FIELDS, SIGNED],
  skipped: [{ key: 'website', label: 'Website', kind: 'url', required: false }, { key: 'pronouns', label: 'Pronouns', kind: 'text', required: false }],
  warnings: [],
  playbookId: 'pb_greenhouse',
  submitLabel: 'Submit application',
  resume: { name: 'Asha-Verma-Kestrel-Pay.pdf', sha256: 'a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2', choice: 'tailored' },
  facts: [
    { id: 'f_intern_upi', text: 'Resume, Internship at Finlit, line 2: “Built a nightly refund reconciliation job for UPI payments, matching settlement files against the ledger.”' },
    { id: 'f_you_payments', text: 'Your answer, 2 Nov: “I want to keep working on payments; it’s the part I enjoyed most.”' },
  ],
  model: 'claude-haiku-5-5',
};

const BASE: ApplicationDetail = {
  id: 'a_kestrel',
  stage: 'waiting_for_you',
  source: 'match',
  ats: 'greenhouse',
  domain: 'job-boards.greenhouse.io',
  jobUrl: 'https://job-boards.greenhouse.io/kestrelpay/jobs/4021',
  applyUrl: 'https://job-boards.greenhouse.io/kestrelpay/jobs/4021',
  manualUrl: 'https://job-boards.greenhouse.io/kestrelpay/jobs/4021#app',
  createdAt: at(2 * HOUR),
  updatedAt: at(10 * MIN),
  needsCount: 0,
  hasScreenshot: true,
  needs: [],
  events: [],
  company: 'Kestrel Pay',
  title: 'Backend Engineer, Payments',
  location: 'Pune',
  matchId: 'm_kestrel',
  resumeChoice: 'tailored',
  creditState: 'held',
  description:
    'Kestrel Pay moves money for small businesses across India. You will build the services behind refunds, settlements and reconciliation: Python and Go, Postgres, Kafka. You have shipped backend work to production and care about correctness.',
};

const with_ = (over: Partial<ApplicationDetail>): ApplicationDetail => ({ ...BASE, ...over });

/* ───────────── filling ───────────── */

const SECTIONS_NOW: ProgressSection[] = [
  { label: 'Personal information', state: 'done', filled: 6, total: 6 },
  { label: 'Eligibility', state: 'done', filled: 2, total: 3, waiting: 1 },
  { label: 'Application questions', state: 'now', filled: 1, total: 3, note: 'Writing “Why Kestrel Pay?” from your facts' },
  { label: 'Voluntary disclosures', state: 'todo', filled: 0, total: 3, note: 'Decline to answer (self-ID is off)' },
];

const US_FIELD: FilledField = { key: 'wa_us', label: 'Authorised to work in the United States?', kind: 'radio', required: true };

const FILLED_SO_FAR: FilledField[] = [...REVIEW_FIELDS.slice(0, 8), US_FIELD, REVIEW_FIELDS[8]];

export const RUN_QUEUED = with_({ id: 'a_kestrel_q', stage: 'queued', hasScreenshot: false, review: undefined, updatedAt: at(30 * 1000) });

export const RUN_FILLING = with_({
  stage: 'filling',
  hasScreenshot: false,
  updatedAt: at(1 * MIN),
  progress: { sections: SECTIONS_NOW, filled: 9, total: 17, fields: FILLED_SO_FAR },
});

/* ───────────── the questions ───────────── */

const NEED_CLASS12: NeedsInput = {
  key: 'class12',
  label: 'Class 12 percentage',
  kind: 'number',
  required: true,
  reason: 'Campus and mass-hiring forms often ask it, so it’s worth saving.',
  questionKey: 'class12',
  requiredBy: 'Hollis Health',
  unit: '%',
  alsoWaiting: [
    { applicationId: 'a_tamarind', company: 'Tamarind' },
    { applicationId: 'a_corvid', company: 'Corvid Labs' },
  ],
};

const NEED_CTC: NeedsInput = {
  key: 'current_ctc',
  label: 'Current CTC',
  kind: 'number',
  required: true,
  reason: 'Kept private: never on your resume or portfolio, and only sent to forms that require it.',
  questionKey: 'currentCtc',
  requiredBy: 'Hollis Health',
  unit: '₹ lakh a year',
};

const NEED_BOND: NeedsInput = {
  key: 'bond',
  label: 'Will you sign a 1-year service agreement?',
  kind: 'radio',
  required: true,
  reason: 'This is about this offer, so by default we don’t remember it.',
  options: ['Yes', 'No'],
  questionKey: 'bond',
  perOffer: true,
};

const NEED_PAN: NeedsInput = {
  key: 'pan',
  label: 'PAN number',
  kind: 'text',
  required: true,
  reason: 'We never store ID numbers. Type it yourself on the employer’s page.',
  questionKey: 'pan',
  onSite: true,
  requiredBy: 'Hollis Health',
};

const NEED_US: NeedsInput = {
  key: 'wa_us',
  label: 'Are you authorised to work in the United States?',
  kind: 'radio',
  required: true,
  reason: 'Your details only cover India, and we never carry an answer from one country to another.',
  options: ['Yes', 'No'],
  questionKey: 'workAuthorization:US',
  requiredBy: 'Kestrel Pay',
};

const NEED_SIGN: NeedsInput = {
  key: 'signature',
  label: 'Signature: type your full name to confirm the information is true',
  kind: 'text',
  required: true,
  reason: 'A signature is always yours to type. We never fill it, and never keep it.',
  questionKey: 'signature',
  perOffer: true,
};

const HOLLIS = {
  id: 'a_hollis',
  company: 'Hollis Health',
  title: 'Data Analyst',
  ats: 'ashby' as const,
  domain: 'jobs.ashbyhq.com',
  jobUrl: 'https://jobs.ashbyhq.com/hollis/77',
  applyUrl: 'https://jobs.ashbyhq.com/hollis/77/application',
  manualUrl: 'https://jobs.ashbyhq.com/hollis/77/application',
  resumeChoice: 'saved' as const,
};

export const RUN_QUESTIONS = with_({
  ...HOLLIS,
  stage: 'waiting_for_you',
  stopReason: 'questions',
  needs: [NEED_CLASS12, NEED_CTC, NEED_BOND, NEED_PAN],
  needsCount: 4,
  review: undefined,
  hasScreenshot: false,
});

/* ───────────── the review ───────────── */

const REVIEW_NO_SIGNATURE: ApplicationReview = { ...REVIEW, fields: REVIEW_FIELDS };

/** Everything answered: Approve and send is on. */
export const RUN_REVIEW = with_({ review: REVIEW });

/** A signature and a country question left: Approve and send says why it is off. */
export const RUN_REVIEW_NEEDS = with_({ id: 'a_kestrel_2', review: REVIEW_NO_SIGNATURE, needs: [NEED_US, NEED_SIGN], needsCount: 2 });

/** Only the signature left (the questions went in), the case Review is for. */
export const RUN_REVIEW_SIGN = with_({ id: 'a_kestrel_3', review: REVIEW_NO_SIGNATURE, needs: [NEED_SIGN], needsCount: 1 });

/** A required ID number: it can never be sent from here, so the form is opened instead. */
export const RUN_REVIEW_ONSITE = with_({
  ...HOLLIS,
  review: { ...REVIEW, fields: REVIEW_FIELDS.slice(0, 6), resume: { ...REVIEW.resume!, name: 'asha-verma-resume.pdf', choice: 'saved' }, facts: [] },
  needs: [NEED_PAN],
  needsCount: 1,
});

export const RUN_REVIEW_WARNINGS = with_({
  id: 'a_kestrel_4',
  review: { ...REVIEW, warnings: ['This form showed a "verify you are human" box on the last page. If it appears when sending, we stop and hand it to you.'] },
});

/** The ids the review's Prev and Next walk through ("2 of 5 ready"). */
export const RUN_QUEUE: readonly string[] = ['a_corvid', 'a_kestrel', 'a_kestrel_2', 'a_kestrel_3', 'a_hollis'];

export const RUN_MATCH: ReviewMatch = {
  id: 'm_kestrel',
  band: 'good',
  note: 'Every must-have has evidence behind it. One other item is a gap.',
};

/* ───────────── the outcomes ───────────── */

export const RUN_FORM_CHANGED = with_({
  stage: 'waiting_for_you',
  stopReason: 'form_changed',
  stageReason: 'a new required field, “Notice period”',
  review: REVIEW_NO_SIGNATURE,
  needs: [{ ...NEED_SIGN }],
  needsCount: 1,
});

export const RUN_APPROVED = with_({ stage: 'approved', review: REVIEW, approvedAt: at(1 * MIN) });

export const RUN_SUBMITTING = with_({ stage: 'submitting', review: REVIEW, approvedAt: at(2 * MIN), updatedAt: at(20 * 1000) });

const WORKDAY = {
  id: 'a_hitachi',
  company: 'Hitachi Energy',
  title: 'Software Engineer',
  ats: 'workday' as const,
  domain: 'hitachi.wd3.myworkdayjobs.com',
  jobUrl: 'https://hitachi.wd3.myworkdayjobs.com/careers/job/Pune/Software-Engineer_R1',
  applyUrl: 'https://hitachi.wd3.myworkdayjobs.com/careers/job/Pune/Software-Engineer_R1/apply',
  manualUrl: 'https://hitachi.wd3.myworkdayjobs.com/careers/job/Pune/Software-Engineer_R1/apply',
};

export const RUN_SIGNIN = with_({
  ...WORKDAY,
  stage: 'needs_signin',
  stopReason: 'signin',
  review: undefined,
  hasScreenshot: false,
  progress: { sections: [], filled: 6, total: 6, fields: REVIEW_FIELDS.slice(0, 6) },
});

export const RUN_CHALLENGE = with_({ ...HOLLIS, stage: 'needs_signin', stopReason: 'challenge', review: REVIEW_NO_SIGNATURE, updatedAt: at(1 * DAY) });

export const RUN_CODE = with_({ stage: 'needs_signin', stopReason: 'verification_code', review: REVIEW });

export const RUN_CHECK = with_({
  id: 'a_juniper',
  company: 'Juniper Grid',
  title: 'Backend Developer',
  stage: 'check_this_one',
  stageReason: 'The page timed out before the “thank you” page loaded',
  review: REVIEW,
  approvedAt: at(5 * HOUR + 2 * MIN),
  submittedAt: at(5 * HOUR),
});

export const RUN_FAILED = with_({
  id: 'a_pebble',
  company: 'Pebble Works',
  title: 'Software Engineer',
  stage: 'failed',
  stopReason: 'fill_error',
  stageReason: 'A dropdown on the form kept rejecting the answer',
  creditState: 'released',
  review: undefined,
  hasScreenshot: false,
});

export const RUN_FAILED_GUARD = with_({ id: 'a_pebble_2', stage: 'failed', stopReason: 'guard_probe', creditState: 'released', review: undefined, hasScreenshot: false, resumeChoice: 'saved' });

export const RUN_CLOSED = with_({ id: 'a_saffron', company: 'Saffron Credit', title: 'Engineer, Ledger', stage: 'closed', creditState: 'released', review: undefined, hasScreenshot: false });

export const RUN_CANCELLED = with_({ id: 'a_willow', company: 'Willow Retail', title: 'Full-stack Engineer', stage: 'cancelled', creditState: 'released' });

export const RUN_SENT = with_({
  stage: 'sent',
  review: REVIEW,
  approvedAt: at(3 * DAY + 5 * MIN),
  submittedAt: at(3 * DAY),
  creditState: 'charged',
  hasConfirmation: true,
});

export const RUN_INTERVIEWING = with_({
  ...RUN_SENT,
  id: 'a_ostrava',
  company: 'Ostrava Systems',
  stage: 'interviewing',
  events: [{ at: at(1 * DAY), kind: 'email', source: 'email', fromStage: 'sent', toStage: 'interviewing', note: 'We would like to invite you to a first-round interview.' }],
});

/** After an answer: the row and two more forms carried on. */
export function requeued(detail: ApplicationDetail): ApplicationDetail {
  return { ...detail, stage: 'queued', needs: [], needsCount: 0, stopReason: undefined, updatedAt: new Date(NOW).toISOString() };
}
