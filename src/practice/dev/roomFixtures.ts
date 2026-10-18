/**
 * Fixtures for the room, the work sample and the setup page: the sample profile's Headout interview of the Interview
 * canvas (questions, answers and plan word for word), the plan for a role of her graph (Senior Frontend Engineer, as the
 * server plans it), and the frontend work sample. Used by `roomStates.tsx`; nothing here is sent anywhere and no server is
 * needed.
 */
import type { AssistantTurn, GraphRoleSource, InterviewPlan, InterviewSession, PlanSlot, Turn, WorkTask } from '../../../shared/interview';
import type { Role } from '../../../shared/types';
import { deriveRoom } from '../room/model';
import type { RoomModel, RoomUi } from '../room/model';

export const DEV_PROFILE_ID = 'dev-fixture-practice';
export const DEV_SESSION_ID = 'dev00000000000000000000000000001';
export const DEV_ROLE = { runId: 'dev-run-20261016', jobId: 'headout-frontend' };

/* ───────────────────────── the Headout interview ───────────────────────── */

export const SLOTS: PlanSlot[] = [
  {
    id: 's1',
    kind: 'ask',
    topic: 'React and TypeScript',
    sources: [
      { origin: 'posting', text: 'Strong proficiency in React (must-have), along with JavaScript and TypeScript' },
      { origin: 'evidence', text: 'Rebuilt the three-step checkout as a single-page flow in React and TypeScript', source: 'Resume · line 14' },
    ],
  },
  {
    id: 's2',
    kind: 'ask',
    topic: 'Performance',
    sources: [
      { origin: 'posting', text: 'Experience with Git and basic performance optimization' },
      { origin: 'evidence', text: 'LCP on the payment step dropped from 4.1s to 1.9s', source: 'Resume · line 15' },
    ],
  },
  {
    id: 's3',
    kind: 'gap',
    topic: 'Responsive and cross-browser',
    sources: [
      { origin: 'posting', text: 'Solid understanding of HTML, CSS, responsive design, and cross-browser compatibility' },
      {
        origin: 'evidence',
        text: 'Own Kite UI, the shared React component library (42 components, typed tokens, Storybook docs) used by 3 product squads.',
        source: 'Resume · line 16',
      },
    ],
  },
  {
    id: 's4',
    kind: 'claim',
    topic: 'Mentoring two interns',
    sources: [{ origin: 'evidence', text: 'Mentored 2 frontend interns through their 6-month internships; both received full-time offers.', source: 'Resume · line 21' }],
  },
  {
    id: 's5',
    kind: 'story',
    topic: "A design that couldn't be built",
    sources: [{ origin: 'evidence', text: 'pair with designers on Figma-to-code handoffs', source: 'Resume · line 20' }],
  },
];

export const QUESTIONS = [
  'Walk me through the checkout rebuild. What did you change first, and why?',
  "Your resume says the payment step's LCP went from 4.1s to 1.9s. How did you find what was slow?",
  'How do you make sure a Kite UI component works on every screen size and browser your customers use?',
  'You mentored two interns who both got offers. What did you do that made a difference?',
  "Tell me about a time a design couldn't be built the way it was drawn. What did you do?",
];

export const PUSH_S2 = 'Which single change moved it most, and how did you measure it?';

export const ANSWER_S1 =
  'I merged the three steps into one page with a reducer for the checkout state. The cart updated before the server answered and rolled back if the payment API said no, with a short message under the button.';
export const ANSWER_S2 = 'We, um, did a lot of things. Images, the bundle, some fonts. And it got a lot faster.';
export const ANSWER_S2_PUSH = "I think the biggest one was the images? I'm not sure how we measured it, the PM had a dashboard.";

const you = (text: string, at: number, slotId: string, rung: Turn['rung'] = 'surface', mode: Turn['mode'] = 'speech'): Turn => ({ speaker: 'you', text, at, slotId, rung, mode });
const them = (text: string, at: number, slotId: string, rung: Turn['rung'] = 'surface'): Turn => ({ speaker: 'interviewer', text, at, slotId, rung });

