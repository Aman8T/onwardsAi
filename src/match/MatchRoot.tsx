/**
 * The match section: the header and whichever screen the address names, wired to the store.
 *
 *   /match → paste · /match/:id → the match · /match/:id/resume → the resume · /match/:id/cover → the cover note
 *
 * Every screen below is presentational; this file is where the store, the account and the router meet. Loading is
 * idempotent (`load`, `ensureResume`, `ensureCover` do nothing a second time), so React's double effect in
 * development never repeats a request. When the screen changes the page scrolls to the top and the new heading
 * takes focus, so a keyboard or screen-reader user starts reading where a sighted one does.
 *
 * Auto-apply's entry on a finished match (`useApplyEntry`) is worked out here, from the auto-apply status, the
 * match's own gates, the person's applications (by match, or by the Discover listing it came from) and the read-only
 * start check, and handed to the match view and the origin bar. Continue with the tailored resume opens the resume
 * screen, which then offers "Use this resume and apply"; with the saved one it goes straight to `/apply/new`.
 */
import { motion, useReducedMotion } from 'motion/react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { blockedReasonOf, roleApplyFor } from '../apply/ApplyForMe';
import type { ApplyChoice, ApplyEntry } from '../apply/ApplyForMe';
import { applyPath } from '../apply/route';
import {
  applyIntentFor,
  applyOn,
  consumeApplyChoice,
  rememberApplyIntent,
  reopens,
  requestApplyChoice,
  useApplications,
  useApplyCheck,
  useApplyChoiceAsks,
  useSavedResume,
} from '../apply/useApplications';
import { useMe } from '../auth/useMe';
import { OriginBar } from '../discover/OriginBar';
import { clearMatchOrigin, useOrigin } from '../discover/origin';
import type { MatchOrigin } from '../discover/origin';
import { EASE } from '../lib/ui';
import { actions, useApp } from '../store';
import { CoverView } from './CoverView';
import { FactSheet } from './FactSheet';
import { MatchHeader } from './MatchHeader';
import { MatchView } from './MatchView';
import { factIndex, isSampleId, pickProfileId } from './model';
import { PasteView } from './PasteView';
import { ResumeView } from './ResumeView';
import { coverFileUrl, resumeFileUrl } from './api';
import { matchActions, useMatch } from './useMatch';
import { matchPath } from './route';
import type { MatchRoute } from './route';
import '../styles/match.css';

const navigate = (route: MatchRoute) => actions.openMatch(route);

/* ───────────────────────── auto-apply's entry ───────────────────────── */

/** Fields Discover's origin may carry for auto-apply (the run the listing came from, and an apply intent). */
type OriginForApply = MatchOrigin & { runId?: string; intent?: string };

/**
 * Apply for me on the finished match `id`, or undefined (auto-apply off, still streaming, another match on screen).
 * Every input is read once and shared: the status, the application list and the check are cached per account.
 */
function useApplyEntry(id: string | undefined): ApplyEntry | undefined {
  const m = useMatch();
  const { user, loaded } = useMe();
  const origin = useOrigin() as OriginForApply | null;
  const apps = useApplications();
  const asks = useApplyChoiceAsks(id);

  const here = !!id && m.matchId === id && !!m.result;
  const from = origin && id && origin.matchId === id ? origin : null;
  const listingId = from?.listingId;
  const runId = from?.runId;
  const signedIn = loaded && !!user;
  const application = id ? (apps.byMatch.get(id) ?? (listingId ? apps.byListing.get(listingId) : undefined)) : undefined;
  const blockedReason = here && m.blocked && m.posting ? blockedReasonOf(m.posting.gates, m.result?.gates ?? m.gates ?? []) : undefined;

  const wantCheck = here && applyOn(apps.status) && signedIn && !blockedReason && !(application && !reopens(application));
  const check = useApplyCheck(wantCheck && id ? { matchId: id, ...(listingId ? { listingId } : {}), ...(runId ? { runId } : {}) } : null);
  const state = here ? roleApplyFor({ status: apps.status, signedIn, blockedReason, application, check: wantCheck ? check : undefined, knownLink: !!from?.applyUrl }) : null;
  const savedResume = useSavedResume(state?.apply.kind === 'offer');
  const onAsked = useCallback(() => {
    if (id) consumeApplyChoice(id);
  }, [id]);

  if (!state || !id) return undefined;
  const onContinue = (choice: ApplyChoice) => {
    rememberApplyIntent({ matchId: id, resume: choice.resume, ...(choice.applyUrl ? { applyUrl: choice.applyUrl } : {}), ...(listingId ? { listingId } : {}), ...(runId ? { runId } : {}) });
    if (choice.resume === 'tailored') navigate({ screen: 'resume', id });
    else window.location.assign(applyPath({ screen: 'new', matchId: id, resume: 'saved', ...(listingId ? { listingId } : {}), ...(runId ? { runId } : {}) }));
  };
  return {
    ...state,
    firstTime: apps.status?.firstCheckDone !== true,
    savedResume,
    open: from?.intent === 'apply',
    asks,
    onAsked,
    signInNext: matchPath({ screen: 'match', id }),
    onContinue,
  };
}

