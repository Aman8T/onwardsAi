/**
 * Dev page (`/dev/prepare`, development builds only): every state of a Prepare thread on fixtures, with no server and
 * no account. Pick a state in the bar, or link straight to one:
 *
 *   /dev/prepare?state=streaming        (`?scheme=dark` for the dark scheme, `?h=120` to pin the hue)
 *
 *   full · stream        the plan for AI Product Engineer (Riya Kapoor, the sample persona), stored, or arriving
 *   streaming · standing the outlines: gaps only, and gaps with their whys while the moves are chosen
 *   error · refused      the moves failed; the request itself refused (a daily window)
 *   thin                 Design Engineer: one gap, one move
 *   requirement          one gap of Senior Frontend Engineer (Automated testing)
 *   no-github            the repo action on a graph without a GitHub source
 *   no-headline          the headline action when no wording passed the check
 *   conflict             the first save is a 409 (another tab saved first); the plan reloads and saves on top
 *   phone                the plan in a 390 px frame: the jump bar, the phone's plan list, sticky actions
 *   practice · practice-phone   Fix steps from practice in the plan's current week (I2): one ticked, two not
 *
 * The data is `riya.*.json` (the contract's shape, by hand from the sample profile); the orchestrator swaps in the
 * backend's golden fixture later. Buttons that leave the thread say where they would go.
 */
import { useMemo, useState } from 'react';
import type { FixTask } from '../../../shared/interview';
import type { PrepareHeadline, PreparePlan, PrepareRepos } from '../../../shared/prepare';
import { Brand } from '../../components/Brand';
import { UserMessage } from '../../views/chat/Message';
import { PrepareThread } from '../PrepareThread';
import { standingLine } from '../plan';
import { mockClient } from './mock';
import type { MockMode } from './mock';
import headlinesJson from './riya.headlines.json';
import planJson from './riya.plan.json';
import reposJson from './riya.repos.json';
import requirementJson from './riya.requirement.json';
import thinJson from './riya.thin.json';
import '../../styles/chat.css';

const FULL = planJson as unknown as PreparePlan;
const THIN = thinJson as unknown as PreparePlan;
const REQUIREMENT = requirementJson as unknown as PreparePlan;
const REPOS = reposJson as unknown as PrepareRepos;
const HEADLINES = headlinesJson as unknown as PrepareHeadline[];
const NO_REPOS: PrepareRepos = { github: false, repos: [] };

/** The plan made eight days ago (so this is week 2), with three Fix steps from practice placed in it: one ticked. */
function withPractice(plan: PreparePlan): PreparePlan {
  const made = new Date(Date.now() - 8 * 24 * 60 * 60 * 1000).toISOString();
  const step = (id: string, slotId: string, claimText: string, text: string, minutes: number, doneAt?: string): FixTask => ({
    id,
    sessionId: '0a1b2c3d4e5f60718293a4b5c6d7e8f9',
    slotId,
    claimText,
    text,
    minutes,
    where: 'plan',
    planId: plan.id,
    addedAt: made,
    ...(doneAt ? { doneAt } : {}),
  });
  return {
    ...plan,
    createdAt: made,
    practice: [
      step('c0ffee00c0ffee00c0ffee00c0ffee11', 's2', 'Built an LLM evaluation harness with 712 attributes', 'Find the run with the clearest result and note what the judge disagreed on.', 20, new Date().toISOString()),
      step('c0ffee00c0ffee00c0ffee00c0ffee12', 's4', 'Set up Opik traces for the evaluation pipeline', 'Write two lines on which traces you looked at first, and why.', 15),
      step('c0ffee00c0ffee00c0ffee00c0ffee13', 's5', 'Kept a worst-model regression gate in CI', "Write down which parts of the gate were yours and which weren't.", 10),
    ],
  };
}

const STATES = [
  ['full', 'Full plan'],
  ['stream', 'Arriving'],
  ['streaming', 'Gaps only'],
  ['standing', 'Choosing the move'],
  ['error', 'Error'],
  ['refused', 'Refused'],
  ['thin', 'Thin · one move'],
  ['requirement', 'One gap'],
  ['no-github', 'No GitHub'],
  ['no-headline', 'No headline'],
  ['conflict', 'Save conflict'],
  ['phone', 'Phone'],
  ['practice', 'Fix steps from practice'],
  ['practice-phone', 'Fix steps · phone'],
] as const;
type StateId = (typeof STATES)[number][0];

const isState = (s: string | null): s is StateId => STATES.some(([id]) => id === s);

function readState(): StateId {
  const s = new URLSearchParams(window.location.search).get('state');
  return isState(s) ? s : 'full';
}

