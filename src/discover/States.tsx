/**
 * The states between "a full list" and "nothing": loading, the employers' boards being read for the first time, a
 * request that failed, a lane with nothing in it (and what would change that), every role being a stretch, a profile
 * too short for closer words, and the honest note about coverage. Each says what is true in plain short sentences and
 * offers the one useful next step; none of them guesses.
 */
import type { ReactNode } from 'react';
import type { DiscoverCoverage, DiscoverResponse, LensStop } from '../../shared/discover';
import { timeAgo } from '../lib/ui';
import { emptyReason, joinNames, topNotFound } from './model';
import type { EmptyKind } from './model';

function StateCard({ title, titleId, children, actions, role = 'status' }: { title: string; titleId: string; children?: ReactNode; actions?: ReactNode; role?: 'status' | 'alert' }) {
  return (
    <section className="panel dc-state" aria-labelledby={titleId} role={role}>
      <h2 id={titleId} className="dc-state-title">
        {title}
      </h2>
      {children}
      {actions && <div className="dc-actions">{actions}</div>}
    </section>
  );
}

/* ───────────────────────── loading ───────────────────────── */

/** Quiet outline rows: the shape of what is coming, with nothing moving. */
export function LoadingRows({ label = 'Finding roles that fit you…' }: { label?: string }) {
  return (
    <div className="dc-loading" aria-busy="true">
      <p className="dc-muted" role="status">
        {label}
      </p>
      <ul className="dc-skel-list" aria-hidden="true">
        {[0, 1, 2].map((i) => (
          <li key={i} className="dc-skel">
            <span className="dc-skel-bar" />
            <span className="dc-skel-bar is-short" />
          </li>
        ))}
      </ul>
    </div>
  );
}

/* ───────────────────────── first refresh ───────────────────────── */

export function FirstRefresh({ note, waited, onCheck }: { note?: string; waited: boolean; onCheck: () => void }) {
  return (
    <StateCard
      title={note ?? "Reading employers' job boards for the first time. This takes about a minute."}
      titleId="dc-first-t"
      actions={
        waited ? (
          <button type="button" className="btn btn-sm dc-btn-sm" onClick={onCheck}>
            Check again
          </button>
        ) : undefined
      }
    >
      <p className="dc-muted">{waited ? "Still reading. It's slower than usual; you can check again, or come back in a few minutes." : "We'll look again in a moment. Nothing is shown until it's read, so nothing here is a guess."}</p>
    </StateCard>
  );
}

/* ───────────────────────── error ───────────────────────── */

export function ErrorState({ message, onRetry }: { message?: string; onRetry: () => void }) {
  return (
    <StateCard
      role="alert"
      title="Discover didn't load."
      titleId="dc-err-t"
      actions={
        <button type="button" className="btn btn-primary btn-sm dc-btn-sm" onClick={onRetry}>
          Try again
        </button>
      }
    >
      <p className="dc-muted">{message ?? 'Something went wrong. Please try again in a moment.'}</p>
    </StateCard>
  );
}

/** Discover is not possible for this profile at all (the sample, for one). */
export function Blocked({ title, children, action }: { title: string; children: ReactNode; action?: ReactNode }) {
  return (
    <StateCard title={title} titleId="dc-blocked-t" actions={action}>
      <p className="dc-muted">{children}</p>
    </StateCard>
  );
}

/* ───────────────────────── nothing in this lane ───────────────────────── */

const stopWords = (res: DiscoverResponse, stop: LensStop) => (stop.value === 'city' ? `in ${res.area.city ?? 'your city'}` : `within ${stop.label}`);

function emptyCopy(res: DiscoverResponse, why: EmptyKind, otherCity?: string): { text: string; action?: { label: string; run: 'reach' | 'breadth' | 'remote' } } {
  const remote = why.remote > 0 ? ` ${why.remote} remote.` : '';
  if (res.lane === 'remote') {
    if (why.kind === 'breadth') return { text: `No remote roles fit well yet. ${why.stop.count} more are a stretch.`, action: { label: 'Show them anyway', run: 'breadth' } };
    return { text: 'No remote roles open to India yet. We leave out roles that say remote but not where.' };
  }
  if (res.lane === 'city') {
    if (why.kind === 'breadth') return { text: `Only strong fits show in ${otherCity ?? 'that city'}, and none does yet. ${why.stop.count} more are a stretch.`, action: { label: 'Show them anyway', run: 'breadth' } };
    return { text: `Nothing in ${otherCity ?? 'that city'} fits strongly yet. Only strong fits show there.` };
  }
  const near = res.reach === 'city' || !res.area.locality ? `in ${res.area.city ?? 'your city'}` : `within ${res.reach} km of ${res.area.locality}`;
  if (why.kind === 'reach') {
    return {
      text: `Nothing ${near}. ${why.stop.count} ${stopWords(res, why.stop)}.${remote}`,
      action: { label: why.stop.value === 'city' ? 'Show the whole city' : `Widen to ${why.stop.label}`, run: 'reach' },
    };
  }
  if (why.kind === 'breadth') {
    return why.stop.value === 'next'
      ? { text: `Nothing here is quite the work you do now. ${why.stop.count} fit where you're heading.${remote}`, action: { label: 'Include next steps', run: 'breadth' } }
      : { text: `Nothing near you fits well yet. ${why.stop.count} ${why.stop.count === 1 ? 'role is a stretch' : 'roles are a stretch'}.${remote}`, action: { label: 'Show them anyway', run: 'breadth' } };
  }
  return { text: `Nothing ${near} yet.${remote}` };
}