const T1: Turn[] = [them(QUESTIONS[0], 0, 's1'), you(ANSWER_S1, 20, 's1')];
const T2: Turn[] = [them(QUESTIONS[1], 62, 's2'), you(ANSWER_S2, 151, 's2')];
const T2_PUSH: Turn[] = [them(PUSH_S2, 160, 's2', 'push'), you(ANSWER_S2_PUSH, 220, 's2', 'push')];

const FULL_MINUTES = 20;

const base = (over: Partial<InterviewSession>): InterviewSession => ({
  id: DEV_SESSION_ID,
  profileId: DEV_PROFILE_ID,
  kind: 'role',
  title: 'Frontend Engineer at Headout',
  status: 'active',
  createdAt: new Date(Date.now() - 14 * 60_000).toISOString(),
  slots: SLOTS,
  slotIndex: 1,
  turns: [],
  minutes: FULL_MINUTES,
  role: DEV_ROLE,
  ...over,
});

/** Question 2 is being asked. */
export const SESSION_ASKING = base({ slotIndex: 1, current: { kind: 'question', text: QUESTIONS[1], slotId: 's2', rung: 'surface' }, turns: T1 });

/** Her first answer to question 2 is in; the push is being asked. */
export const SESSION_FOLLOWUP = base({
  slotIndex: 1,
  current: { kind: 'followup', text: PUSH_S2, slotId: 's2', rung: 'push' },
  turns: [...T1, ...T2],
});

/** She said "I don't know" to question 2: the reply comes first, then question 3. */
export const SESSION_DONTKNOW = base({
  slotIndex: 2,
  current: {
    kind: 'question',
    lead: "That's fine. I'll note it and we'll move on. You'll get a step to close it.",
    text: QUESTIONS[2],
    slotId: 's3',
    rung: 'surface',
  },
  turns: [...T1, them(QUESTIONS[1], 62, 's2'), you("I don't know.", 98, 's2')],
});

/** Question 3 is being asked. */
export const SESSION_NEXT = base({ slotIndex: 2, current: { kind: 'question', text: QUESTIONS[2], slotId: 's3', rung: 'surface' }, turns: [...T1, ...T2, ...T2_PUSH] });

/** Question 4 is being asked, after three. */
export const SESSION_Q4 = base({ slotIndex: 3, current: { kind: 'question', text: QUESTIONS[3], slotId: 's4', rung: 'surface' }, turns: [...T1, ...T2, ...T2_PUSH] });

/** The last line, said; the debrief is being written. */
export const SESSION_DONE = base({
  slotIndex: 5,
  current: { kind: 'done', text: "That's the last question. Thank you. Your debrief is ready." },
  turns: [...T1, ...T2, ...T2_PUSH],
});

/* ───────────────────────── the plan page ───────────────────────── */

export const PLAN_FULL: InterviewPlan = { kind: 'role', title: 'Frontend Engineer at Headout', slots: SLOTS, minutes: 20, role: DEV_ROLE };
export const PLAN_SHORT: InterviewPlan = { kind: 'role', title: 'Frontend Engineer at Headout', slots: SLOTS.slice(0, 3), minutes: 8, role: DEV_ROLE };
export const PLAN_THIN: InterviewPlan = {
  ...PLAN_SHORT,
  slots: SLOTS.slice(0, 3),
  note: 'Your profile is short, so this interview has 3 questions, not 5. Add a project or a link to get more.',
};

/* ───────────────────────── a role of the graph ───────────────────────── */

