/**
 * The Practice section: the header and the screen, wired to the store. The address says which screen:
 *
 *   /practice                                              the hub
 *   /practice?screen=setup&kind=role&run=…&job=…           the plan, before it starts (the room's own side: `setup/`)
 *   /practice?screen=setup&kind=role&graphRole=…           the same for a role of the person's graph
 *   /practice?screen=room&session=…                        the interview (`room/`; it has a bar of its own, no header)
 *   /practice?screen=debrief&session=…                     what it showed
 *   /practice?screen=progress                              what carries over
 *
 * The plan and the room load on first visit; the hub, the debrief and progress are this folder's. A new screen starts at
 * the top of the page.
 */
import { Suspense, lazy, useEffect, useRef } from 'react';
import type { Profile } from '../../shared/types';
import { useApp } from '../store';
import { DebriefScreen } from './debrief/DebriefScreen';
import { HubScreen } from './hub/HubScreen';
import { PracticeHeader } from './hub/PracticeHeader';
import { ProgressScreen } from './progress/ProgressScreen';
import '../styles/practice.css';

// The plan and the room (speech, the work sample's workspace) load only when a person gets that far.
const SetupScreen = lazy(() => import('./setup/SetupScreen').then((m) => ({ default: m.SetupScreen })));
const RoomScreen = lazy(() => import('./room/RoomScreen').then((m) => ({ default: m.RoomScreen })));

export function PracticeRoot({ profile }: { profile: Profile }) {
  const route = useApp((s) => s.practiceRoute);

  // a new screen starts at the top (not on first paint)
  const seen = useRef(`${route.screen}|${route.session ?? ''}`);
  useEffect(() => {
    const now = `${route.screen}|${route.session ?? ''}`;
    if (seen.current === now) return;
    seen.current = now;
    window.scrollTo({ top: 0 });
  }, [route.screen, route.session]);

  // the interview has a bar of its own, so nothing pulls the person out of it
  if (route.screen === 'room' && route.session) {
    return (
      <Suspense fallback={null}>
        <RoomScreen key={route.session} profile={profile} sessionId={route.session} />
      </Suspense>
    );
  }

  return (
    <div className="pc-view">
      <PracticeHeader profileId={profile.id} />
      <main className="pc-main">
        {route.screen === 'setup' && route.kind && (
          <Suspense fallback={null}>
            <SetupScreen
              key={`${route.kind}|${route.run ?? ''}|${route.job ?? ''}|${route.graphRole ?? ''}`}
              profile={profile}
              kind={route.kind}
              role={route.run && route.job ? { runId: route.run, jobId: route.job } : undefined}
              graphRole={route.graphRole ? { roleId: route.graphRole } : undefined}
            />
          </Suspense>
        )}
        {route.screen === 'debrief' && route.session && <DebriefScreen key={route.session} profile={profile} sessionId={route.session} />}
        {route.screen === 'progress' && <ProgressScreen profile={profile} />}
        {route.screen === 'hub' && <HubScreen profile={profile} />}
      </main>
    </div>
  );
}
