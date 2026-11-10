/**
 * The resume section: the header and whichever screen the address names, wired to the store and the account.
 *
 *   /resume → the resumes, the default in view · ?r=<id> → that resume · &screen=finalise → Finalise · ?screen=blocks → your blocks
 *
 * Every screen below is presentational; this file is where the store, the account, the data hook and the address
 * meet. Who may ask follows Match: a signed-in person for a profile of their account, or anyone for an unclaimed
 * profile while the server matches without an account. The sample profile belongs to nobody, so it has no resumes.
 *
 * When the screen changes the page scrolls to the top and the new heading takes focus, so a keyboard or
 * screen-reader user starts reading where a sighted one does.
 */
import { motion, useReducedMotion } from 'motion/react';
import { useCallback, useEffect, useRef } from 'react';
import type { Profile } from '../../shared/types';
import { AccountMenu } from '../auth/AccountMenu';
import { signInSheet, useMe } from '../auth/useMe';
import { Atmosphere } from '../components/Atmosphere';
import { EASE } from '../lib/ui';
import { isSampleId } from '../match/model';
import { actions, getState, useApp } from '../store';
import { resumeFileUrl } from './api';
import { BlocksView } from './BlocksView';
import { FinaliseView } from './FinaliseView';
import { StatePanel } from './parts';
import { ResumeHeader } from './ResumeHeader';
import { ResumePage } from './ResumePage';
import { resumePath } from './route';
import type { ResumeQuery } from './route';
import type { FileKind } from './types';
import { useResumes } from './useResumes';
import '../styles/resume.css';

const navigate = (to: ResumeQuery) => actions.openResume(to);