/** The sample profile's Senior Frontend Engineer role (`Profile.roles`), requirements as the analysis read them. */
export const GRAPH_ROLE: Role = {
  id: 'senior-frontend-engineer',
  title: 'Senior Frontend Engineer',
  alignment: 78,
  eta: '~6 months',
  why: 'Owns checkout and Kite UI with metrics and mentoring. Testing depth and cross-team scope beyond one product are the gaps.',
  requirements: [
    { name: 'React', skillId: 'react', have: true, current: 90, target: 88 },
    { name: 'Web performance', skillId: 'web-performance', have: true, current: 74, target: 80 },
    { name: 'Accessibility (WCAG)', skillId: 'accessibility', have: true, current: 76, target: 75 },
    { name: 'Automated testing', skillId: 'testing', have: false, current: 48, target: 75 },
    { name: 'Mentoring & review', skillId: 'mentoring-review', have: true, current: 68, target: 75 },
    { name: 'System design', have: false, current: 45, target: 70 },
  ],
};

export const DEV_GRAPH_ROLE: GraphRoleSource = { roleId: GRAPH_ROLE.id };

/** What `POST /api/interview/plan` gives for it: the biggest gaps, then what she can defend, then a story. */
export const GRAPH_SLOTS: PlanSlot[] = [
  {
    id: 's1',
    kind: 'gap',
    topic: 'Automated testing',
    sources: [
      { origin: 'role', text: 'Automated testing' },
      { origin: 'evidence', text: 'Vitest and Jest listed; no testing bullet at work', source: 'Resume' },
    ],
  },
  {
    id: 's2',
    kind: 'gap',
    topic: 'System design',
    sources: [
      { origin: 'role', text: 'System design' },
      { origin: 'evidence', text: 'Owns Kite UI: 42 components, 3 squads, Storybook', source: 'Resume' },
    ],
  },
  { id: 's3', kind: 'claim', topic: 'React', sources: [{ origin: 'evidence', text: 'Rebuilt Kitebird checkout in React; owns 42-component Kite UI', source: 'Resume' }] },
  { id: 's4', kind: 'claim', topic: 'Accessibility (WCAG)', sources: [{ origin: 'evidence', text: 'WCAG 2.1 AA checkout audit: 37 issues logged, 21 fixed', source: 'Portfolio' }] },
  { id: 's5', kind: 'story', topic: 'Mentoring & review', sources: [{ origin: 'evidence', text: 'Mentored 2 interns, both hired full-time; reviews for 4-person team', source: 'Resume' }] },
];

export const GRAPH_PLAN_FULL: InterviewPlan = { kind: 'role', title: GRAPH_ROLE.title, slots: GRAPH_SLOTS, minutes: 20, graphRole: DEV_GRAPH_ROLE };
export const GRAPH_PLAN_SHORT: InterviewPlan = { ...GRAPH_PLAN_FULL, slots: GRAPH_SLOTS.slice(0, 3), minutes: 12 };

/* ───────────────────────── the work sample ───────────────────────── */

export const CART_BEFORE = `export function CartSummary({ items, coupon }: Props) {
  const [total, setTotal] = useState(sum(items));

  useEffect(() => {
    setTotal(sum(items));
    if (coupon) {
      applyCoupon(coupon).then((off) => setTotal(sum(items) - off));
    }
  }, [items, coupon]);

  return <p className="total">{formatINR(total)}</p>;
}`;

export const CART_AFTER = `export function CartSummary({ items, coupon }: Props) {
  const [discount, setDiscount] = useState(0);
  const [pending, setPending] = useState(false);

  useEffect(() => {
    if (!coupon) {
      setDiscount(0);
      setPending(false);
      return;
    }
    let ignoreStale = false;
    setPending(true);
    applyCoupon(coupon)
      .then((off) => !ignoreStale && setDiscount(off))
      .catch(() => !ignoreStale && setDiscount(0))
      .finally(() => !ignoreStale && setPending(false));
    return () => { ignoreStale = true; };
  }, [coupon]);

  const total = sum(items) - discount;

  return (
    <p className="total">
      {pending ? "Applying coupon…" : formatINR(total)}
    </p>
  );
}`;

