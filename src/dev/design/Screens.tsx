/**
 * The five screens, each the real component fed from fixtures. No server: calls that Home makes on mount
 * (api.health, api.sampleProfile, the Weekly tile) fail softly, and each screen sits in an error boundary.
 */
import { Component, useMemo, useState } from 'react';
import type { ReactNode } from 'react';
import { MotionConfig } from 'motion/react';
import type { Profile } from '../../../shared/types';
import type { WeeklySubscription } from '../../../shared/weekly';
import sampleProfile from '../../../backend/tayyari/sample/sample-profile.json';
import { MatchView } from '../../match/MatchView';
import { FIXTURES } from '../../match/dev/fixtures';
import { SAMPLE_ARCHIVE, SAMPLE_EDITION } from '../../weekly/fixtures';
import { WeeklyRoom } from '../../weekly/WeeklyRoom';
import { Dashboard } from '../../views/Dashboard';
import { GraphView } from '../../views/GraphView';
import { Onboarding } from '../../views/Onboarding';
import { DESK_SEED, EMPTY_SEED } from './deskFixtures';
import type { ScreenId } from './state';
import '../../styles/match.css';
import '../../styles/weekly.css';

const PROFILE = sampleProfile as unknown as Profile;
const noop = () => {};
const nap = (ms: number) => new Promise((r) => setTimeout(r, ms));

class Boundary extends Component<{ name: string; children: ReactNode }, { error: string | null }> {
  state = { error: null as string | null };
  static getDerivedStateFromError(e: unknown) {
    return { error: e instanceof Error ? e.message : String(e) };
  }
  render() {
    if (this.state.error === null) return this.props.children;
    return (
      <div className="panel dp-fail" role="alert">
        <p className="kicker">{this.props.name} could not render without the server</p>
        <p>{this.state.error}</p>
      </div>
    );
  }
}

const ACTIVE: WeeklySubscription = {
  status: 'active',
  email: 'riya@example.com',
  day: 'mon',
  sections: ['jobs', 'progress', 'news', 'tip'],
  unread: 1,
};

/** Riya three weeks before the sample's analysis: a lower Design & Motion, Design Engineer third, no portfolio yet; the dashboard says what moved. */
const PREVIOUS: Profile = {
  ...PROFILE,
  id: 'previous-riya',
  createdAt: '2026-09-14T09:00:00.000Z',
  mastery: { ...PROFILE.mastery, score: 74 },
  domains: PROFILE.domains.map((d) => (d.id === 'design-motion' ? { ...d, score: 79 } : d)),
  roles: PROFILE.roles.map((r) => (r.id === 'design-engineer' ? { ...r, alignment: 77 } : r)),
  sources: PROFILE.sources.filter((s) => s.kind !== 'portfolio'),
};

/** `?desk=empty` is the desk of someone with only the analysis: no previous one, nothing practised, saved or asked. */
function DashboardScreen() {
  const empty = new URLSearchParams(window.location.search).get('desk') === 'empty';
  return <Dashboard profile={PROFILE} previous={empty ? null : PREVIOUS} seed={empty ? EMPTY_SEED : DESK_SEED} />;
}

function GraphScreen() {
  return <GraphView profile={PROFILE} />;
}

function MatchScreen() {
  const fx = FIXTURES.riya;
  return (
    <div className="mt-view">
      <main className="mt-main">
        <MatchView
          posting={fx.posting}
          facts={fx.facts}
          coverage={fx.result.coverage}
          gates={fx.result.gates}
          blocked={fx.result.blocked}
          radar={fx.result.radar}
          questions={fx.result.questions}
          result={fx.result}
          reading={false}
          createdAt={new Date(Date.now() - 2 * 60_000).toISOString()}
          recheck={{ status: 'idle' }}
          reveal={false}
          onAnswer={noop}
          onBuildResume={noop}
        />
      </main>
    </div>
  );
}

function WeeklyScreen() {
  const [sub, setSub] = useState(ACTIVE);
  const archive = useMemo(() => SAMPLE_ARCHIVE.map((a) => ({ ...a })), []);
  return (
    <main className="wk-main">
      <WeeklyRoom
        mode="ready"
        subscription={sub}
        onSaved={setSub}
        save={async (u) => {
          await nap(400);
          return { ...sub, status: u.status, day: u.day, sections: u.sections };
        }}
        editions={archive}
        edition={SAMPLE_EDITION}
        onOpenEdition={noop}
      />
    </main>
  );
}

const VIEWS: Record<ScreenId, { name: string; View: () => ReactNode }> = {
  home: { name: 'Empty dashboard', View: () => <Onboarding /> },
  dashboard: { name: 'Dashboard', View: DashboardScreen },
  graph: { name: 'Next role', View: GraphScreen },
  match: { name: 'Match', View: MatchScreen },
  weekly: { name: 'Weekly', View: WeeklyScreen },
};

export function Screen({ id }: { id: ScreenId }) {
  const { name, View } = VIEWS[id];
  return (
    <Boundary name={name} key={id}>
      <MotionConfig reducedMotion="user">
        <View />
      </MotionConfig>
    </Boundary>
  );
}
