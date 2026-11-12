/**
 * "Share my skill shape". It opens the share sheet, which is loaded the first time it is asked for: nobody who never
 * shares pays for it. Three looks, one behaviour: the graph header's quiet button, a primary button on the dashboard's
 * profile card, and a whole slip (the caller's children are the slip's contents). The sheet's two actions for a graph
 * that cannot share live here, because they leave the sheet: "Analyse my resume" starts over from Home, "Sign in"
 * opens the sign-in sheet.
 */
import { Suspense, lazy, useState } from 'react';
import type { ReactNode } from 'react';
import { offersSignIn } from '../auth/links';
import { signInSheet, useMe } from '../auth/useMe';
import { actions } from '../store';
import '../styles/card.css';

const ShareSheet = lazy(() => import('./ShareSheet').then((m) => ({ default: m.ShareSheet })));

export type ShareLook = 'header' | 'primary' | 'slip';


/**
 * The sheet and the way to open it, for a caller that draws its own controls (the dashboard's slip, once the card is
 * live, has two buttons and so cannot itself be one). Render `sheet` anywhere in the output; call `show` to open it.
 */
export function useShareSheet(profileId: string): { show: () => void; open: boolean; sheet: ReactNode } {
  const [open, setOpen] = useState(false);
  // once asked for, the sheet stays mounted so it can play its exit
  const [asked, setAsked] = useState(false);
  const { providers, loaded } = useMe();

  const show = () => {
    setAsked(true);
    setOpen(true);
  };

  const sheet = asked ? (
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
  ) : null;

  return { show, open, sheet };
}

export function ShareButton({ profileId, look = 'header', className, children, label = 'Share my skill shape' }: { profileId: string; look?: ShareLook; className?: string; children?: ReactNode; /** The button's words (the dashboard's primary button says "Share your skill graph"). */ label?: string }) {
  const { show, open, sheet } = useShareSheet(profileId);

  return (
    <>
      <button
        type="button"
        className={look === 'header' ? 'btn btn-quiet gv-portfolio gv-share' : look === 'primary' ? `btn btn-primary${className ? ` ${className}` : ''}` : (className ?? '')}
        aria-label={look === 'slip' ? undefined : label}
        aria-haspopup="dialog"
        aria-expanded={open}
        title="Share your skill shape as a card for your GitHub README or LinkedIn"
        onClick={show}
      >
        {look === 'header' ? (
          <>
            <span className="gv-share-long" aria-hidden="true">
              Share my skill shape
            </span>
            <span className="gv-share-short" aria-hidden="true">
              Share
            </span>
          </>
        ) : (
          (children ?? label)
        )}
      </button>
      {sheet}
    </>
  );
}