export function ResumeRoot({ profile }: { profile: Profile }) {
  const route = useApp((s) => s.resumeRoute);
  const { user, loaded, profiles, matchAccess } = useMe();
  const reduced = useReducedMotion() ?? false;

  const sample = isSampleId(profile.id);
  const signedOut = loaded && !user;
  // an account is needed and there is a way to get one: ask before the server has to refuse
  const mustSignIn = signedOut && matchAccess === 'sign-in';
  const stuck = signedOut && matchAccess === 'stuck';
  const r = useResumes(sample || mustSignIn || stuck ? undefined : profile.id, route.id);
  const { reload, ensureBlocks, runFinalise, listStatus } = r;

  // a profile that was just attached to the account: a list the server refused a moment ago is asked for again
  const owned = profiles.some((p) => p.id === profile.id);
  const wasOwned = useRef(owned);
  useEffect(() => {
    if (owned && !wasOwned.current && listStatus === 'error') reload();
    wasOwned.current = owned;
  }, [owned, listStatus, reload]);

  useEffect(() => {
    if (route.screen === 'blocks' && listStatus === 'ready') ensureBlocks();
  }, [route.screen, listStatus, ensureBlocks]);

  // a new screen: top of the page, and the heading takes focus (not on first paint, which would pull focus off the address bar)
  const key = `${route.screen}:${route.id ?? ''}`;
  const seen = useRef<string | null>(null);
  useEffect(() => {
    const first = seen.current === null;
    const sameScreen = seen.current?.split(':')[0] === route.screen;
    seen.current = key;
    if (first) return;
    if (!sameScreen) window.scrollTo({ top: 0 });
    const frame = requestAnimationFrame(() => document.querySelector<HTMLElement>('[data-rs-h1]')?.focus({ preventScroll: true }));
    return () => cancelAnimationFrame(frame);
  }, [key, route.screen]);

  // a heading that appears while focus has nowhere to be (a loading panel became the screen it was waiting for)
  const lastHeading = useRef<Element | null>(null);
  useEffect(() => {
    const heading = document.querySelector<HTMLElement>('[data-rs-h1]');
    if (!heading || heading === lastHeading.current) return;
    const first = lastHeading.current === null;
    lastHeading.current = heading;
    if (!first && (document.activeElement === document.body || document.activeElement === null)) heading.focus({ preventScroll: true });
  });

  const viewId = r.viewId;
  const hrefFor = useCallback((file: FileKind) => (viewId ? resumeFileUrl(viewId, file) : '#'), [viewId]);
  const openFinalise = (makeDefault: boolean) => {
    if (!viewId) return;
    void runFinalise(viewId, { makeDefault });
    navigate({ screen: 'finalise', id: viewId });
  };
  const ask = (question: string) => {
    const title = r.detail?.resume.title;
    actions.openChat({ kind: 'free', title: 'Your resume', caption: title ? `Asked from ${title}` : 'Asked from your resumes' }, question);
  };

  let body;
  if (sample) {
    body = (
      <StatePanel
        title="The sample has no resumes of its own"
        action={
          <button type="button" className="btn btn-primary rs-btn" onClick={() => actions.goto('onboarding')}>
            Analyse my resume
          </button>
        }
      >
        This is the sample profile, so there are no blocks of yours to arrange yet. Analyse your own resume and it shows here, line by line.
      </StatePanel>
    );
  } else if (stuck) {
    body = <StatePanel title="Your resumes need an account">This server has no way to sign in yet, so the resumes cannot be opened here.</StatePanel>;
  } else if (mustSignIn || r.listError?.status === 401) {
    body = (
      <StatePanel
        title="Sign in to open your resumes"
        action={
          <button type="button" className="btn btn-primary rs-btn" onClick={() => signInSheet.open('account', undefined, resumePath(route))}>
            Sign in
          </button>
        }
      >
        Your resumes are kept with your account, so only you can open, change and download them.
      </StatePanel>
    );
  } else if (r.listStatus === 'error') {
    body = (
      <StatePanel
        title="Your resumes didn't load"
        action={
          <button type="button" className="btn btn-primary rs-btn" onClick={reload}>
            Try again
          </button>
        }
      >
        {r.listError?.message}
      </StatePanel>
    );
  } else if (!r.list) {
    body = <StatePanel title="Opening your resumes…" busy />;
  } else if (route.screen === 'blocks') {
    body = (
      <BlocksView
        data={r.blocks}
        status={r.blocksStatus}
        error={r.blocksError}
        onRetry={() => ensureBlocks(true)}
        back={{ screen: 'resume', id: route.id }}
        onNavigate={navigate}
      />
    );
  } else if (route.screen === 'finalise' && route.id) {
    const id = route.id;
    const detail = r.detail?.resume.id === id ? r.detail : null;
    const fz = r.finalise.id === id ? r.finalise : undefined;
    body = detail ? (
      <FinaliseView
        detail={detail}
        status={fz?.status ?? 'idle'}
        report={fz?.report}
        error={fz?.error}
        wantsDefault={fz?.makeDefault ?? false}
        busy={r.busy}
        notice={r.notice}
        onRun={({ makeDefault, answers }) => void runFinalise(id, { makeDefault, ...(answers.length > 0 ? { answers } : {}) })}
        onMakeDefault={() => void r.makeDefault(id)}
        onDownload={(file) => void r.download(id, file)}
        hrefFor={hrefFor}
        onNavigate={navigate}
      />
    ) : r.detailStatus === 'error' ? (
      <StatePanel
        title="That resume didn't open"
        action={
          <button type="button" className="btn btn-primary rs-btn" onClick={r.reloadDetail}>
            Try again
          </button>
        }
      >
        {r.detailError?.message}
      </StatePanel>
    ) : (
      <StatePanel title="Opening your resume…" busy />
    );
  } else {
    body = (
      <ResumePage
        list={r.list}
        detail={r.detail}
        detailStatus={r.detailStatus}
        detailError={r.detailError}
        viewId={viewId}
        busy={r.busy}
        notice={r.notice}
        onNavigate={navigate}
        onMake={(roleId) => {
          void r.makePath(roleId).then((id) => {
            // the new resume opens, unless the person has gone to another part of the app while it was made
            if (id && getState().view === 'resume') navigate({ screen: 'resume', id });
          });
        }}
        onOp={r.op}
        onFinalise={openFinalise}
        onMakeDefault={() => viewId && void r.makeDefault(viewId)}
        onDownload={(file) => viewId && void r.download(viewId, file)}
        hrefFor={hrefFor}
        onAsk={ask}
        onRetry={r.reloadDetail}
      />
    );
  }

  return (
    <div className="rs-view">
      <ResumeHeader
        route={route}
        blocks={r.list?.blocks}
        onHome={() => actions.showDashboard()}
        onNavigate={navigate}
        end={
          <>
            <Atmosphere />
            <AccountMenu />
          </>
        }
      />
      <motion.main key={route.screen} className="rs-main" initial={reduced ? false : { opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.35, ease: EASE }}>
        {body}
      </motion.main>
    </div>
  );
}
