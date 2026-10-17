/**
 * Fixtures for the dev page (`/dev/practice`): Riya Kapoor, a frontend engineer with about 2.4 years in Bengaluru, and the
 * interview the "Practice the interview" canvas drew for her: This role, Frontend Engineer at Headout, 16 Oct, 14 minutes,
 * five questions, with her words and the verdicts exactly as the boards have them. The earlier session (12 Oct, Defend your
 * resume) and the work sample (the cart total that flickers) come from the same boards.
 *
 * Typed against `shared/interview.ts`, so a change to the contract breaks this file at compile time. Where the boards drew
 * a line the backend's rules would not write (a "Say" for an answer with nothing to add), the rules win: the fixtures show
 * what the server can send, so a screen is never drawn for text it will never get.
 *
 * Nothing here is sent anywhere: the dev page hands these to the real screens.
 */
import type {
  CheckResult,
  Debrief,
  PracticeHistory,
  RoleSource,
  SessionSummary,
  SlotResult,
  SlotSource,
  Turn,
  VerdictCounts,
  WorkTask,
} from '../../../shared/interview';
import { HEADOUT, RUN_ID } from '../../discover/dev/fixtures';

export const PROFILE_ID = 'dev-fixture-practice';
export const WHO = 'Riya Kapoor · Frontend engineer';

export const ROLE: RoleSource = { runId: RUN_ID, jobId: HEADOUT.id };

export const SESSION_ID = '0a1b2c3d4e5f60718293a4b5c6d7e8f9';
export const EARLIER_ID = '1b2c3d4e5f60718293a4b5c6d7e8f90a';
export const WORK_ID = '2c3d4e5f60718293a4b5c6d7e8f90a1b';

const CREATED = '2026-10-16T10:30:00.000Z';
const EARLIER = '2026-10-12T09:10:00.000Z';

/* ───────────────────────── the interview of 16 Oct ───────────────────────── */

const posting = (text: string): SlotSource => ({ origin: 'posting', text });
const resume = (text: string, line: string): SlotSource => ({ origin: 'evidence', text, source: `Resume · line ${line}` });

const Q = {
  s1: 'Walk me through the checkout rebuild. What did you change first, and why?',
  s2: "Your resume says the payment step's LCP went from 4.1s to 1.9s. How did you find what was slow?",
  s3: 'How do you make sure a Kite UI component works on every screen size and browser your customers use?',
  s4: 'You mentored two interns who both got offers. What did you do that made a difference?',
  s5: "Tell me about a time a design couldn't be built the way it was drawn. What did you do?",
};

const A = {
  s1: 'I merged the three steps into one page with a reducer for the checkout state. The cart updated before the server answered and rolled back if the payment API said no, with a short message under the button.',
  s2a: 'We, um, did a lot of things. Images, the bundle, some fonts. And it got a lot faster.',
  s2b: "I think the biggest one was the images? I'm not sure how we measured it, the PM had a dashboard.",
  s3: "We test in Chrome, Safari on iPhone and Samsung Internet, because most of our traffic is mobile. Last month the sticky pay button hid behind Safari's bottom toolbar, and I fixed it with safe-area insets.",
  s4: 'I paired with them twice a week and gave each of them one real ticket from the backlog in the first week, so they shipped early. One of them struggled with TypeScript generics, so we did small exercises on our own components.',
  s5a: "Hmm, I don't remember a specific time. It usually goes fine.",
  s5b: "I'd message the designer.",
};