interface Scene {
  plan: PreparePlan;
  repos: PrepareRepos;
  headlines: PrepareHeadline[];
  mode: MockMode;
  /** The thread already knows the stored plan's id. */
  known: boolean;
  narrow: boolean;
}

function sceneFor(id: StateId): Scene {
  const base: Scene = {
    plan: FULL,
    repos: REPOS,
    headlines: HEADLINES.slice(0, 2),
    mode: 'stored',
    known: true,
    narrow: false,
  };
  switch (id) {
    case 'full':
      return base;
    case 'stream':
      return { ...base, mode: 'stream', known: false };
    case 'streaming':
      return { ...base, mode: 'hang', known: false };
    case 'standing':
      return { ...base, mode: 'hang-moves', known: false };
    case 'error':
      return { ...base, mode: 'error', known: false };
    case 'refused':
      return { ...base, mode: 'refused', known: false };
    case 'thin':
      return { ...base, plan: THIN };
    case 'requirement':
      return { ...base, plan: REQUIREMENT };
    case 'no-github':
      return { ...base, repos: NO_REPOS };
    case 'no-headline':
      return { ...base, headlines: [HEADLINES[2]] };
    case 'conflict':
      return { ...base, mode: 'conflict' };
    case 'phone':
      return { ...base, narrow: true };
    case 'practice':
      return { ...base, plan: withPractice(FULL) };
    case 'practice-phone':
      return { ...base, plan: withPractice(FULL), narrow: true };
  }
}

export function PrepareGallery() {
  const [state, setState] = useState<StateId>(readState);
  const [note, setNote] = useState('');

  const go = (next: StateId) => {
    setState(next);
    setNote('');
    window.history.replaceState(null, '', `/dev/prepare?state=${next}${window.location.search.includes('scheme=dark') ? '&scheme=dark' : ''}`);
  };

  return (
    <div className="pp-dev">
      <header className="pp-dev-header">
        <Brand />
        <span className="kicker">Dev only · Prepare on fixtures, no server</span>
        <nav className="pp-dev-nav" aria-label="States">
          {STATES.map(([id, label]) => (
            <button key={id} type="button" className={`pp-dev-navbtn${state === id ? ' is-current' : ''}`} aria-pressed={state === id} onClick={() => go(id)}>
              {label}
            </button>
          ))}
        </nav>
        <p className="pp-dev-note" role="status">
          {note}
        </p>
      </header>
      <main className="pp-dev-main">
        <Stage key={state} id={state} onNote={setNote} />
      </main>
    </div>
  );
}

function Stage({ id, onNote }: { id: StateId; onNote: (s: string) => void }) {
  const scene = useMemo(() => sceneFor(id), [id]);
  // a fresh run per state: the key carries the state id, so a run from another state is never reused
  const profileId = `${scene.plan.profileId}-${id}`;
  const client = useMemo(
    () =>
      mockClient({
        plan: { ...scene.plan, profileId },
        repos: scene.repos,
        headlines: scene.headlines,
        mode: scene.mode,
      }),
    [scene, profileId],
  );
  const [planId, setPlanId] = useState<string | undefined>(scene.known ? scene.plan.id : undefined);

  const question =
    scene.plan.scope === 'requirement'
      ? `Help me prepare for ${scene.plan.requirement} for the ${scene.plan.roleTitle} role.`
      : `How do I prepare for the ${scene.plan.roleTitle} role?`;
  const title = scene.plan.scope === 'requirement' ? (scene.plan.requirement ?? scene.plan.roleTitle) : scene.plan.roleTitle;

  const thread = (
    <>
      <div className="pp-dev-head">
        <span className="kicker">
          {title} · {scene.plan.scope === 'requirement' ? `${scene.plan.roleTitle} · gap` : standingLine(scene.plan)}
        </span>
        <span className="kicker">Started from the graph · just now</span>
      </div>
      <UserMessage text={question} caption={`from the graph · ${title}`} />
      <PrepareThread
        client={client}
        profileId={profileId}
        roleId={scene.plan.roleId}
        requirement={scene.plan.requirement}
        planId={planId}
        onPlanId={setPlanId}
        onJustThis={(name) => onNote(`Gallery: opens a thread that prepares for just "${name}" (scope requirement). See ?state=requirement.`)}
        onPractice={() => onNote('Gallery: opens Practice · This role (/practice?screen=setup&kind=role&graphRole=<role id>).')}
        onRedrill={() => onNote('Gallery: opens Re-drill what wobbled (/practice?screen=redrill).')}
        narrow={scene.narrow}
      />
    </>
  );

  return scene.narrow ? <div className="pp-dev-frame scroll">{thread}</div> : thread;
}
