/**
 * The debrief of one interview, wired: `GET /api/interview/sessions/{id}` gives the session and, once it is done, its
 * debrief (stored with it, so opening it again costs nothing). An interview that is not finished offers to carry on, or to
 * end and see what it showed. Asking a question again starts a new interview of one question and goes to its room.
 * The screen itself is `DebriefView`.
 *
 * Fix steps (I2): the person's Prepare plans (`GET /api/prepare?profileId=`) say where a step can go, and the re-drill view
 * says which of this session's steps are already placed. Either read failing leaves the steps as steps to read, with
 * "Keep it in Practice" still offered.
 */
import { useEffect, useState } from 'react';
import type { Profile } from '../../../shared/types';
import type { Debrief, FixTask, SessionView } from '../../../shared/interview';
import type { PreparePlanSummary } from '../../../shared/prepare';
import { NARROW_QUERY, useMediaQuery } from '../../lib/ui';
import { addFix, askAgain, deleteSession, endSession, fetchHistory, fetchPlanList, fetchRedrill, fetchSession, removeFix, setFact } from '../api';
import type { FixPlacing } from './ResultCard';
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
  // the person's practice so far draws "claims defended" over sessions; a read that fails just leaves that tile out
  const past = useLoad((signal) => fetchHistory(profile.id, signal), [profile.id]).load;
  const placing = useFixPlacing(profile.id, sessionId, load.status === 'ready' ? load.data.debrief : undefined);

  if (load.status === 'loading') {
    return (
      <div className="pc-page" aria-busy="true">
        <p className="pc-muted pc-gutter" role="status">
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
      {...(placing ? { fix: placing } : {})}
      {...(past.status === 'ready' ? { history: past.data } : {})}
    />
  );
}

/** Where this debrief's Fix steps can go, and which are placed: read once the debrief is there. */
function useFixPlacing(profileId: string, sessionId: string, debrief: Debrief | undefined): FixPlacing | undefined {
  const [plans, setPlans] = useState<readonly PreparePlanSummary[]>([]);
  const [placed, setPlaced] = useState<Readonly<Record<string, FixTask>>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<{ slotId: string; message: string } | null>(null);
  const hasFix = !!debrief?.results.some((r) => r.fix);

  useEffect(() => {
    if (!hasFix) return;
    const ctl = new AbortController();
    fetchPlanList(profileId, ctl.signal).then(
      (list) => {
        if (!ctl.signal.aborted) setPlans(list);
      },
      () => undefined,
    );
    fetchRedrill(profileId, ctl.signal).then(
      (view) => {
        if (ctl.signal.aborted) return;
        const mine: Record<string, FixTask> = {};
        for (const t of [...view.claims.flatMap((c) => (c.fix ? [c.fix] : [])), ...view.kept]) if (t.sessionId === sessionId) mine[t.slotId] = t;
        setPlaced(mine);
      },
      () => undefined,
    );
    return () => ctl.abort();
  }, [profileId, sessionId, hasFix]);

  if (!debrief || !hasFix) return undefined;
  const roleId = debrief.graphRole?.roleId;
  const defaultPlanId = (roleId ? plans.find((p) => p.roleId === roleId && !p.requirement) : undefined)?.id ?? plans[0]?.id;

  const run = async (slotId: string, act: () => Promise<void>) => {
    setBusy(slotId);
    setError(null);
    try {
      await act();
    } catch (err) {
      setError({ slotId, message: sentenceOf(err) });
    } finally {
      setBusy(null);
    }
  };

  return {
    plans,
    ...(defaultPlanId ? { defaultPlanId } : {}),
    placed,
    busy,
    error,
    onPlace: (slotId, planId) =>
      void run(slotId, async () => {
        const task = await addFix({ profileId, sessionId, slotId, ...(planId ? { planId } : {}) });
        setPlaced((prev) => ({ ...prev, [slotId]: task }));
      }),
    onUndo: (slotId) =>
      void run(slotId, async () => {
        const task = placed[slotId];
        if (task) await removeFix(task.id, profileId);
        setPlaced((prev) => {
          const next = { ...prev };
          delete next[slotId];
          return next;
        });
      }),
    onOpenPlan: (plan) => liveNav.plan(plan.roleId, plan.requirement),
    onRedrill: liveNav.redrill,
  };
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
      <header className="pc-head pc-gutter">
        <button type="button" className="pc-back" onClick={liveNav.hub}>
          <span aria-hidden="true">←</span> Practice
        </button>
        <p className="kicker">Debrief</p>
        <h1 className="serif pc-title">{done ? 'Your debrief is ready to open' : "This interview isn't finished"}</h1>
      </header>
      <section className="m-tile m-quiet pc-notice">
        <div className="pc-notice-text">
          <p>{done ? 'Open it to see each answer with the words its verdict rests on.' : 'Carry on where you left off, or end it here. Questions you have not answered are Not assessed, which never counts against you.'}</p>
        </div>
        {!done && (
          <div className="pc-actions">
            <button type="button" className="btn pc-btn" disabled={busy} onClick={() => liveNav.room(sessionId)}>
              Carry on
            </button>
          </div>
        )}
        {error && (
          <p className="pc-danger" role="alert">
            {error}
          </p>
        )}
      </section>
      <button type="button" className="m-tile m-ink pc-ink" disabled={busy} onClick={() => void end()}>
        {busy ? 'Opening…' : done ? 'Open the debrief' : 'End and see the debrief'} <span aria-hidden="true">→</span>
      </button>
    </div>
  );
}