export const TRANSCRIPT: Turn[] = [
  { speaker: 'interviewer', text: Q.s1, at: 0, slotId: 's1', rung: 'surface' },
  { speaker: 'you', text: A.s1, at: 20, slotId: 's1', rung: 'surface', mode: 'speech' },
  { speaker: 'interviewer', text: Q.s2, at: 134, slotId: 's2', rung: 'surface' },
  { speaker: 'you', text: A.s2a, at: 151, slotId: 's2', rung: 'surface', mode: 'speech' },
  { speaker: 'interviewer', text: 'Which single change moved it most, and how did you measure it?', at: 202, slotId: 's2', rung: 'push' },
  { speaker: 'you', text: A.s2b, at: 220, slotId: 's2', rung: 'push', mode: 'speech' },
  { speaker: 'interviewer', text: Q.s3, at: 298, slotId: 's3', rung: 'surface' },
  { speaker: 'you', text: A.s3, at: 312, slotId: 's3', rung: 'surface', mode: 'speech' },
  { speaker: 'interviewer', text: Q.s4, at: 520, slotId: 's4', rung: 'surface' },
  { speaker: 'you', text: A.s4, at: 535, slotId: 's4', rung: 'surface', mode: 'speech' },
  { speaker: 'interviewer', text: Q.s5, at: 746, slotId: 's5', rung: 'surface' },
  { speaker: 'you', text: A.s5a, at: 760, slotId: 's5', rung: 'surface', mode: 'speech' },
  { speaker: 'interviewer', text: 'Who did you talk to first?', at: 778, slotId: 's5', rung: 'floor' },
  { speaker: 'you', text: A.s5b, at: 787, slotId: 's5', rung: 'floor', mode: 'speech' },
];

export const RESULTS: SlotResult[] = [
  {
    slotId: 's1',
    kind: 'ask',
    topic: 'React and TypeScript',
    question: Q.s1,
    sources: [posting('Strong proficiency in React (must-have), along with JavaScript and TypeScript'), resume('Rebuilt the three-step checkout as a single-page flow in React and TypeScript with optimistic cart updates', '14')],
    verdict: 'defended',
    quote: { text: 'rolled back if the payment API said no, with a short message under the button', at: 20 },
    why: 'You named what you built and what happens when it fails.',
    say: { text: 'Lead with the result you already have: “checkout conversion rose 9.4% in the A/B test.”', placeholders: [], sources: ['Resume · line 15'] },
  },
  {
    slotId: 's2',
    kind: 'ask',
    topic: 'Performance',
    question: Q.s2,
    sources: [posting('Experience with Git and basic performance optimization'), resume('LCP on the payment step dropped from 4.1s to 1.9s', '15')],
    verdict: 'shaky',
    quote: { text: "I'm not sure how we measured it", at: 220 },
    why: "The number is on your resume, but the answer didn't say how it was found or which change was yours.",
    say: {
      text: "“The payment step's LCP was 4.1 seconds. I found [what was slow] with [the tool you used], and [the change] moved it most. It ended at 1.9 seconds.”",
      placeholders: ['what was slow', 'the tool you used', 'the change'],
      sources: ['Resume · line 15', 'Your answer'],
    },
    avoid: { quote: 'We, um, did a lot of things', advice: 'Say which change was yours.' },
    fix: { text: 'Find the pull request or report behind the LCP change and note the one change that moved it most.', minutes: 20 },
  },
  {
    slotId: 's3',
    kind: 'gap',
    topic: 'Responsive and cross-browser',
    question: Q.s3,
    sources: [
      posting('Solid understanding of HTML, CSS, responsive design, and cross-browser compatibility'),
      resume('Own Kite UI, the shared React component library (42 components, typed tokens, Storybook docs) used by 3 product squads.', '16'),
    ],
    verdict: 'defended',
    quote: { text: "the sticky pay button hid behind Safari's bottom toolbar, and I fixed it with safe-area insets", at: 312 },
    why: 'A real bug, the browser it was in, and the fix.',
    newFact: "Tests checkout in Chrome, Safari on iPhone and Samsung Internet; fixed the sticky pay button hiding behind Safari's toolbar with safe-area insets.",
  },
  {
    slotId: 's4',
    kind: 'claim',
    topic: 'Mentoring two interns',
    question: Q.s4,
    sources: [resume('Mentored 2 frontend interns through their 6-month internships; both received full-time offers.', '21')],
    verdict: 'defended',
    quote: { text: 'gave each of them one real ticket from the backlog in the first week', at: 535 },
    why: "You said what you did and how you adjusted for the intern who struggled. The result is on your resume. What you'd change is missing.",
    say: {
      text: "Add one line on what you'd do differently next time: “Next time I'd [what you'd change].”",
      placeholders: ["what you'd change"],
      sources: ['Resume · line 21', 'Your answer'],
    },
    story: { situation: true, action: true, result: true, reflection: false },
  },
  {
    slotId: 's5',
    kind: 'story',
    topic: "A design that couldn't be built",
    question: Q.s5,
    sources: [resume('pair with designers on Figma-to-code handoffs', '20')],
    verdict: 'couldnt',
    quote: { text: "I don't remember a specific time", at: 760 },
    why: "No example yet. That's a story to find before the interview, not a gap in your skills.",
    say: {
      text: '“On [the screen], the design asked for [what it asked]. I [what you changed] after talking to [who], and we shipped [what shipped].”',
      placeholders: ['the screen', 'what it asked', 'what you changed', 'who', 'what shipped'],
      sources: ['Resume · line 20'],
    },
    fix: { text: 'Pick one handoff from your Figma-to-code work and write it as: what the design asked, what you changed, what shipped.', minutes: 15 },
    story: { situation: false, action: false, result: false, reflection: false },
  },
];