export interface EmptyStateProps {
  res: DiscoverResponse;
  otherCity?: string;
  onWiden: (reach: LensStop['value']) => void;
  onBreadth: (breadth: LensStop['value']) => void;
  onRemote: () => void;
  onPaste: () => void;
}

export function EmptyState({ res, otherCity, onWiden, onBreadth, onRemote, onPaste }: EmptyStateProps) {
  const why = emptyReason(res);
  const copy = emptyCopy(res, why, otherCity);
  const run = () => {
    if (!copy.action) return;
    if (copy.action.run === 'reach' && why.kind === 'reach') onWiden(why.stop.value);
    else if (copy.action.run === 'breadth' && why.kind === 'breadth') onBreadth(why.stop.value);
  };
  return (
    <StateCard
      title={copy.text}
      titleId="dc-empty-t"
      actions={
        <>
          {copy.action && (
            <button type="button" className="btn btn-primary dc-btn" onClick={run}>
              {copy.action.label}
            </button>
          )}
          {res.lane === 'near' && res.laneCounts.remote > 0 && (
            <button type="button" className="btn dc-btn" onClick={onRemote}>
              See {res.laneCounts.remote} remote
            </button>
          )}
          <button type="button" className="btn dc-btn" onClick={onPaste}>
            Paste a posting you found
          </button>
        </>
      }
    />
  );
}

/* ───────────────────────── banners ───────────────────────── */

/** Every role listed is a stretch or can't be told: say what most of them ask for that the evidence doesn't show. */
export function WeakBanner({ res }: { res: DiscoverResponse }) {
  const names = topNotFound(res.listings, 3);
  return (
    <section className="dc-banner" aria-label="How well these fit">
      <p className="dc-banner-text">
        Nothing near you fits well yet.{names.length > 0 ? ` Most of these ask for ${joinNames(names)}, which you can't show yet.` : ''}
      </p>
      {names.length > 0 && (
        <div className="dc-gaps">
          {names.map((n) => (
            <span key={n} className="dc-gap">
              Not found by name: {n}
            </span>
          ))}
        </div>
      )}
    </section>
  );
}

/** The profile is too short for closer words. */
export function ThinBanner({ onAdd }: { onAdd: () => void }) {
  return (
    <section className="dc-banner" aria-label="About your profile">
      <p className="dc-banner-text">Your profile is short, so these fits are rough. Most read "Can't tell yet".</p>
      <button type="button" className="btn btn-primary btn-sm dc-btn-sm" onClick={onAdd}>
        Add a project or link
      </button>
    </section>
  );
}

/* ───────────────────────── coverage ───────────────────────── */

/** The honest line about coverage, when it is thin for this person, and the way around it. */
export function CoverageNote({ note, onPaste }: { note: string; onPaste: () => void }) {
  return (
    <section className="dc-note" aria-label="About coverage">
      <p className="dc-muted">{note}</p>
      <button type="button" className="btn btn-sm dc-btn-sm" onClick={onPaste}>
        Paste a posting
      </button>
    </section>
  );
}

export function Footer({ coverage, refreshedAt }: { coverage: DiscoverCoverage; refreshedAt?: string }) {
  const at = refreshedAt ? Date.parse(refreshedAt) : NaN;
  return (
    <footer className="dc-footer">
      <p>Roles come from employers' own job boards. We never use LinkedIn data.</p>
      {coverage.employers > 0 && (
        <p className="dc-faint">
          Read from <span className="numeral">{coverage.employers}</span> employers' boards · <span className="numeral">{coverage.listings}</span> open roles
          {Number.isFinite(at) ? ` · last read ${timeAgo(at)}` : ''}.
        </p>
      )}
    </footer>
  );
}