export function MatchRoot() {
  const route = useApp((s) => s.matchRoute);
  const reading = useMatch().phase === 'reading';
  const reduced = useReducedMotion() ?? false;
  const seen = useRef<string | null>(null);

  // The live run shows on the paste address while it streams; it is the match screen, and stays that screen when
  // the run finishes and the address moves to /match/:id, so nothing remounts (the radar does not redraw).
  const kind: MatchRoute['screen'] = route.screen === 'paste' && reading ? 'match' : route.screen;

  // a new screen: top of the page, and the heading takes focus (not on first paint, which would pull focus off the address bar)
  const key = kind === 'match' ? 'match' : `${kind}:${route.id ?? ''}`;
  useEffect(() => {
    const first = seen.current === null;
    seen.current = key;
    if (first) return;
    window.scrollTo({ top: 0 });
    const frame = requestAnimationFrame(() => document.querySelector<HTMLElement>('[data-mt-h1]')?.focus({ preventScroll: true }));
    return () => cancelAnimationFrame(frame);
  }, [key]);

  // A heading that appears while focus has nowhere to be (a loading panel was replaced by the screen it was
  // waiting for, a button that was clicked left the page): focus lands on it instead of on <body>.
  const lastHeading = useRef<Element | null>(null);
  useEffect(() => {
    const heading = document.querySelector<HTMLElement>('[data-mt-h1]');
    if (!heading || heading === lastHeading.current) return;
    const first = lastHeading.current === null; // the first paint never takes focus
    lastHeading.current = heading;
    if (!first && (document.activeElement === document.body || document.activeElement === null)) heading.focus({ preventScroll: true });
  });

  const shownId = kind === 'paste' ? undefined : route.id;
  const entry = useApplyEntry(shownId);
  const originApply = () => {
    if (!shownId) return;
    if (route.screen !== 'match') navigate({ screen: 'match', id: shownId });
    requestApplyChoice(shownId);
  };

  return (
    <div className="mt-view">
      <MatchHeader onNavigate={navigate} />
      <OriginBar apply={entry?.apply} onApply={originApply} signInNext={entry?.signInNext} />
      <motion.main
        key={kind}
        className="mt-main"
        initial={reduced ? false : { opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.35, ease: EASE }}
      >
        <Screen route={route} apply={entry} />
      </motion.main>
    </div>
  );
}

function Screen({ route, apply }: { route: MatchRoute; apply?: ApplyEntry }) {
  const m = useMatch();
  if (route.screen === 'paste') return m.phase === 'reading' ? <MatchScreen /> : <PasteScreen />;
  if (!route.id) return <PasteScreen />;
  if (route.screen === 'match') return <MatchScreen id={route.id} apply={apply} />;
  if (route.screen === 'resume') return <ResumeScreen id={route.id} />;
  return <CoverScreen id={route.id} />;
}

/* ───────────────────────── states ───────────────────────── */

