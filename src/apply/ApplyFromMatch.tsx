/**
 * The interim "Apply for me" on a posting match: a link to `/apply/new?match=<id>&resume=saved`, where the checks run
 * before anything starts. It never asks for a link and starts nothing itself. Package FE3 replaces it with the full
 * choice (the two resumes, the link field for a pasted match, the blocked and applied states).
 *
 * Renders nothing while auto-apply is off or the match has no id. Signed out, it opens the sign-in sheet instead
 * (auto-apply is signed-in only). It renders nothing on the server (the status is read after mount), so the match
 * parity render is unchanged. `links` is the old signature, kept so MatchView needs no change; it is not used.
 */
import { useEffect, useState } from 'react';
import { signInSheet } from '../auth/useMe';
import { applyApi } from './api';
import { applyPath } from './route';

type Offer = 'none' | 'signed_in' | 'signed_out';

export function ApplyFromMatch({ matchId }: { matchId?: string; links?: readonly string[] }) {
  const [offer, setOffer] = useState<Offer>('none');
  useEffect(() => {
    let live = true;
    applyApi
      .status()
      .then((s) => live && setOffer(!s.enabled ? 'none' : s.signedIn ? 'signed_in' : 'signed_out'))
      .catch(() => live && setOffer('none'));
    return () => {
      live = false;
    };
  }, []);
  if (offer === 'none' || !matchId) return null;
  const href = applyPath({ screen: 'new', matchId, resume: 'saved' });
  if (offer === 'signed_out') {
    return (
      <button type="button" className="btn btn-quiet mt-btn" onClick={() => signInSheet.open('account', undefined, href)}>
        Apply for me
      </button>
    );
  }
  return (
    <a className="btn btn-quiet mt-btn" href={href}>
      Apply for me
    </a>
  );
}
