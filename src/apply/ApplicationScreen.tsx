/**
 * `/apply/<id>`: one application, shown by its stage (spec 3) and, where it stopped, by why (`stopReason`).
 *
 *   queued, filling                     FillingView      the form, section by section; nothing has been sent
 *   waiting_for_you                     QuestionsView    while the form asks things we do not know yet
 *                                       ReviewView       every value, then Approve and send
 *   waiting_for_you + form_changed      OutcomeView      the form changed after the review, then the new review
 *   everything else                     OutcomeView      sending, needs you on the site, check this one, failed,
 *                                                        closed, skipped, sent and later
 *
 * Someone who watched the form fill stays on Filling when it finishes, with "Ready: review it" switched on, rather than
 * having the screen change under them. The screen reads the application and polls it every 3 s while the worker is
 * still moving it (`busy`: queued, filling, approved, submitting).
 *
 * `ApplicationScreen` is the container (the API); `ApplicationRun` is the same screen on any detail and any actions,
 * which the dev gallery drives from fixtures.
 */
import { useCallback, useEffect, useState } from 'react';
import type {
  AnswerRequest,
  AnswerResult,
  ApplicationDetail,
  ApplicationStage,
  ApplyStatus,
  EmailClassification,
  EmailClassifyRequest,
  ResolveOutcome,
} from '../../shared/apply';
import { plural } from '../lib/ui';
import { getMatch } from '../match/api';
import { bandNote } from '../match/model';
import { ApplyError, applyApi, busy, errorText, screenshotUrl } from './api';
import { FillingView } from './FillingView';
import { isQuestion } from './NeedCard';
import { OutcomeView, outcomeKind } from './OutcomeView';
import { QuestionsView } from './QuestionsView';
import { ReviewView } from './ReviewView';
import type { ReviewMatch } from './ReviewView';
import type { Navigate } from './route';
import '../styles/apply-run.css';

export interface ApplicationScreenProps {
  id: string;
  navigate: Navigate;
  status: ApplyStatus;
}

const POLL_MS = 3000;

/* ───────────────────────── what code computes ───────────────────────── */

export type RunScreen = 'filling' | 'questions' | 'review' | 'outcome';

/** The stages that wait for the person's answers or approval (the legacy names until Z). */
const WAITING: ReadonlySet<ApplicationStage> = new Set(['waiting_for_you', 'needs_user', 'ready_to_submit']);
/** The stages a person watches the form fill in. */
const FILLING: ReadonlySet<ApplicationStage> = new Set(['queued', 'filling']);
/** The stages after the person approved, before the outcome. */
const SENDING: ReadonlySet<ApplicationStage> = new Set(['approved', 'submitting']);

export interface RunState {
  /** The person saw this form fill, so the finished fill waits on Filling for "Ready: review it". */
  watched: boolean;
  /** The person went on from Filling or from "the form changed". */
  opened: boolean;
}

/** Which screen an application shows. */
export function runScreen(detail: Pick<ApplicationDetail, 'stage' | 'stopReason' | 'needs' | 'review'>, state: RunState): RunScreen {
  if (FILLING.has(detail.stage)) return 'filling';
  if (WAITING.has(detail.stage)) {
    if (!state.opened && detail.stopReason === 'form_changed') return 'outcome';
    if (!state.opened && state.watched) return 'filling';
    // Questions only while a required question is left; optional ones are answered (or not) in Review
    if (detail.needs.some((n) => n.required && isQuestion(n)) || !detail.review) return 'questions';
    return 'review';
  }
  return 'outcome';
}

/**
 * The screen's own state as the stage moves: a fill that starts again (answers went in) is watched again from Filling;
 * once approved, the person has not seen what comes next, so a later "the form changed" (the submit pass's read-back)
 * is shown first, never skipped.
 */
export function runStateOn(state: RunState, stage: ApplicationStage): RunState {
  if (FILLING.has(stage)) return state.watched && !state.opened ? state : { watched: true, opened: false };
  if (SENDING.has(stage)) return !state.watched && !state.opened ? state : { watched: false, opened: false };
  return state;
}

/** What the screen says after answers went in: the server's count of other forms that carried on. */
export function requeuedText(requeued: number): string {
  return requeued > 0
    ? `Saved. ${plural(requeued, 'more form')} waiting carried on with your answer too.`
    : 'Saved. We carry on filling the form with your answers.';
}

/* ───────────────────────── the screen on any detail ───────────────────────── */

export interface RunActions {
  answer: (body: AnswerRequest) => Promise<AnswerResult>;
  approve: (digest: string) => Promise<ApplicationDetail>;
  cancel: () => Promise<ApplicationDetail>;
  resolve: (outcome: ResolveOutcome) => Promise<ApplicationDetail>;
  /** "I applied there", from needs_signin. */
  appliedThere: () => Promise<ApplicationDetail>;
  /** Queued again after a failure before any click. */
  fillAgain: () => Promise<ApplicationDetail>;
  stage: (stage: ApplicationStage) => Promise<ApplicationDetail>;
  email?: (body: EmailClassifyRequest) => Promise<EmailClassification>;
}

export interface ApplicationRunProps {
  detail: ApplicationDetail;
  onDetail: (next: ApplicationDetail) => void;
  navigate: Navigate;
  actions: RunActions;
  /** The ids of the applications waiting for the person, for Review's Prev and Next. */
  queue?: readonly string[];
  match?: ReviewMatch;
  /** The screenshots' addresses; absent in the gallery. */
  shots?: { review?: string; confirmation?: string };
  /** The person saw this form fill (default: the first detail is still filling). The gallery sets it for a finished fill. */
  initialWatched?: boolean;
}

