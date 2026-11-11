/**
 * "Save to my resumes", on a match's resume screen: the tailored resume joins the list at /resume as a resume of its
 * own. The server makes it once (pressing again returns the same one), so a second press is harmless.
 */
import { useState } from 'react';
import { actions } from '../store';
import { saveFromMatch } from './api';
import { resumePath } from './route';
import '../styles/resume.css';

export function SaveToResumes({ matchId }: { matchId: string }) {
  const [state, setState] = useState<{ status: 'idle' | 'saving' | 'saved' | 'error'; id?: string; message?: string }>({ status: 'idle' });

  const save = async () => {
    if (state.status === 'saving') return;
    setState({ status: 'saving' });
    try {
      const detail = await saveFromMatch(matchId);
      setState({ status: 'saved', id: detail.resume.id });
    } catch (err) {
      setState({ status: 'error', message: err instanceof Error ? err.message : 'It could not be saved just now.' });
    }
  };

  return (
    <div className="rs-save">
      {state.status === 'saved' && state.id ? (
        <p className="rs-small" role="status">
          Saved. It is in your resumes.{' '}
          <a
            href={resumePath({ screen: 'resume', id: state.id })}
            className="rs-link"
            onClick={(e) => {
              if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
              e.preventDefault();
              actions.openResume({ screen: 'resume', id: state.id });
            }}
          >
            Open it
          </a>
        </p>
      ) : (
        <>
          <button type="button" className="btn btn-sm rs-btn" disabled={state.status === 'saving'} aria-busy={state.status === 'saving'} onClick={() => void save()}>
            {state.status === 'saving' ? 'Saving…' : 'Save to my resumes'}
          </button>
          {state.status === 'error' && (
            <p className="rs-notice" role="alert">
              {state.message}
            </p>
          )}
        </>
      )}
    </div>
  );
}