const COUNTS: VerdictCounts = { defended: 3, shaky: 1, couldnt: 1, notAssessed: 0 };

export const DEBRIEF: Debrief = {
  sessionId: SESSION_ID,
  kind: 'role',
  title: 'Frontend Engineer at Headout',
  createdAt: CREATED,
  minutes: 14,
  counts: COUNTS,
  firstThing: "Performance. The number is on your resume, but the answer didn't say how it was found or which change was yours.",
  firstSlotId: 's2',
  summary: "Of Headout's 2 asks you show, you defended 1 and 1 was shaky. The gap question went well: add what you said to your profile.",
  results: RESULTS,
  transcript: TRANSCRIPT,
  keptUntil: '2026-11-15',
  role: ROLE,
};

/** The same interview with the fact from question 3 already kept. */
export const DEBRIEF_FACT_ADDED: Debrief = {
  ...DEBRIEF,
  results: RESULTS.map((r) => (r.slotId === 's3' ? { ...r, factAdded: true } : r)),
};

/** An interview ended after three of five: the last two are Not assessed, which never counts against the person. */
export const DEBRIEF_ENDED: Debrief = {
  ...DEBRIEF,
  minutes: 6,
  counts: { defended: 2, shaky: 1, couldnt: 0, notAssessed: 2 },
  summary: "Of Headout's 2 asks you show, you defended 1 and 1 was shaky. The gap question went well: add what you said to your profile.",
  results: [
    ...RESULTS.slice(0, 3),
    {
      slotId: 's4',
      kind: 'claim',
      topic: 'Mentoring two interns',
      question: Q.s4,
      sources: [resume('Mentored 2 frontend interns through their 6-month internships; both received full-time offers.', '21')],
      verdict: 'not_assessed',
      why: '',
    },
    {
      slotId: 's5',
      kind: 'story',
      topic: "A design that couldn't be built",
      question: Q.s5,
      sources: [resume('pair with designers on Figma-to-code handoffs', '20')],
      verdict: 'not_assessed',
      why: '',
    },
  ],
  transcript: TRANSCRIPT.slice(0, 8),
};

/** An interview where every answer was backed up: nothing to work on first. */
export const DEBRIEF_CLEAN: Debrief = {
  ...DEBRIEF,
  counts: { defended: 3, shaky: 0, couldnt: 0, notAssessed: 0 },
  firstThing: undefined,
  firstSlotId: undefined,
  summary: undefined,
  results: [RESULTS[0], RESULTS[2], RESULTS[3]],
  transcript: TRANSCRIPT.filter((t) => ['s1', 's3', 's4'].includes(t.slotId ?? '')),
};

/* ───────────────────────── the work sample ───────────────────────── */

const MATERIAL = `export function CartSummary({ items, coupon }: Props) {
  const [total, setTotal] = useState(sum(items));

  useEffect(() => {
    setTotal(sum(items));
    if (coupon) {
      applyCoupon(coupon).then((off) => setTotal(sum(items) - off));
    }
  }, [items, coupon]);

  return <p className="total">{formatINR(total)}</p>;
}`;

