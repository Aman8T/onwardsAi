/**
 * "Share my skill shape", the graph header's button. It opens the share sheet, which is loaded the first time it is
 * asked for: nobody who never shares pays for it. The sheet's two actions for a graph that cannot share live here,
 * because they leave the sheet: "Analyse my resume" starts over from Home, "Sign in" opens the sign-in sheet.
 */
import { Suspense, lazy, useState } from 'react';
import { offersSignIn } from '../auth/links';
import { signInSheet, useMe } from '../auth/useMe';
import { actions } from '../store';
import '../styles/card.css';

const ShareSheet = lazy(() => import('./ShareSheet').then((m) => ({ default: m.ShareSheet })));

export function ShareButton({ profileId }: { profileId: string }) {
  const [open, setOpen] = useState(false);
  // once asked for, the sheet stays mounted so it can play its exit
  const [asked, setAsked] = useState(false);
  const { providers, loaded } = useMe();

  const show = () => {
    setAsked(true);
    setOpen(true);
  };

  return (
    <>
      <button
        type="button"
        className="btn btn-quiet gv-portfolio gv-share"
        aria-label="Share my skill shape"
        aria-haspopup="dialog"
        aria-expanded={open}
        title="Share your skill shape as a card for your GitHub README or LinkedIn"
        onClick={show}
      >
        <span className="gv-share-long" aria-hidden="true">
          Share my skill shape
        </span>
        <span className="gv-share-short" aria-hidden="true">
          Share
        </span>
      </button>
      {asked && (
        <Suspense fallback={null}>
          <ShareSheet
            open={open}
            onClose={() => setOpen(false)}
            profileId={profileId}
            onAnalyse={() => {
              setOpen(false);
              actions.startOver();
            }}
            onSignIn={
              loaded && offersSignIn(providers)
                ? () => {
                    setOpen(false);
                    signInSheet.open('save');
                  }
                : undefined
            }
          />
        </Suspense>
      )}
    </>
  );
}
