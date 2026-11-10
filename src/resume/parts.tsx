/**
 * Small parts the resume screens share: a link to a screen of the page, the stamp, a source line, a state panel and
 * the two download links. Everything that comes from data (a line, a quote, a title) is a text node.
 */
import type { MouseEvent, ReactNode } from 'react';
import { resumePath } from './route';
import type { ResumeQuery } from './route';
import type { Source } from './model';
import type { Busy, FileKind } from './types';

/** A link to a screen of the page: a real anchor (new tab, copy link) that a plain click turns into in-app navigation. */
export function ScreenLink({
  to,
  onNavigate,
  className,
  current,
  children,
}: {
  to: ResumeQuery;
  onNavigate: (to: ResumeQuery) => void;
  className?: string;
  /** This link names what is on screen now. */
  current?: boolean;
  children: ReactNode;
}) {
  const click = (e: MouseEvent<HTMLAnchorElement>) => {
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    e.preventDefault();
    onNavigate(to);
  };
  return (
    <a href={resumePath(to)} className={className} aria-current={current ? 'page' : undefined} onClick={click}>
      {children}
    </a>
  );
}

/** A small turned mark: `default`, `done`. */
export function Stamp({ children }: { children: ReactNode }) {
  return <span className="rs-stamp">{children}</span>;
}

/** "your resume · line 14" then the words of the source. */
export function SourceLine({ source, quote = true }: { source: Source; quote?: boolean }) {
  return (
    <span className="rs-source">
      <span className="rs-cite">{source.label}</span>
      {quote && source.quote && <q className="rs-quote">{source.quote}</q>}
    </span>
  );
}

/** Loading, empty, error and signed-out states: a title, one sentence, and the next step. */
export function StatePanel({ title, children, action, busy = false }: { title: string; children?: ReactNode; action?: ReactNode; busy?: boolean }) {
  return (
    <section className="panel rs-state" role="status" aria-busy={busy}>
      <h1 className="title rs-state-title" data-rs-h1 tabIndex={-1}>
        {title}
      </h1>
      {children && <p className="rs-lede">{children}</p>}
      {action && <div className="rs-actions">{action}</div>}
    </section>
  );
}

const FILE_LABEL: Record<FileKind, string> = { pdf: 'PDF', docx: 'Word' };

/**
 * Download PDF and Download Word for a finalised resume. Plain links to the files, so a middle-click or a long-press
 * works; a normal click goes through `onDownload`, which can say on the page why a file did not come.
 */
export function FileLinks({
  hrefFor,
  onDownload,
  busy,
  quiet = false,
}: {
  hrefFor: (file: FileKind) => string;
  onDownload: (file: FileKind) => void;
  busy: Busy | null;
  /** Small text links instead of buttons (the older finalised version of a draft). */
  quiet?: boolean;
}) {
  return (
    <>
      {(['pdf', 'docx'] as const).map((file) => {
        const fetching = busy?.kind === 'download' && busy.file === file;
        return (
          <a
            key={file}
            href={hrefFor(file)}
            download
            className={quiet ? 'rs-link' : `btn btn-sm rs-btn${file === 'pdf' ? ' btn-primary' : ''}`}
            aria-busy={fetching}
            onClick={(e) => {
              if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
              e.preventDefault();
              if (!busy) onDownload(file);
            }}
          >
            {fetching ? 'Preparing…' : quiet ? FILE_LABEL[file] : `Download ${FILE_LABEL[file]}`}
          </a>
        );
      })}
    </>
  );
}