export function ApplicationRun({ detail, onDetail, navigate, actions, queue, match, shots, initialWatched }: ApplicationRunProps) {
  const [run, setRun] = useState<RunState>(() => ({ watched: initialWatched ?? FILLING.has(detail.stage), opened: false }));
  const { watched, opened } = run;
  const [notice, setNotice] = useState<string | null>(null);

  const stage = detail.stage;
  useEffect(() => setRun((s) => runStateOn(s, stage)), [stage]);

  const take = (next: ApplicationDetail) => {
    onDetail(next);
    window.scrollTo?.({ top: 0 });
  };
  const screen = runScreen(detail, { watched, opened });
  const open = () => {
    setRun((s) => ({ ...s, opened: true }));
    setNotice(null);
  };

  let body;
  switch (screen) {
    case 'filling':
      body = <FillingView detail={detail} navigate={navigate} onReady={open} />;
      break;
    case 'questions':
      body = (
        <QuestionsView
          key={`${detail.id}-${detail.updatedAt}`}
          detail={detail}
          navigate={navigate}
          onAnswer={async (b) => {
            const got = await actions.answer(b);
            setNotice(requeuedText(got.requeued));
            take(got.application);
          }}
          onSkip={async () => take(await actions.cancel())}
        />
      );
      break;
    case 'review':
      body = (
        <ReviewView
          key={`${detail.id}-${detail.review?.digest ?? ''}`}
          detail={detail}
          navigate={navigate}
          queue={queue}
          match={match}
          shot={shots?.review}
          onApprove={async (digest) => {
            const next = await actions.approve(digest);
            setRun((s) => runStateOn(s, next.stage));
            take(next);
          }}
          onAnswer={async (b) => {
            const got = await actions.answer(b);
            setNotice(requeuedText(got.requeued));
            take(got.application);
          }}
          onCancel={async () => take(await actions.cancel())}
          onEmail={actions.email}
        />
      );
      break;
    case 'outcome':
      body = outcomeKind(detail) ? (
        <OutcomeView
          detail={detail}
          navigate={navigate}
          confirmation={shots?.confirmation}
          onResolve={async (o) => take(await actions.resolve(o))}
          onAppliedThere={async () => take(await actions.appliedThere())}
          onCancel={async () => take(await actions.cancel())}
          onFillAgain={async () => take(await actions.fillAgain())}
          onStage={async (s) => take(await actions.stage(s))}
          onReview={open}
          onEmail={actions.email}
        />
      ) : null;
      break;
  }

  return (
    <div className="ap-stack">
      {notice && (
        <p className="ar-notice" role="status">
          {notice}
        </p>
      )}
      {body}
    </div>
  );
}

/* ───────────────────────── the container ───────────────────────── */

export function ApplicationScreen({ id, navigate }: ApplicationScreenProps) {
  const [detail, setDetail] = useState<ApplicationDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [queue, setQueue] = useState<readonly string[]>([]);
  const [match, setMatch] = useState<ReviewMatch | undefined>(undefined);

  const load = useCallback(async () => {
    try {
      setDetail(await applyApi.get(id));
      setError(null);
    } catch (err) {
      setError(errorText(err, 'Could not load this application. Try again.'));
    }
  }, [id]);

  useEffect(() => {
    setDetail(null);
    void load();
  }, [load]);

  const moving = detail ? busy(detail.stage) : false;
  useEffect(() => {
    if (!moving) return;
    const timer = window.setInterval(() => void load(), POLL_MS);
    return () => window.clearInterval(timer);
  }, [moving, load]);

  // Review's Prev and Next: the applications waiting for the person, in the list's order
  const waiting = detail ? WAITING.has(detail.stage) : false;
  useEffect(() => {
    if (!waiting) return;
    let live = true;
    applyApi
      .list()
      .then((l) => live && setQueue(l.applications.filter((a) => WAITING.has(a.stage)).map((a) => a.id)))
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, [waiting]);

  const matchId = detail?.matchId;
  useEffect(() => {
    if (!matchId || !waiting) return;
    let live = true;
    getMatch(matchId)
      .then((m) => live && setMatch({ id: m.id, band: m.result.band, note: bandNote(m.result, m.posting) }))
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, [matchId, waiting]);

  if (!detail) {
    return error ? (
      <div className="ap-stack-s">
        <p className="ap-error" role="alert">
          {error}
        </p>
        <div className="ap-actions">
          <button type="button" className="btn" onClick={() => void load()}>
            Try again
          </button>
        </div>
      </div>
    ) : (
      <p className="ap-muted" role="status">
        Loading the application
      </p>
    );
  }

  const actions: RunActions = {
    answer: (b) => applyApi.answer(id, b),
    approve: async (digest) => {
      try {
        return await applyApi.approve(id, digest);
      } catch (err) {
        // 409: the review changed under the person; show them the current one
        if (err instanceof ApplyError && err.status === 409) void load();
        throw err;
      }
    },
    cancel: () => applyApi.cancel(id),
    resolve: (o) => applyApi.resolve(id, o),
    appliedThere: () => applyApi.appliedThere(id),
    fillAgain: () => applyApi.retry(id),
    stage: (s) => applyApi.stage(id, s),
    email: (b) => applyApi.email({ ...b, applicationId: b.applicationId ?? id }),
  };

  const version = detail.review?.capturedAt ?? detail.updatedAt;
  return (
    <ApplicationRun
      detail={detail}
      onDetail={setDetail}
      navigate={navigate}
      actions={actions}
      queue={queue}
      match={match}
      shots={{
        review: detail.hasScreenshot ? screenshotUrl(id, 'review', version) : undefined,
        confirmation: detail.hasConfirmation ? screenshotUrl(id, 'confirmation', detail.submittedAt ?? detail.updatedAt) : undefined,
      }}
    />
  );
}