export const TEST_NOTE = "Apply a coupon, then change it quickly twice; the total should end on the last one. Throttle the network to see the pending state.";

export const WORK_TASK: WorkTask = {
  family: 'frontend',
  familyLabel: 'Frontend engineer',
  task: "The cart total flickers when a coupon is applied. Find out why, fix it, and say how you'd test it.",
  materialName: 'CartSummary.tsx',
  material: CART_BEFORE,
  materialKind: 'code',
  minutes: 10,
  assistant: true,
};

export const TABLE_TASK: WorkTask = {
  family: 'data',
  familyLabel: 'Data analyst',
  task: 'Weekly orders by city, 8 rows. Two numbers are wrong. Find them and write one line for your manager.',
  materialName: 'Weekly orders by city',
  material: ['City\tOrders\tRevenue (₹)', 'Bengaluru\t1,240\t18,60,000', 'Pune\t610\t9,15,000', 'Pune\t610\t9,15,000', 'Chennai\t480\t7.2', 'Hyderabad\t530\t7,95,000'].join('\n'),
  materialKind: 'table',
  minutes: 10,
  assistant: true,
};

const TASK_SLOTS: PlanSlot[] = [
  { id: 's1', kind: 'task', topic: 'Working out the total', sources: [] },
  { id: 's2', kind: 'task', topic: 'A slow coupon call', sources: [] },
  { id: 's3', kind: 'task', topic: 'Using the assistant', sources: [] },
];

export const SESSION_WORK = base({
  kind: 'work',
  title: 'Work sample · Frontend engineer',
  role: undefined,
  slots: TASK_SLOTS,
  slotIndex: 0,
  minutes: 19,
  task: WORK_TASK,
  current: { kind: 'question', text: WORK_TASK.task },
  createdAt: new Date(Date.now() - 6 * 60_000).toISOString(),
});

export const SESSION_WORK_TABLE = { ...SESSION_WORK, task: TABLE_TASK, title: 'Work sample · Data analyst', current: { kind: 'question' as const, text: TABLE_TASK.task } };

export const ASSISTANT_LOG: AssistantTurn[] = [
  { you: 'why does the total flicker', assistant: 'The effect sets the full total first, then the discounted one when applyCoupon resolves, so two values render.' },
  {
    you: 'write a version that ignores stale coupon answers',
    assistant: `A late answer can overwrite a newer one. A flag in the cleanup lets the effect ignore it.

\`\`\`tsx
useEffect(() => {
  let ignore = false;
  applyCoupon(coupon).then((off) => {
    if (!ignore) setDiscount(off);
  });
  return () => { ignore = true; };
}, [coupon]);
\`\`\``,
  },
];

/* ───────────────────────── building a room ───────────────────────── */

export const LIVE_BARS = [8, 14, 10, 20, 26, 16, 9, 12, 22, 18, 11, 7, 15, 24, 19, 10, 13, 21, 9, 17, 12, 8, 14, 10] as const;
export const LIVE_BARS_2 = [6, 10, 16, 12, 8, 20, 24, 14, 9, 18, 22, 12, 7, 11, 17, 23, 13, 9, 15, 21, 10, 8, 12, 6] as const;

/** The quiet defaults of a room: the interviewer has just finished, speech on a browser that can do it. */
export const UI: RoomUi = {
  gate: false,
  answer: 'speech',
  listen: 'off',
  speaking: false,
  draft: '',
  interim: '',
  listenBy: 'browser',
  writing: false,
  silent: false,
  speechSupported: true,
  voiceSupported: true,
  voiceOn: true,
  slow: false,
  voiceBlocked: false,
  sending: false,
  whyOpen: false,
  confirmEnd: false,
  finishing: 'no',
  clock: () => 0,
};

export function roomModel(session: InterviewSession, ui: Partial<RoomUi> = {}): RoomModel {
  return deriveRoom(session, { ...UI, ...ui });
}
