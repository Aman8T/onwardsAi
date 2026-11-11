/**
 * The resumes screen: the rail of resumes, the one in view as lines with the pressed block's panel, the blocks it
 * leaves out, and the coach beside it. Presentational: the store-connected root and the dev gallery both drive it.
 *
 * Under 760 px the three columns are one stack and the pressed block's panel sits directly under its line, so it is
 * never a scroll away from the thing it is about.
 */
import { useMemo, useState } from 'react';
import { NARROW_QUERY, useMediaQuery } from '../lib/ui';
import { BlockPanel } from './BlockPanel';
import { CoachPanel } from './CoachPanel';
import { Lines } from './Lines';
import { bulletOfFact, factIndex, kickerOf, sheetLines, shortDate, slotOf, sourceOf, stateLine } from './model';
import { FileLinks, StatePanel } from './parts';
import { Rail } from './Rail';
import type { ResumeQuery } from './route';
import type { Busy, Failure, FileKind, LineWording, LoadStatus, ResumeDetail, ResumeList, ResumeOp } from './types';

export interface ResumePageProps {
  list: ResumeList;
  /** The resume in view, once it has loaded. */
  detail: ResumeDetail | null;
  detailStatus: LoadStatus;
  detailError?: Failure;
  viewId?: string;
  busy: Busy | null;
  /** A sentence about the last action that failed. */
  notice?: string;
  /** The bullet pressed when the screen opens (the gallery uses it). */
  initialSelected?: string;
  /** Wordings other resumes use for lines of this one. */
  wordings?: readonly LineWording[];
  /** Open the match a posting resume was saved from. */
  onOpenMatch: (matchId: string) => void;
  onNavigate: (to: ResumeQuery) => void;
  onMake: (roleId: string) => void;
  onOp: (op: ResumeOp) => Promise<ResumeDetail | null>;
  /** Open Finalise for the resume in view; `true` when the person asked to make it the default with it. */
  onFinalise: (makeDefault: boolean) => void;
  onMakeDefault: () => void;
  onDownload: (file: FileKind) => void;
  hrefFor: (file: FileKind) => string;
  /** Save the file the person uploaded, as it was (an uploaded resume only). */
  onDownloadOriginal?: () => void;
  /** The original of this resume was not kept: the button is not offered for the rest of the visit. */
  originalMissing?: boolean;
  onAsk: (question: string) => void;
  onRetry: () => void;
}

export function ResumePage(props: ResumePageProps) {
  const { list, detail, detailStatus, detailError, viewId, busy, onNavigate, onMake, onAsk, onRetry } = props;
  const inView = detail && (!viewId || detail.resume.id === viewId) ? detail : null;
  const firstToMake = list.paths.find((p) => !p.resumeId);

  return (
    <div className="rs-cols">
      <Rail list={list} viewId={viewId} busy={busy} onNavigate={onNavigate} onMake={onMake} onOpenMatch={props.onOpenMatch} />

      <div className="rs-doc">
        {inView ? (
          <InView key={inView.resume.id} {...props} detail={inView} />
        ) : detailStatus === 'error' ? (
          <StatePanel
            title="That resume didn't open"
            action={
              <button type="button" className="btn btn-primary rs-btn" onClick={onRetry}>
                Try again
              </button>
            }
          >
            {detailError?.message}
          </StatePanel>
        ) : !viewId ? (
          <StatePanel
            title="No resume here yet"
            hand="Start with the role you want most."
            action={
              firstToMake && (
                <button type="button" className="btn btn-primary rs-btn" disabled={busy !== null} onClick={() => onMake(firstToMake.roleId)}>
                  {`Make a resume for ${firstToMake.title}`}
                </button>
              )
            }
          >
            {list.paths.length > 0
              ? 'Pick one of your next roles in the list and press "Make it": Leafout arranges your blocks for that role, each in the words of its source.'
              : 'Your blocks come from the resume and the sources you gave. Analyse a resume first, and it shows here as lines.'}
          </StatePanel>
        ) : (
          <StatePanel title="Opening your resume…" busy />
        )}
        {!inView && props.notice && (
          <p className="rs-notice" role="alert">
            {props.notice}
          </p>
        )}
      </div>

      <CoachPanel resume={inView?.resume} onAsk={onAsk} />
    </div>
  );
}