export const TASK: WorkTask = {
  family: 'frontend',
  familyLabel: 'Frontend engineer',
  task: "The cart total flickers when a coupon is applied. Find out why, fix it, and say how you'd test it.",
  materialName: 'CartSummary.tsx',
  material: MATERIAL,
  materialKind: 'code',
  minutes: 10,
  assistant: true,
};

const WORK = `export function CartSummary({ items, coupon }: Props) {
  const [discount, setDiscount] = useState(0);
  const [pending, setPending] = useState(false);

  useEffect(() => {
    let ignore = false;
    if (!coupon) {
      setDiscount(0);
      return;
    }
    setPending(true);
    applyCoupon(coupon).then((off) => {
      if (!ignore) {
        setDiscount(off);
        setPending(false);
      }
    });
    return () => {
      ignore = true;
    };
  }, [coupon]);

  const total = sum(items) - discount;
  return <p className="total">{pending ? 'Applying coupon…' : formatINR(total)}</p>;
}`;

const WORK_CHECKS: CheckResult[] = [
  { check: 'Saw the total shows twice: full, then discounted', verdict: 'defended', quote: 'Apply a coupon, then change it quickly twice; the total should end on the last one.' },
  { check: 'Worked out the total instead of copying it into state', verdict: 'defended', quote: 'const total = sum(items) - discount;' },
  { check: 'Handled a slow or failed coupon call', verdict: 'defended', quote: 'Applying coupon…' },
  { check: 'Ignored an older coupon answer that arrives last', verdict: 'shaky', quote: 'I think it stops a warning?' },
];

const WQ = {
  t1: 'Why work out the total during render instead of storing it?',
  t2: 'The coupon call now takes 3 seconds. What does the person see?',
  t3: 'The assistant suggested useMemo. Why did you change its version?',
};

export const DEBRIEF_WORK: Debrief = {
  sessionId: WORK_ID,
  kind: 'work',
  title: 'Work sample · Frontend engineer',
  createdAt: '2026-10-17T05:20:00.000Z',
  minutes: 18,
  counts: { defended: 2, shaky: 1, couldnt: 0, notAssessed: 0 },
  firstThing: "The assistant's version. Some of it, but not how or what was yours.",
  firstSlotId: 't3',
  results: [
    {
      slotId: 't1',
      kind: 'task',
      topic: 'Working the total out',
      question: WQ.t1,
      sources: [],
      verdict: 'defended',
      quote: { text: 'the total is just items minus the discount, so there is nothing to keep in sync', at: 640 },
      why: 'You named what the total depends on and why a copy in state goes stale.',
    },
    {
      slotId: 't2',
      kind: 'task',
      topic: 'A slow coupon',
      question: WQ.t2,
      sources: [],
      verdict: 'defended',
      quote: { text: 'it shows Applying coupon and the old total stays until it comes back', at: 702 },
      why: 'You said what is on screen while it waits.',
    },
    {
      slotId: 't3',
      kind: 'task',
      topic: "The assistant's version",
      question: WQ.t3,
      sources: [],
      verdict: 'shaky',
      quote: { text: 'I think it stops a warning?', at: 768 },
      why: 'Some of it, but not how or what was yours.',
      say: {
        text: "“I [what you did first] because [why]. I'd test it by [how].”",
        placeholders: ['what you did first', 'why', 'how'],
        sources: ['Your answer'],
      },
      fix: { text: 'Write one line on what the ignore flag does when a second coupon is applied before the first answers.', minutes: 10 },
    },
  ],
  checks: WORK_CHECKS,
  assistantUse: 'Asked the assistant 2 things · changed its version before using it: kept a loading state, renamed the flag',
  transcript: [
    { speaker: 'interviewer', text: WQ.t1, at: 628, slotId: 't1', rung: 'surface' },
    { speaker: 'you', text: 'Because the total is just items minus the discount, so there is nothing to keep in sync. Two copies go stale.', at: 640, slotId: 't1', rung: 'surface', mode: 'speech' },
    { speaker: 'interviewer', text: WQ.t2, at: 690, slotId: 't2', rung: 'surface' },
    { speaker: 'you', text: 'It shows Applying coupon and the old total stays until it comes back.', at: 702, slotId: 't2', rung: 'surface', mode: 'speech' },
    { speaker: 'interviewer', text: WQ.t3, at: 752, slotId: 't3', rung: 'surface' },
    { speaker: 'you', text: 'I kept the flag but renamed it. I think it stops a warning?', at: 768, slotId: 't3', rung: 'surface', mode: 'speech' },
  ],
  keptUntil: '2026-11-16',
  task: TASK,
  work: WORK,
};

