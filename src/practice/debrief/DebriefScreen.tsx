/**
 * The debrief of one interview, wired: `GET /api/interview/sessions/{id}` gives the session and, once it is done, its
 * debrief (stored with it, so opening it again costs nothing). An interview that is not finished offers to carry on, or to
 * end and see what it showed. Asking a question again starts a new interview of one question and goes to its room.
 * The screen itself is `DebriefView`.
 */
import { useState } from 'react';
import type { Profile } from '../../../shared/types';
import type { Debrief, SessionView } from '../../../shared/interview';
import { NARROW_QUERY, useMediaQuery } from '../../lib/ui';
import { askAgain, deleteSession, endSession, fetchSession, setFact } from '../api';
import { ErrorNote } from '../hub/Notice';
import { liveNav } from '../hub/nav';
import { useLoad } from '../hub/useLoad';
import { sentenceOf } from '../hub/words';
import { DebriefView } from './DebriefView';
import '../../styles/practice.css';

export function DebriefScreen({ profile, sessionId }: { profile: Profile; sessionId: string }) {
  const narrow = useMediaQuery(NARROW_QUERY);
  const { load, reload, patch } = useLoad<SessionView>((signal) => fetchSession(sessionId, profile.id, signal), [profile.id, sessionId]);
  const nav = liveNav;

  if (load.status === 'loading') {
    return (
      <div className="pc-page" aria-busy="true">
        <p className="pc-muted" role="status">
          Opening your debrief…
        </p>
      </div>
    );
  }
  if (load.status === 'error') {
    return (
      <div className="pc-page">
        <ErrorNote message={load.message} onRetry={load.code === 404 ? undefined : reload} onBack={nav.hub} />
      </div>
    );
  }

  const { session, debrief } = load.data;
  if (!debrief) {
    return <NotFinished profile={profile} sessionId={sessionId} done={session.status === 'done'} onDebrief={(d) => patch((v) => ({ ...v, debrief: d }))} />;
  }

  return (
    <DebriefView
      debrief={debrief}
      narrow={narrow}
      nav={nav}
      onAgain={async (slotId) => {
        const next = await askAgain(sessionId, { profileId: profile.id, slotId });
        nav.room(next.id);
      }}
      onFact={async (slotId, add) => {
        const updated = await setFact(sessionId, { profileId: profile.id, slotId, add });
        patch((v) => ({ ...v, debrief: updated }));
      }}
      onDelete={async () => {
        await deleteSession(sessionId, profile.id);
        nav.hub();
      }}
    />
  );
}

/** An interview with no debrief yet: carry on with it, or end it here and see what it showed. */
function NotFinished({ profile, sessionId, done, onDebrief }: { profile: Profile; sessionId: string; done: boolean; onDebrief: (d: Debrief) => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | undefined>();
  const end = async () => {
    setBusy(true);
    setError(undefined);
    try {
      onDebrief(await endSession(sessionId, profile.id));
    } catch (err) {
      setError(sentenceOf(err));
      setBusy(false);
    }
  };
  return (
    <div className="pc-page">
      <header className="pc-head">
        <button type="button" className="pc-back" onClick={liveNav.hub}>
          <span aria-hidden="true">←</span> Practice
        </button>
        <p className="kicker">Debrief</p>
        <h1 className="serif pc-title">{done ? 'Your debrief is ready to open' : "This interview isn't finished"}</h1>
      </header>
      <section className="panel pc-notice">
        <div className="pc-notice-text">
          <p>{done ? 'Open it to see each answer with the words its verdict rests on.' : 'Carry on where you left off, or end it here. Questions you have not answered are Not assessed, which never counts against you.'}</p>
        </div>
        <div className="pc-actions">
          <button type="button" className="btn btn-primary pc-btn" disabled={busy} onClick={() => void end()}>
            {busy ? 'Opening…' : done ? 'Open the debrief' : 'End and see the debrief'}
          </button>
          {!done && (
            <button type="button" className="btn pc-btn" disabled={busy} onClick={() => liveNav.room(sessionId)}>
              Carry on
            </button>
          )}
        </div>
        {error && (
          <p className="pc-danger" role="alert">
            {error}
          </p>
        )}
      </section>
    </div>
  );
}