function InView({ list, detail, busy, notice, initialSelected, wordings, onOp, onFinalise, onMakeDefault, onDownload, hrefFor, onDownloadOriginal, originalMissing }: ResumePageProps & { detail: ResumeDetail }) {
  const narrow = useMediaQuery(NARROW_QUERY);
  const [picked, setPicked] = useState<string | null>(initialSelected ?? null);
  const { resume, variant, marks, facts, leftOut } = detail;

  const lines = useMemo(() => sheetLines(variant, marks), [variant, marks]);
  const byId = useMemo(() => factIndex(facts), [facts]);
  // a pressed line that is no longer in the resume (it was left out) is no longer pressed
  const line = picked ? lines.find((l) => l.bullet?.id === picked) : undefined;
  const selectedId = line?.bullet?.id ?? null;

  const draft = resume.state === 'draft';
  const locked = busy !== null;
  const marked = marks.length > 0;

  const putBack = (factId: string) => {
    void onOp({ op: 'put_back', factId }).then((next) => {
      const bullet = next ? bulletOfFact(next.variant, factId) : undefined;
      if (bullet) setPicked(bullet.id);
    });
  };

  const panel = <BlockPanel line={line} facts={byId} state={resume.state} busy={busy} onOp={onOp} inline={narrow} wordings={wordings} />;

  return (
    <section className="rs-inview" aria-labelledby="rs-title">
      <header className="rs-head">
        <div className="rs-head-text">
          <p className="kicker">{kickerOf(resume, slotOf(list, resume))}</p>
          <h1 id="rs-title" className="serif rs-title" data-rs-h1 tabIndex={-1}>
            {resume.title}
          </h1>
          <p className="rs-fine">{stateLine(detail)}</p>
        </div>
        <div className="rs-head-acts">
          <div className="rs-actions rs-actions-end" role="group" aria-label="What you can do with this resume">
            {draft ? (
              <>
                <button type="button" className="btn btn-sm btn-primary rs-btn" disabled={locked} onClick={() => onFinalise(false)}>
                  Finalise
                </button>
                {!resume.isDefault && (
                  <button type="button" className="btn btn-sm rs-btn" disabled={locked} onClick={() => onFinalise(true)}>
                    Make default
                  </button>
                )}
              </>
            ) : (
              <>
                <FileLinks hrefFor={hrefFor} onDownload={onDownload} busy={busy} />
                {!resume.isDefault && (
                  <button type="button" className="btn btn-sm rs-btn" disabled={locked} aria-busy={busy?.kind === 'default'} onClick={onMakeDefault}>
                    {busy?.kind === 'default' ? 'Making it your default…' : 'Make default'}
                  </button>
                )}
              </>
            )}
            {resume.kind === 'upload' && onDownloadOriginal && !originalMissing && (
              <button type="button" className="btn btn-sm rs-btn" disabled={locked} aria-busy={busy?.kind === 'original'} onClick={onDownloadOriginal}>
                {busy?.kind === 'original' ? 'Preparing…' : 'Download your file'}
              </button>
            )}
          </div>
          {resume.kind === 'upload' && originalMissing && (
            <p className="rs-fine" role="status">
              The original of this file was not kept. Files added from today are.
            </p>
          )}
          <p className="rs-fine">
            {draft
              ? resume.isDefault
                ? 'A draft is not downloaded. Finalise runs the checks first.'
                : 'A draft is not downloaded. Finalise and Make default both run the checks first.'
              : 'These are the finalised files, read back and checked.'}
          </p>
          {draft && detail.canDownload && (
            <p className="rs-fine">
              The version you finalised{resume.finalisedAt ? ` on ${shortDate(resume.finalisedAt)}` : ''} is still the one that downloads: <FileLinks hrefFor={hrefFor} onDownload={onDownload} busy={busy} quiet />
            </p>
          )}
        </div>
      </header>

      <p className="rs-notice" role="alert">
        {notice}
      </p>

      {!narrow && panel}

      <div className="rs-sheet-wrap">
        <p className="rs-fine rs-key">
          <span className="rs-key-box" aria-hidden="true" />
          <span>
            {marked ? 'A marked line differs from your uploaded resume. ' : ''}
            Press a line to work on it.
          </span>
        </p>
        <Lines lines={lines} selectedId={selectedId} onSelect={setPicked} inline={narrow && line ? panel : undefined} />
      </div>

      <section className="rs-out" aria-labelledby="rs-out-h">
        <h2 id="rs-out-h" className="kicker">
          In your blocks, left out of this resume · <span className="numeral">{leftOut.length}</span>
        </h2>
        {leftOut.length > 0 ? (
          <ul className={`panel rs-rows rs-rows-in rs-leftout stack${leftOut.length === 1 ? ' is-single' : ''}`}>
            {leftOut.map((fact) => {
              const source = sourceOf(fact);
              const working = busy?.kind === 'op' && busy.target === fact.id;
              return (
                <li key={fact.id} className="rs-row">
                  <span className="rs-row-main">
                    <span className="rs-small">{fact.text}</span>
                    {source && <span className="rs-cite">{source.label}</span>}
                  </span>
                  <button type="button" className="btn btn-sm rs-btn" disabled={locked} aria-busy={working} onClick={() => putBack(fact.id)}>
                    {working ? 'Putting it back…' : 'Put back'}
                  </button>
                </li>
              );
            })}
          </ul>
        ) : (
          <p className="rs-fine">Nothing is left out: every bullet you have is in this resume.</p>
        )}
        {leftOut.length > 0 && <p className="rs-fine">Left out is not lost. These stay in your blocks and can go into any resume.</p>}
      </section>
    </section>
  );
}
