/**
 * The Weekly's reading room, without any wiring: it draws what it is given, so the screen (`WeeklyView`) and the dev
 * gallery share it.
 *
 *   signed out         the sample edition and a way in
 *   never subscribed   the subscription panel first, then the sample edition (or the reader's own preview, once built)
 *   subscribed         the selected (else latest) edition, then the subscription panel and the archive
 *
 * "Sample edition" is said whenever the paper on screen is the fixture, and its listings carry no links.
 */
import type { ReactNode } from 'react';
import type { WeeklyEdition, WeeklyEditionSummary, WeeklySubscription, WeeklySubscriptionUpdate } from '../../shared/weekly';
import { Archive } from './Archive';
import { EditionPaper } from './EditionPaper';
import { SAMPLE_EDITION } from './fixtures';
import { PANEL_ID, SubscriptionPanel, UNSUBSCRIBE_ID } from './SubscriptionPanel';

export interface WeeklyRoomProps {
  /** `signed-out`: nobody is signed in; `loading`: the answer is on its way; `error`: it failed; `ready`: a subscription is known. */
  mode: 'signed-out' | 'loading' | 'error' | 'ready';
  error?: string;
  onRetry?: () => void;
  onSignIn?: () => void;
  subscription?: WeeklySubscription;
  onSaved?: (next: WeeklySubscription) => void;
  save?: (update: WeeklySubscriptionUpdate) => Promise<WeeklySubscription>;
  editions: WeeklyEditionSummary[];
  /** The edition on screen, when one is loaded. */
  edition?: WeeklyEdition;
  editionLoading?: boolean;
  editionError?: string;
  onOpenEdition: (id: string) => void;
  /** "Read the latest edition first": build this week's edition now. */
  onPreview?: () => void;
  previewing?: boolean;
  previewError?: string;
}

function focusPanel(target: string) {
  const el = document.getElementById(target) ?? document.getElementById(PANEL_ID);
  if (!el) return;
  const calm = typeof window.matchMedia === 'function' && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  el.scrollIntoView({ behavior: calm ? 'auto' : 'smooth', block: 'center' });
  el.focus({ preventScroll: true });
}

export function WeeklyRoom(p: WeeklyRoomProps) {
  const sub = p.subscription;
  const subscribed = p.mode === 'ready' && !!sub && sub.status !== 'off';
  const latest = p.editions[0];

  if (p.mode === 'loading') {
    return (
      <p className="wk-quiet-page" role="status">
        Opening your paper…
      </p>
    );
  }
  if (p.mode === 'error') {
    return (
      <div className="panel wk-notice" role="alert">
        <p>{p.error ?? 'Leafout ran into trouble. Please try again in a moment.'}</p>
        {p.onRetry && (
          <button type="button" className="btn wk-btn" onClick={p.onRetry}>
            Try again
          </button>
        )}
      </div>
    );
  }

  const edition = p.edition;
  const loadingOnly = !edition && !!p.editionLoading;
  const showSample = !edition && !loadingOnly;

  const gate: ReactNode =
    p.mode === 'signed-out' ? (
      <section className="panel wk-panel" id={PANEL_ID} aria-labelledby="wk-gate-h" tabIndex={-1}>
        <p className="kicker">New · every Monday</p>
        <h2 className="serif wk-panel-h" id="wk-gate-h">
          Get your own paper
        </h2>
        <p className="wk-lede">
          Openings that fit your graph, your week's progress and news from your field, as an old-style newspaper. Free with a Leafout account.
        </p>
        <div className="wk-actions">
          <button type="button" className="btn btn-primary wk-btn" onClick={p.onSignIn}>
            Sign in to subscribe
          </button>
        </div>
      </section>
    ) : sub ? (
      <SubscriptionPanel subscription={sub} save={p.save} onSaved={p.onSaved ?? (() => {})} />
    ) : null;

  const preview: ReactNode =
    p.mode === 'ready' && showSample && p.onPreview ? (
      <div className="wk-preview">
        <button type="button" className="btn wk-btn" onClick={p.onPreview} disabled={p.previewing}>
          {p.previewing ? 'Setting this week in type…' : 'Read the latest edition first'}
        </button>
        {p.previewError && (
          <p className="wk-error" role="alert">
            {p.previewError}
          </p>
        )}
      </div>
    ) : null;

  const paper: ReactNode = (
    <div className="wk-sheet">
      {showSample ? (
        <p className="wk-sample">
          <span className="chip">Sample edition</span> Built from a sample reader. Yours is built from your own graph, with the same rule: every line
          cited.
        </p>
      ) : (
        loadingOnly && (
          <p className="wk-quiet-page" role="status">
            Opening the edition…
          </p>
        )
      )}
      {p.editionError && (
        <p className="wk-error" role="alert">
          {p.editionError}
        </p>
      )}
      {!loadingOnly && (
        <EditionPaper
          edition={edition ?? SAMPLE_EDITION}
          sample={showSample}
          onManage={subscribed ? () => focusPanel(PANEL_ID) : undefined}
          onUnsubscribe={subscribed ? () => focusPanel(UNSUBSCRIBE_ID) : undefined}
        />
      )}
    </div>
  );

  const firstRun = subscribed && !latest && showSample;

  return (
    <div className={`wk-room${subscribed && !showSample ? '' : ' wk-room--gate'}`}>
      <div className="wk-side-col">
        {gate}
        {preview}
        {p.mode === 'ready' && subscribed && <Archive editions={p.editions} current={edition?.id} onOpen={p.onOpenEdition} />}
        {firstRun && (
          <div className="panel wk-notice">
            <p>Your first edition arrives on your next day. Until then, here is a sample, or read this week's now.</p>
          </div>
        )}
      </div>
      <div className="wk-paper-col">{paper}</div>
    </div>
  );
}