function StatePanel({ title, children, action }: { title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <section className="panel mt-panel mt-state" role="status">
      <h1 className="mt-state-title" data-mt-h1 tabIndex={-1}>
        {title}
      </h1>
      {children && <p className="mt-lede">{children}</p>}
      {action && <div className="mt-actions">{action}</div>}
    </section>
  );
}

const toPaste = (
  <button type="button" className="btn btn-primary mt-btn" onClick={() => navigate({ screen: 'paste' })}>
    Match a posting
  </button>
);

/* ───────────────────────── paste ───────────────────────── */

function PasteScreen() {
  const m = useMatch();
  const { user, loaded, profiles, entitlements, matchAccess, unlimited } = useMe();
  const profile = useApp((s) => s.profile);
  const shownId = profile?.id;
  const profileId = pickProfileId(shownId, profiles) ?? (shownId && !isSampleId(shownId) ? shownId : undefined);
  const userId = user?.id;

  // the side list: the account's matches, or what this device remembers when nobody is signed in
  useEffect(() => {
    if (loaded) void matchActions.loadRecent(userId);
  }, [loaded, userId]);

  const roles = useMemo(
    () => (profile ? [...profile.roles].sort((a, b) => b.alignment - a.alignment).slice(0, 3).map((r) => ({ id: r.id, title: r.title })) : []),
    [profile],
  );

  let blocker: string | undefined;
  if (shownId && isSampleId(shownId) && !profileId) blocker = 'This is the sample profile, so there is nothing of yours to match yet. Analyse your own resume first.';
  else if (!profileId) blocker = 'A match compares a posting with your evidence, so your resume needs to be analysed first.';

  return (
    <PasteView
      draft={m.draft}
      onDraft={matchActions.setDraft}
      onSubmit={() => {
        if (!profileId) return;
        clearMatchOrigin(); // a posting pasted here is not the Discover listing a bar may remember
        void matchActions.submit({ profileId, text: m.draft.text, url: m.draft.url }, { signedIn: loaded ? !!user : null });
      }}
      busy={m.phase === 'reading'}
      notice={m.notice}
      paywall={m.paywall && !unlimited}
      onDismissPaywall={matchActions.clearNotice}
      blocker={blocker}
      blockerAction={!profileId ? { label: 'Analyse my resume', run: () => actions.goto('onboarding') } : undefined}
      showFree={!unlimited && (!loaded || !user || entitlements.freeMatchAvailable)}
      noAccount={matchAccess === 'anonymous'}
      recent={m.recent ?? []}
      roles={roles}
      onPickRole={() => {
        actions.setFlipped(true);
        actions.showDashboard();
      }}
      onNavigate={navigate}
    />
  );
}

/* ───────────────────────── match ───────────────────────── */

/** Without an `id` this is the live run on `/match` (the stream is being read). */
function MatchScreen({ id, apply }: { id?: string; apply?: ApplyEntry }) {
  const m = useMatch();
  useEffect(() => {
    if (id) void matchActions.load(id);
  }, [id]);

  const mine = id ? m.matchId === id : m.phase === 'reading';
  if (id && !mine) {
    if (m.loadError?.id === id) {
      return (
        <StatePanel title="That match didn't open" action={toPaste}>
          {m.loadError.message}
        </StatePanel>
      );
    }
    return <StatePanel title="Opening your match…" />;
  }

  const matchId = id ?? m.matchId;
  return (
    <MatchView
      matchId={matchId}
      posting={m.posting}
      facts={m.facts}
      coverage={m.coverage}
      gates={m.gates}
      blocked={m.blocked}
      radar={m.radar}
      questions={m.questions}
      result={m.result}
      reading={m.phase === 'reading'}
      step={m.step}
      createdAt={m.createdAt}
      recheck={m.recheck}
      reveal={m.live}
      onAnswer={(answers) => matchId && void matchActions.answer(matchId, answers)}
      onBuildResume={() => matchId && navigate({ screen: 'resume', id: matchId })}
      apply={id ? apply : undefined}
    />
  );
}

/* ───────────────────────── resume ───────────────────────── */

