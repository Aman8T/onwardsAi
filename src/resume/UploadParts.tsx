/**
 * "Add a resume file": a real file input behind a button, and the line under the header that says what is happening
 * to it ("Reading riya.pdf…") or why nothing was added. A failed file never costs the page its state: the strip only
 * adds a line, and "Try again" sends the same file once more.
 */
import { useId, useRef } from 'react';
import { ACCEPT, ACCEPT_HINT } from './upload';
import type { UploadFailure } from './types';

export function UploadButton({ onPick, reading, locked, className = 'btn btn-sm rs-btn' }: { onPick: (file: File) => void; reading: boolean; locked: boolean; className?: string }) {
  const input = useRef<HTMLInputElement>(null);
  const hint = useId();
  return (
    <>
      <button
        type="button"
        className={className}
        disabled={reading || locked}
        aria-busy={reading}
        aria-describedby={hint}
        onClick={() => input.current?.click()}
      >
        {reading ? 'Reading…' : 'Add a resume file'}
      </button>
      <span id={hint} className="sr-only">
        {ACCEPT_HINT}
      </span>
      <input
        ref={input}
        type="file"
        accept={ACCEPT}
        hidden
        tabIndex={-1}
        aria-hidden="true"
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file) onPick(file);
          e.target.value = ''; // the same file can be picked again
        }}
      />
    </>
  );
}

/** Under the header: "Reading <filename>…" while a file is read, or the plain-words reason it was not added. */
export function UploadStrip({
  reading,
  filename,
  failure,
  onRetry,
  onDismiss,
}: {
  /** The name of the file being read, while one is. */
  reading?: string;
  filename?: string;
  failure?: UploadFailure;
  onRetry: () => void;
  onDismiss: () => void;
}) {
  if (reading) {
    return (
      <div className="rs-upnote" role="status">
        <p className="rs-small">
          Reading <span className="rs-strong">{reading}</span>…
        </p>
        <p className="rs-fine">Leafout reads every line and sets it beside what you already have. This takes a model call, so it can take a minute.</p>
      </div>
    );
  }
  return (
    <div className="rs-upnote" role="alert">
      {failure && (
        <>
          <p className="rs-small rs-danger">
            {filename ? <span className="rs-strong">{filename}: </span> : null}
            {failure.message}
          </p>
          <div className="rs-actions">
            {failure.retry && (
              <button type="button" className="btn btn-sm btn-primary rs-btn" onClick={onRetry}>
                Try again
              </button>
            )}
            <button type="button" className="btn btn-sm btn-quiet rs-btn" onClick={onDismiss}>
              Dismiss
            </button>
          </div>
        </>
      )}
    </div>
  );
}
