/**
 * The owner's portfolio screen. It reads the portfolio from the store and shows one of:
 *   - a calm waiting page while the first draft is being made (progress label, Back to the graph),
 *   - a calm error page with "Try again",
 *   - the editor, as soon as a draft exists (the polished copy swaps in when the stream says `done`).
 *
 * The editor's `preview` renders `PortfolioView`: the owner's own page in the editor, or the server's filtered
 * public preview when "Preview public view" is chosen (fetched then, and again after every saved change).
 * Edits go to the store, which saves them one at a time; a failed edit shows a dismissible notice.
 */
import { AnimatePresence, motion } from 'motion/react';
import { useCallback, useEffect, useState } from 'react';
import type { CSSProperties, ReactNode } from 'react';
import type { PublicPortfolio } from '../../shared/portfolio';
import type { Profile } from '../../shared/types';
import { RadarGlyph } from '../components/Radar';
import { getPublicPreview } from '../lib/api';
import { EASE } from '../lib/ui';
import { actions, getState, useApp } from '../store';
import { EditorView } from './editor/EditorView';
import type { EditorPreviewArgs } from './editor/EditorView';
import { CalmPage, PortfolioPage } from './PublicView';

const NOTICE_MS = 10_000;

const noticeStyle: CSSProperties = {
  position: 'fixed',
  insetInline: 'var(--s4)',
  bottom: 'var(--s5)',
  zIndex: 80,
  display: 'flex',
  alignItems: 'center',
  gap: 'var(--s3)',
  width: 'fit-content',
  maxWidth: 'min(560px, calc(100vw - 2 * var(--s4)))',
  marginInline: 'auto',
  padding: 'var(--s3) var(--s4)',
  fontSize: 'var(--fs-2)',
};

/** A failed edit or an interrupted polish: say it once, calmly, and let it go. */
function Notice({ message }: { message: string }) {
  useEffect(() => {
    const t = window.setTimeout(() => actions.clearPortfolioError(), NOTICE_MS);
    return () => window.clearTimeout(t);
  }, [message]);
  return (
    <motion.div
      className="panel"
      style={noticeStyle}
      role="alert"
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.25, ease: EASE }}
    >
      <span style={{ minWidth: 0, overflowWrap: 'anywhere' }}>{message}</span>
      <button type="button" className="btn btn-quiet btn-sm" onClick={() => actions.clearPortfolioError()}>
        Dismiss
      </button>
    </motion.div>
  );
}

/**
 * "Preview public view": the server's filtered copy of the page (what a viewer would get). The previous copy
 * stays on screen while a newer one is fetched, so saving an edit never blanks the preview.
 */
function PublicPreview({ args, profileId, stamp }: { args: EditorPreviewArgs; profileId: string; stamp: string }) {
  const [data, setData] = useState<PublicPortfolio | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const ctrl = new AbortController();
    setFailed(null);
    getPublicPreview(profileId, ctrl.signal)
      .then(setData)
      .catch((err: unknown) => {
        if (!ctrl.signal.aborted) setFailed(err instanceof Error ? err.message : String(err));
      });
    return () => ctrl.abort();
  }, [profileId, stamp, attempt]);

  if (data) {
    return (
      <PortfolioPage spec={data.spec} content={data.content} graph={data.graph} mode="public" skin={args.skin} onToggleSkin={args.onToggleSkin} />
    );
  }
  return (
    <div style={{ display: 'grid', justifyItems: 'center', gap: 'var(--s3)', padding: 'var(--s8) var(--s4)', textAlign: 'center' }}>
      <p className="kicker" role="status" aria-live="polite" style={{ margin: 0 }}>
        {failed ? 'The public preview did not load.' : 'Preparing the public view…'}
      </p>
      {failed && (
        <>
          <p style={{ margin: 0, color: 'var(--text-muted)' }}>{failed}</p>
          <button type="button" className="btn" onClick={() => setAttempt((n) => n + 1)}>
            Try again
          </button>
        </>
      )}
    </div>
  );
}

export function PortfolioScreen({ profile }: { profile: Profile }) {
  const { doc, status, step, error, busy } = useApp((s) => s.portfolio);

  // A reload on this screen (or any route straight to it) starts with nothing loaded: load it now. Leaving the
  // screen also resets the status to idle while this component is still fading out, so check the view too.
  useEffect(() => {
    if (status === 'idle' && getState().view === 'portfolio') void actions.openPortfolio();
  }, [status]);

  const profileId = doc?.profileId ?? profile.id;
  const stamp = doc ? `${doc.version}:${doc.updatedAt}` : '';
  const preview = useCallback(
    (args: EditorPreviewArgs): ReactNode =>
      args.mode === 'public' ? (
        <PublicPreview args={args} profileId={profileId} stamp={stamp} />
      ) : (
        <PortfolioPage
          spec={args.spec}
          content={args.content}
          graph={args.graph}
          mode={args.mode}
          skin={args.skin}
          onToggleSkin={args.onToggleSkin}
          editing={args.editing}
        />
      ),
    [profileId, stamp],
  );

  const back = (
    <button type="button" className="btn" onClick={() => actions.goto('graph')}>
      Back to Skill Graph
    </button>
  );

  if (!doc) {
    if (status === 'error') {
      return (
        <CalmPage
          kicker="Your portfolio"
          title="We couldn’t build your portfolio"
          actions={
            <>
              <button type="button" className="btn btn-primary" onClick={() => void actions.openPortfolio()}>
                Try again
              </button>
              {back}
            </>
          }
        >
          <span style={{ display: 'block' }}>{error ?? 'Something went wrong while reading your sources.'}</span>
          <span style={{ display: 'block' }}>Your graph is untouched.</span>
        </CalmPage>
      );
    }
    const label = step ?? (status === 'loading' ? 'Opening your portfolio…' : 'Getting started…');
    return (
      <CalmPage kicker="Your portfolio" title="Building your page" live actions={back}>
        {/* The Skill Graph is already known: draw it while the resume is read, so the wait has a shape. */}
        <span style={{ display: 'grid', justifyItems: 'center', gap: 'var(--s2)', marginBottom: 'var(--s4)' }} aria-hidden="true">
          <RadarGlyph values={profile.domains.map((d) => d.score)} size={168} />
          <span style={{ fontFamily: 'var(--font-serif)', fontSize: 'var(--fs-5)', color: 'var(--text)' }}>{profile.person.name}</span>
        </span>
        <AnimatePresence mode="wait" initial={false}>
          <motion.span
            key={label}
            style={{ display: 'block' }}
            initial={{ opacity: 0, y: 4 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.25, ease: EASE }}
          >
            {label}
          </motion.span>
        </AnimatePresence>
      </CalmPage>
    );
  }

  const working = busy ?? (status === 'generating' ? (step ?? 'Polishing the wording…') : undefined);
  return (
    <>
      <EditorView
        portfolio={doc}
        busy={working}
        onPatch={(patch) => void actions.patchPortfolio(patch)}
        onRewrite={(slotId) => void actions.rewriteSlot(slotId)}
        onBack={() => actions.goto('graph')}
        onRegenerate={() => void actions.regeneratePortfolio()}
        preview={preview}
      />
      <AnimatePresence>{error && <Notice key={error} message={error} />}</AnimatePresence>
    </>
  );
}
