/**
 * The bar above a match that was started from a Discover listing: a way back to Discover (with the lens as it was)
 * and the employer's own apply link, named. It shows only for that match: once the match has an address it is
 * remembered (`origin.ts`), and a different match, or a new one started from the paste screen, does not carry it.
 *
 * Rendered by the match section (`MatchRoot`); it decides for itself whether there is anything to show.
 */
import { useEffect } from 'react';
import { useMatch } from '../match/useMatch';
import { actions, useApp } from '../store';
import { bindOrigin, useOrigin } from './origin';
import type { MatchOrigin } from './origin';
import { parseDiscoverUrl } from './route';
import { applyLabel, safeHref } from './util';
import '../styles/discover.css';

export function OriginBar() {
  const origin = useOrigin();
  const route = useApp((s) => s.matchRoute);
  const m = useMatch();

  // the match this origin started now has an address: remember which one, so the bar shows on that match only
  const finishedHere = !!origin && !origin.matchId && m.live && !!m.matchId && route.id === m.matchId;
  useEffect(() => {
    if (!finishedHere || !m.matchId) return;
    bindOrigin(m.matchId);
  }, [finishedHere, m.matchId]);

  if (!origin) return null;
  const showing = origin.matchId ? route.id === origin.matchId : (route.screen === 'paste' && m.phase === 'reading') || finishedHere;
  if (!showing) return null;

  const back = () => {
    const url = new URL(origin.discoverHref, window.location.origin);
    const q = parseDiscoverUrl(url.pathname, url.search);
    // every field is named, so one the address leaves out is cleared rather than left as it was
    actions.openDiscover(q ? { city: q.city, remote: q.remote, tab: q.tab, run: q.run, job: q.job, ids: q.ids } : {});
  };
  return <OriginBarView origin={origin} onBack={back} />;
}

/** The bar itself: presentational, so the dev gallery can show it without a match behind it. */
export function OriginBarView({ origin, onBack }: { origin: MatchOrigin; onBack: () => void }) {
  return (
    <div className="dc-origin" role="region" aria-label="Where this posting came from">
      <a
        className="dc-origin-back"
        href={origin.discoverHref}
        onClick={(e) => {
          if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button !== 0) return;
          e.preventDefault();
          onBack();
        }}
      >
        <span aria-hidden="true">← </span>Discover
      </a>
      <span className="dc-origin-role">
        <span className="dc-strong">{origin.title}</span>
        <span className="dc-muted"> · {origin.employer}</span>
      </span>
      <span className="dc-origin-apply">
        <OriginApply origin={origin} />
        <span className="dc-faint dc-small">{origin.sourceLabel} · opens in a new tab</span>
      </span>
    </div>
  );
}

/** "Apply on jobs.lever.co ↗", opening the employer's own posting in a new tab (only a plain web address is linked). */
function OriginApply({ origin }: { origin: MatchOrigin }) {
  const href = safeHref(origin.applyUrl);
  if (!href) return <span className="dc-faint">{`Apply on ${origin.applyHost}`}</span>;
  return (
    <a
      className="btn btn-sm dc-btn-sm"
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      aria-label={`Apply on ${origin.applyHost} for ${origin.title} at ${origin.employer}, opens in a new tab`}
    >
      {applyLabel(origin)}
    </a>
  );
}
