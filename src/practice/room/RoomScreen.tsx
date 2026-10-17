/**
 * The interview room, wired: loads the interview (or carries on after a reload), runs the state machine, and hands the
 * model to the drawing. A work sample shows its workspace until the work is handed in; every other interview, and a work
 * sample's questions, are the room. Space is "Done answering" while listening, when focus is not in a text field.
 *
 * When the last question is answered, or the person ends the interview, `endSession` makes the debrief and the address
 * moves to it: `openPractice({ screen: 'debrief', session })`.
 */
import { useEffect, useRef } from 'react';
import type { Profile } from '../../../shared/types';
import { Brand } from '../../components/Brand';
import { NARROW_QUERY, useMediaQuery } from '../../lib/ui';
import { useInterview, usePracticeNav } from '../useInterview';
import type { UseInterview } from '../useInterview';
import { WorkWorkspace } from '../work/WorkWorkspace';
import type { RoomActions, RoomModel } from './model';
import { RoomView } from './RoomView';
import '../../styles/practice-room.css';

export interface RoomScreenProps {
  profile: Profile;
  sessionId: string;
}

/** Space finishes the answer, unless the focus is somewhere that has its own use for Space. */
function useSpaceKey(model: RoomModel | null, actions: RoomActions) {
  const latest = useRef({ model, actions });
  latest.current = { model, actions };
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.code !== 'Space' && e.key !== ' ') return;
      if (e.repeat || e.ctrlKey || e.metaKey || e.altKey || e.shiftKey) return;
      const { model: m, actions: a } = latest.current;
      if (!m || m.answer !== 'speech' || m.gate || m.sending || m.confirmEnd || m.finishing !== 'no' || m.done) return;
      if (m.listen !== 'listening' && m.listen !== 'paused') return;
      if (m.problem?.kind === 'dropped') return;
      const target = e.target instanceof Element ? e.target : null;
      if (target?.closest('input, textarea, select, button, summary, dialog, a[href], [role="button"], [contenteditable=""], [contenteditable="true"]')) return;
      e.preventDefault();
      a.done();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);
}

export function RoomScreen({ profile, sessionId }: RoomScreenProps) {
  // a different interview is a fresh room: its own load, its own words
  return <RoomBody key={`${profile.id}:${sessionId}`} profileId={profile.id} sessionId={sessionId} />;
}

function RoomBody({ profileId, sessionId }: { profileId: string; sessionId: string }) {
  const open = usePracticeNav();
  const narrow = useMediaQuery(NARROW_QUERY);
  const iv = useInterview({
    profileId,
    sessionId,
    // the debrief replaces the room in the history: Back from it must not land on a finished room that sends it forward again
    onDebrief: (id) => open({ screen: 'debrief', session: id }, { replace: true }),
    onLeave: () => open({ screen: 'hub' }),
  });
  useSpaceKey(iv.model, iv.actions);
  return <RoomContent iv={iv} narrow={narrow} onLeave={() => open({ screen: 'hub' })} />;
}

/** Everything the room shows for one `UseInterview`. */
export function RoomContent({ iv, narrow, onLeave }: { iv: UseInterview; narrow: boolean; onLeave: () => void }) {
  const { load, model, actions, work } = iv;

  if (load.status !== 'ready' || !model) {
    return (
      <div className="rm-room">
        <header className="rm-bar">
          <Brand onClick={onLeave} />
          <span className="rm-faint" aria-hidden="true">
            ·
          </span>
          <span className="rm-bar-title">Practice</span>
        </header>
        <main className="rm-main">
          <div className="rm-col rm-col-solo">
            {load.status === 'error' ? (
              <section className="panel rm-answer">
                <p role="alert">{load.message}</p>
                <div className="rm-buttons">
                  {!load.gone && (
                    <button type="button" className="btn btn-primary rm-btn" onClick={iv.retryLoad}>
                      Try again
                    </button>
                  )}
                  <button type="button" className={`btn rm-btn${load.gone ? ' btn-primary' : ''}`} onClick={onLeave}>
                    Back to Practice
                  </button>
                </div>
              </section>
            ) : (
              <p className="rm-muted" role="status" aria-live="polite">
                Opening your interview…
              </p>
            )}
          </div>
        </main>
      </div>
    );
  }

  if (work.active) return <WorkWorkspace model={model} actions={actions} work={work} />;
  return <RoomView model={model} actions={actions} narrow={narrow} />;
}
