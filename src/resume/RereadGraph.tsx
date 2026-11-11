/**
 * "Your graph reads every block": the card that reads the whole graph again from everything the person has given, every
 * resume file, the lines they said in their own words and their links (`actions.rereadGraph`, a full analysis started
 * from this profile). It says what a re-read takes in, that it replaces the graph on the dashboard while the resumes stay,
 * and has one button behind a small inline confirm. The button is disabled, with its reason, when nothing is newer than the
 * profile's last analysis: no file added and no own-words line newer than it.
 *
 * Self-contained: the file dates come from `GET /api/resumes/files` (read here), the link count and the profile's date from
 * the store. `ownWordsNewest` (an ISO date) is optional: with no date for the own-words lines they never count as new.
 */
import { useEffect, useId, useRef, useState } from 'react';
import { actions, useApp } from '../store';
import '../styles/reread.css';
import { getFiles } from './api';

export interface RereadGraphProps {
  profileId: string;
  /** How many resume files the profile holds. */
  files: number;
  /** How many lines the person said in their own words. */
  ownWords: number;
  /** When the newest own-words line was said (ISO date), when the caller knows it. */
  ownWordsNewest?: string | null;
}

const times = (iso: string | null | undefined): number => {
  const t = iso ? Date.parse(iso) : NaN;
  return Number.isFinite(t) ? t : NaN;
};

function plural(n: number, one: string, many: string): string {
  return `${n} ${n === 1 ? one : many}`;
}

function sentence(parts: string[]): string {
  if (parts.length <= 1) return parts[0] ?? '';
  return `${parts.slice(0, -1).join(', ')} and ${parts[parts.length - 1]}`;
}

export function RereadGraph({ profileId, files, ownWords, ownWordsNewest }: RereadGraphProps) {
  const profile = useApp((s) => (s.profile && s.profile.id === profileId ? s.profile : null));
  const busy = useApp((s) => s.analysis.phase === 'reading' || s.analysis.phase === 'synthesizing');
  const [open, setOpen] = useState(false);
  const [newestFile, setNewestFile] = useState<number | null | undefined>(undefined); // undefined: not known (yet)
  const btn = useRef<HTMLButtonElement>(null);
  const keep = useRef<HTMLButtonElement>(null);
  const id = useId();

  useEffect(() => {
    const ctrl = new AbortController();
    setNewestFile(undefined);
    getFiles(profileId, ctrl.signal)
      .then((r) => {
        const dates = r.files.filter((f) => f.origin === 'upload').map((f) => times(f.uploadedAt)).filter((t) => !Number.isNaN(t));
        setNewestFile(dates.length ? Math.max(...dates) : null);
      })
      .catch(() => {
        if (!ctrl.signal.aborted) setNewestFile(undefined);
      });
    return () => ctrl.abort();
  }, [profileId, files]);

  useEffect(() => {
    if (!open) return;
    keep.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return;
      e.preventDefault();
      setOpen(false);
      btn.current?.focus();
    };
    window.addEventListener('keydown', onKey, true);
    return () => window.removeEventListener('keydown', onKey, true);
  }, [open]);

  const links = profile
    ? new Set(profile.sources.filter((s) => s.url && s.kind !== 'resume' && s.kind !== 'notes').map((s) => s.url)).size
    : 0;
  const parts = [
    ...(files > 0 ? [plural(files, 'resume file', 'resume files')] : []),
    ...(ownWords > 0 ? [`${plural(ownWords, 'line', 'lines')} in your own words`] : []),
    ...(links > 0 ? [`your ${plural(links, 'link', 'links')}`] : []),
  ];

  const since = times(profile?.createdAt);
  const newFile = newestFile === undefined ? true : newestFile !== null && (Number.isNaN(since) || newestFile > since);
  const newWords = ownWords > 0 && !Number.isNaN(times(ownWordsNewest)) && (Number.isNaN(since) || times(ownWordsNewest) > since);
  const nothingNew = !newFile && !newWords;
  const reason = !profile
    ? 'Open your own graph to update it.'
    : busy
      ? 'An analysis is already running.'
      : nothingNew
        ? 'Nothing is new since your last analysis: add a resume file or say a line in your own words first.'
        : '';
  const disabled = reason !== '';

  return (
    <section className="panel rr-card" aria-labelledby={`${id}-h`}>
      <span className="kicker">Update my graph</span>
      <h2 id={`${id}-h`} className="rr-title">
        Your graph reads every block
      </h2>
      <p className="rr-text">
        A re-read takes in <span className="rr-takes">{parts.length ? sentence(parts) : 'what you have given so far'}</span>. It runs a
        full analysis and replaces the graph on your dashboard; your resumes stay.
      </p>
      {!open && (
        <div className="rr-actions">
          <button
            ref={btn}
            type="button"
            className="btn btn-primary"
            disabled={disabled}
            aria-describedby={disabled ? `${id}-why` : undefined}
            aria-haspopup="dialog"
            aria-expanded={open}
            onClick={() => setOpen(true)}
          >
            Update my graph
          </button>
          {disabled && (
            <span id={`${id}-why`} className="rr-reason">
              {reason}
            </span>
          )}
        </div>
      )}
      {open && (
        <div className="rr-confirm" role="dialog" aria-labelledby={`${id}-t`} aria-describedby={`${id}-d`}>
          <p id={`${id}-t`} className="rr-confirm-title">
            Read the graph again?
          </p>
          <p id={`${id}-d`} className="rr-text">
            It takes a minute or two and replaces the graph on your dashboard with a new one. Your resumes, files and notes stay with you.
          </p>
          <div className="rr-confirm-actions">
            <button
              ref={keep}
              type="button"
              className="btn"
              onClick={() => {
                setOpen(false);
                btn.current?.focus();
              }}
            >
              Keep my graph
            </button>
            <button
              type="button"
              className="btn btn-primary"
              onClick={() => {
                setOpen(false);
                void actions.rereadGraph();
              }}
            >
              Update my graph
            </button>
          </div>
        </div>
      )}
    </section>
  );
}
