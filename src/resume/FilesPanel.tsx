/**
 * "Your files": every file the blocks came from, oldest first. Each row says what it is (name, size, when, how it came
 * here), how its lines merged when they did, and gives the way to its resume and to its original.
 */
import { countsLine, originText, shortDate } from './model';
import { ScreenLink } from './parts';
import type { ResumeQuery } from './route';
import type { Busy, LoadStatus, ResumeFile } from './types';
import { formatSize } from './upload';
import { MAX_RESUME_FILES } from './types';

export interface FilesPanelProps {
  files: readonly ResumeFile[] | null;
  status: LoadStatus;
  busy: Busy | null;
  /** Resumes whose original was not kept (hidden for the rest of the visit). */
  noOriginal: ReadonlySet<string>;
  /** Files whose merge result is still on hand (those added this visit). */
  merged: ReadonlySet<string>;
  onRetry: () => void;
  onNavigate: (to: ResumeQuery) => void;
  onOpenMerge: (fileId: string) => void;
  onDownloadOriginal: (resumeId: string, filename: string) => void;
}

export function FilesPanel({ files, status, busy, noOriginal, merged, onRetry, onNavigate, onOpenMerge, onDownloadOriginal }: FilesPanelProps) {
  return (
    <section className="m-tile m-graph rs-card" aria-labelledby="rs-files-h">
      <h2 id="rs-files-h" className="kicker">
        Your files{files ? <> · <span className="numeral">{files.length}</span></> : null}
      </h2>
      {files && files.length > 0 ? (
        <ul className="rs-rows rs-rows-in">
          {files.map((f) => {
            const counts = countsLine(f.counts);
            const lost = f.resumeId ? noOriginal.has(f.resumeId) : false;
            const fetching = busy?.kind === 'original' && busy.resumeId === f.resumeId;
            return (
              <li key={f.id} className="rs-row rs-row-single">
                <span className="rs-row-main">
                  <span className="rs-small rs-strong">{f.filename}</span>
                  <span className="rs-fine">
                    {formatSize(f.size)} · {shortDate(f.uploadedAt)} · {originText(f)}
                  </span>
                  {counts && <span className="rs-cite">{counts}</span>}
                </span>
                <span className="rs-actions rs-file-acts">
                  {f.resumeId && (
                    <ScreenLink to={{ screen: 'resume', id: f.resumeId }} onNavigate={onNavigate} className="rs-link rs-small">
                      Open its resume
                    </ScreenLink>
                  )}
                  {merged.has(f.id) && (
                    <button type="button" className="rs-linkbtn rs-small" onClick={() => onOpenMerge(f.id)}>
                      How it merged
                    </button>
                  )}
                  {f.resumeId && !lost && (
                    <button type="button" className="btn btn-sm rs-btn" disabled={busy !== null} aria-busy={fetching} onClick={() => onDownloadOriginal(f.resumeId as string, f.filename)}>
                      {fetching ? 'Preparing…' : 'Download the original'}
                      <span className="sr-only">: {f.filename}</span>
                    </button>
                  )}
                </span>
                {lost && <span className="rs-fine rs-file-note">The original of this file was not kept. Files added from today are.</span>}
              </li>
            );
          })}
        </ul>
      ) : status === 'error' ? (
        <p className="rs-fine">
          Your files did not load.{' '}
          <button type="button" className="rs-linkbtn" onClick={onRetry}>
            Try again
          </button>
        </p>
      ) : status === 'ready' ? (
        <p className="rs-fine">No file is kept for this profile yet.</p>
      ) : (
        <p className="rs-fine" role="status" aria-busy="true">
          Opening your files…
        </p>
      )}
      <p className="rs-fine">Add another with "Add a resume file" above. Up to {MAX_RESUME_FILES} files are kept, each with its own words.</p>
    </section>
  );
}