function ResumeScreen({ id }: { id: string }) {
  const m = useMatch();
  const [factId, setFactId] = useState<string | null>(null);
  useEffect(() => {
    void matchActions.load(id);
    void matchActions.ensureResume(id);
  }, [id]);

  const r = m.resume;
  const byId = useMemo(() => factIndex(m.facts), [m.facts]);
  const here = m.matchId === id;
  // "Fit my resume to this role, then apply" was chosen on this match: the resume screen leads on to the start
  const intent = applyIntentFor(id);
  const applyHref =
    intent?.resume === 'tailored'
      ? applyPath({ screen: 'new', matchId: id, resume: 'tailored', ...(intent.listingId ? { listingId: intent.listingId } : {}), ...(intent.runId ? { runId: intent.runId } : {}) })
      : undefined;

  if (here && r.matchId === id && r.status === 'ready' && r.variant) {
    return (
      <>
        <ResumeView
          posting={m.posting}
          facts={m.facts}
          variant={r.variant}
          matchId={id}
          saving={r.saving}
          notice={r.error}
          downloading={r.downloading}
          onDecide={(choice) => void matchActions.decide(id, choice)}
          onNavigate={navigate}
          onDownload={(kind) => void matchActions.downloadResume(id, kind)}
          onFact={(fid) => setFactId(fid)}
          hrefFor={(kind) => resumeFileUrl(id, kind)}
          applyHref={applyHref}
        />
        <FactSheet id={factId} fact={factId ? byId.get(factId) : undefined} onClose={() => setFactId(null)} />
      </>
    );
  }

  if (m.loadError?.id === id) {
    return (
      <StatePanel title="That match didn't open" action={toPaste}>
        {m.loadError.message}
      </StatePanel>
    );
  }
  if (r.matchId === id && r.status === 'error') {
    return (
      <StatePanel
        title="The resume didn't build"
        action={
          <>
            <button type="button" className="btn btn-primary mt-btn" onClick={() => void matchActions.ensureResume(id, true)}>
              Try again
            </button>
            <button type="button" className="btn btn-quiet mt-btn" onClick={() => navigate({ screen: 'match', id })}>
              Back to the match
            </button>
          </>
        }
      >
        {r.error}
      </StatePanel>
    );
  }
  return (
    <StatePanel title={r.status === 'generating' ? 'Building your resume from your facts…' : 'Opening your resume…'}>
      {r.status === 'generating' ? 'Choosing the facts that fit this posting, then phrasing each line from them and checking it. This takes about 20 seconds.' : undefined}
    </StatePanel>
  );
}

/* ───────────────────────── cover ───────────────────────── */

function CoverScreen({ id }: { id: string }) {
  const m = useMatch();
  useEffect(() => {
    void matchActions.load(id);
    void matchActions.ensureCover(id);
  }, [id]);

  const c = m.cover;
  if (m.matchId === id && c.matchId === id && c.status === 'ready' && c.note) {
    return (
      <CoverView
        posting={m.posting}
        facts={m.facts}
        coverage={m.result?.coverage ?? m.coverage}
        note={c.note}
        matchId={id}
        onNavigate={navigate}
        hrefPdf={coverFileUrl(id)}
        onDownload={() => void matchActions.downloadCover(id)}
        downloading={c.downloading}
        notice={c.error}
      />
    );
  }
  if (m.loadError?.id === id) {
    return (
      <StatePanel title="That match didn't open" action={toPaste}>
        {m.loadError.message}
      </StatePanel>
    );
  }
  if (c.matchId === id && c.status === 'error') {
    return (
      <StatePanel
        title="The cover note didn't write"
        action={
          <>
            <button type="button" className="btn btn-primary mt-btn" onClick={() => void matchActions.ensureCover(id, true)}>
              Try again
            </button>
            <button type="button" className="btn btn-quiet mt-btn" onClick={() => navigate({ screen: 'match', id })}>
              Back to the match
            </button>
          </>
        }
      >
        {c.error}
      </StatePanel>
    );
  }
  return (
    <StatePanel title={c.status === 'generating' ? 'Writing your cover note…' : 'Opening your cover note…'}>
      {c.status === 'generating' ? 'Each paragraph pairs one thing the posting asks for with one fact of yours. A paragraph with no fact is not written.' : undefined}
    </StatePanel>
  );
}
