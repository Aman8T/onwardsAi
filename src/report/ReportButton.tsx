/**
 * "Share where I can go": the graph header's button for the shareable report (N1, beta). It opens the owner sheet,
 * which is loaded the first time it is asked for, so nobody who never shares pays for it. It sits beside the radar
 * card's "Share my skill shape" and is the same size. The sheet's actions that leave it live here: "Analyse my resume"
 * starts over from Home, "Sign in" opens the sign-in sheet, "What would show it" opens Prepare for that role, and
 * "Open your resume once" opens /resume.
 */
import { Suspense, lazy, useState } from 'react';
import { offersSignIn } from '../auth/links';
import { signInSheet, useMe } from '../auth/useMe';
import { actions } from '../store';
import { openPrepare } from './nav';
import '../styles/report.css';

const ReportSheet = lazy(() => import('./ReportSheet').then((m) => ({ default: m.ReportSheet })));

export function ReportButton({ profileId }: { profileId: string }) {
  const [open, setOpen] = useState(false);
  // once asked for, the sheet stays mounted so it can play its exit
  const [asked, setAsked] = useState(false);
  const { providers, loaded } = useMe();

  const close = () => setOpen(false);

  return (
    <>
      <button
        type="button"
        className="btn btn-quiet gv-portfolio rp-button"
        aria-label="Share where I can go"
        aria-haspopup="dialog"
        aria-expanded={open}
        title="Share a page of the roles your resume backs, with the lines behind each"
        onClick={() => {
          setAsked(true);
          setOpen(true);
        }}
      >
        <svg className="rp-button-icon" width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.25" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="M2 12L12 2M5.5 2H12v6.5" />
        </svg>
        <span className="rp-button-long" aria-hidden="true">
          Share where I can go
        </span>
        <span className="rp-button-short" aria-hidden="true">
          Where next
        </span>
      </button>
      {asked && (
        <Suspense fallback={null}>
          <ReportSheet
            open={open}
            onClose={close}
            profileId={profileId}
            onAnalyse={() => {
              close();
              actions.startOver();
            }}
            onSignIn={
              loaded && offersSignIn(providers)
                ? () => {
                    close();
                    signInSheet.open('save');
                  }
                : undefined
            }
            onPrepare={(roleId) => {
              close();
              openPrepare(roleId);
            }}
            onResume={() => {
              close();
              actions.openResume();
            }}
          />
        </Suspense>
      )}
    </>
  );
}
