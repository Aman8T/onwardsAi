/**
 * The bar above a match that was started from a Discover listing: a way back to Discover (with the lens as it was)
 * and the employer's own apply link, named. It shows only for that match: once the match has an address it is
 * remembered (`origin.ts`), and a different match, or a new one started from the paste screen, does not carry it.
 *
 * A match that finishes while this bar is waiting also tells the saved role about itself, so "Your applications" can
 * open it later. Rendered by the match section (`MatchRoot`); it decides for itself whether there is anything to show.
 */
import { useEffect } from 'react';
import { useMatch } from '../match/useMatch';
import { actions, useApp } from '../store';
import { ApplyLink } from './ListingRow';
import { bindOrigin, useOrigin } from './origin';
import type { MatchOrigin } from './origin';
import { parseDiscoverUrl } from './route';
import { attachMatchTo } from './tracker';
import '../styles/discover.css';

export function OriginBar() {
  const origin = useOrigin();
  const route = useApp((s) => s.matchRoute);
  const m = useMatch();

  // the match this origin started now has an address: remember it, and tell the saved role
  const finishedHere = !!origin && !origin.matchId && m.live && !!m.matchId && route.id === m.matchId;
  useEffect(() => {
    if (!finishedHere || !origin || !m.matchId) return;
    bindOrigin(m.matchId);
    if (origin.profileId) attachMatchTo(origin.profileId, origin.listingId, m.matchId, m.result?.band);
  }, [finishedHere, origin, m.matchId, m.result?.band]);

  if (!origin) return null;
  const showing = origin.matchId ? route.id === origin.matchId : (route.screen === 'paste' && m.phase === 'reading') || finishedHere;
  if (!showing) return null;

  const back = () => {
    const url = new URL(origin.discoverHref, window.location.origin);
    const q = parseDiscoverUrl(url.pathname, url.search);
    // every field is named, so one the address leaves out is cleared rather than left as it was
    actions.openDiscover(q ? { city: q.city, locality: q.locality, otherCity: q.otherCity, reach: q.reach, breadth: q.breadth, lane: q.lane, order: q.order, tab: q.tab } : {});
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
        <ApplyLink card={origin} className="btn btn-sm dc-btn-sm" />
        <span className="dc-faint dc-small">{origin.sourceLabel} · opens in a new tab</span>
      </span>
    </div>
  );
}
