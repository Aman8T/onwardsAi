/**
 * What the Ask coach can hand the person as a button: the one list. `backend/tayyari/contract/capabilities.py` mirrors it
 * field for field and in the same order (`npm run check:capabilities` keeps `backend/tests/data/capabilities.json` in step;
 * the Python tests compare the mirror to that file), the coach's prompt and the Ask panel are generated from it, and
 * nothing here is a tool: a button only opens a screen, it never does anything on the person's behalf.
 *
 * Every destination maps to something the app really has: a store action (`openRole`, `openPractice`, `openMatch`,
 * `openDiscover`, `openResume`, `openWeekly`, `openPricing`, `openPortfolio`, `openChat`, `goto('onboarding')`) and, where
 * the client has an address for it, a path in `APP_ROUTES` (`backend/tayyari/api/static.py`). The client has no router;
 * `store.view` drives it, so `route` says where the screen lives, not how it is reached.
 */

export type Destination =
  | 'open_role'
  | 'practice'
  | 'redrill'
  | 'match'
  | 'discover'
  | 'open_application'
  | 'apply'
  | 'open_plan'
  | 'portfolio'
  | 'resumes'
  | 'share_card'
  | 'weekly'
  | 'new_analysis'
  | 'pricing';

/** What the button's target id names: nothing, a next role of the graph, a stored Prepare plan, a posting match (the
 * person's own, loaded by the server), a tracked role (from the person's browser), or a skill of the graph. */
export type TargetKind = 'none' | 'role' | 'plan' | 'match' | 'tracked' | 'skill';

export interface Capability {
  destination: Destination;
  /** The button's words. */
  label: string;
  target: TargetKind;
  /** One line for the prompt: what the button opens. Never says it does something. */
  can: string;
  /** The path of the screen in the app's own routes, or null when it is a sheet or a view with no address. */
  route: string | null;
}

export const CAPABILITIES: readonly Capability[] = [
  { destination: 'open_role', label: 'Open this role', target: 'role', can: 'opens one of their next roles on the graph', route: '/dashboard' },
  { destination: 'practice', label: 'Start Practice', target: 'none', can: 'opens Practice, where they choose an interview to take', route: '/practice' },
  { destination: 'redrill', label: 'Re-drill weak claims', target: 'none', can: 'opens the claims that wobbled in Practice, to take again', route: '/practice' },
  { destination: 'match', label: 'Match a posting', target: 'none', can: 'opens the place where they paste a job posting to see it against their evidence', route: '/match' },
  { destination: 'discover', label: 'Search roles', target: 'none', can: 'opens Discover, where roles are searched', route: '/discover' },
  { destination: 'open_application', label: 'Open application', target: 'tracked', can: 'opens one of their tracked roles in Your applications', route: '/discover' },
  { destination: 'apply', label: 'Apply for me', target: 'match', can: 'opens Apply for me for one of their posting matches, where they check every value before anything is sent', route: '/apply' },
  { destination: 'open_plan', label: 'Open your plan', target: 'plan', can: 'opens one of their preparation plans', route: '/dashboard' },
  { destination: 'portfolio', label: 'Open your portfolio', target: 'none', can: 'opens their portfolio page', route: null },
  { destination: 'resumes', label: 'Open your resumes', target: 'none', can: 'opens their resumes', route: '/resume' },
  { destination: 'share_card', label: 'Share your radar card', target: 'none', can: 'opens the sheet where they choose whether to share their radar card', route: null },
  { destination: 'weekly', label: 'Read the Weekly', target: 'none', can: 'opens the Weekly', route: '/weekly' },
  { destination: 'new_analysis', label: 'New analysis', target: 'none', can: 'opens the page where they upload a resume for a new analysis', route: null },
  { destination: 'pricing', label: 'See pricing', target: 'none', can: 'opens the pricing page', route: '/pricing' },
];

/** What the coach is given about the person, one line each (the Ask panel's "Answer, from what it can see"). */
export const CAN_SEE: readonly string[] = [
  'Your skill graph, each score and the evidence under it',
  'Your five next roles and what each percentage is made of',
  'Your applications, their stages and your notes',
  'Your practice sessions and the claims that wobbled',
  'Your plan, its weeks and what is ticked',
  'Your posting matches and their open questions',
  'How any screen in the app works',
  'The public web, when a question needs it, with the source named',
];

/** What the coach does not do here, one line each (the Ask panel's "Not here"). */
export const CANNOT: readonly string[] = [
  'Submit or approve an application',
  'Send an email or a message, or book a calendar slot',
  'Change your resume, portfolio or notes',
  "Change your plan's ticks or an application's stage",
  'Compare you with other people: there is no cohort',
  'Say anything about you that no fact supports',
];