/* ───────────────────────── what carries over ───────────────────────── */

const SESSIONS: SessionSummary[] = [
  {
    id: SESSION_ID,
    kind: 'role',
    title: 'Frontend Engineer at Headout',
    createdAt: CREATED,
    status: 'done',
    verdicts: ['defended', 'shaky', 'defended', 'defended', 'couldnt'],
    counts: COUNTS,
    role: ROLE,
  },
  {
    id: EARLIER_ID,
    kind: 'resume',
    title: 'Defend your resume',
    createdAt: EARLIER,
    status: 'done',
    verdicts: ['defended', 'shaky', 'defended', 'shaky', 'defended'],
    counts: { defended: 3, shaky: 2, couldnt: 0, notAssessed: 0 },
  },
];

export const HISTORY: PracticeHistory = {
  sessions: SESSIONS,
  claims: [
    {
      text: 'LCP on the payment step dropped from 4.1s to 1.9s',
      source: 'Resume · line 15',
      best: 'shaky',
      on: '2026-10-12',
      marks: [
        { sessionId: EARLIER_ID, date: '2026-10-12', verdict: 'shaky' },
        { sessionId: SESSION_ID, date: '2026-10-16', verdict: 'shaky' },
      ],
    },
    {
      text: 'Rebuilt the three-step checkout as a single-page flow in React and TypeScript',
      source: 'Resume · line 14',
      best: 'defended',
      on: '2026-10-12',
      marks: [
        { sessionId: EARLIER_ID, date: '2026-10-12', verdict: 'defended' },
        { sessionId: SESSION_ID, date: '2026-10-16', verdict: 'defended' },
      ],
    },
    {
      text: 'Mentored 2 frontend interns',
      source: 'Resume · line 21',
      best: 'defended',
      on: '2026-10-16',
      marks: [{ sessionId: SESSION_ID, date: '2026-10-16', verdict: 'defended' }],
    },
    {
      text: 'Led the storefront migration to React 18 and Vite',
      source: 'Resume · line 17',
      best: 'shaky',
      on: '2026-10-12',
      marks: [{ sessionId: EARLIER_ID, date: '2026-10-12', verdict: 'shaky' }],
    },
    {
      text: 'Own Kite UI, the shared React component library (42 components, typed tokens, Storybook docs)',
      source: 'Resume · line 16',
      best: 'defended',
      on: '2026-10-12',
      marks: [{ sessionId: EARLIER_ID, date: '2026-10-12', verdict: 'defended' }],
    },
    {
      text: 'Maintain motion-kit, a spring-physics animation library for React (212 GitHub stars)',
      source: 'Resume · line 46',
      best: 'defended',
      on: '2026-10-12',
      marks: [{ sessionId: EARLIER_ID, date: '2026-10-12', verdict: 'defended' }],
    },
  ],
  facts: [
    {
      text: "Tests checkout in Chrome, Safari on iPhone and Samsung Internet; fixed the sticky pay button hiding behind Safari's toolbar with safe-area insets.",
      sessionId: SESSION_ID,
      slotId: 's3',
      at: 312,
      date: '2026-10-16',
    },
  ],
  leftToday: 2,
};

export const HISTORY_EMPTY: PracticeHistory = { sessions: [], claims: [], facts: [], leftToday: 3 };

/** A session that was left half way: the hub offers to carry on with it. */
export const HISTORY_UNFINISHED: PracticeHistory = {
  ...HISTORY,
  sessions: [
    { id: '3d4e5f60718293a4b5c6d7e8f90a1b2c', kind: 'stories', title: 'Behavioural stories', createdAt: '2026-10-17T04:40:00.000Z', status: 'active', verdicts: [] },
    ...SESSIONS,
  ],
};
