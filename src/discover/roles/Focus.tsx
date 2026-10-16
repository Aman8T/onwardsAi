/**
 * One role in full (board "Focus" of the Discover canvas): the fit and the level; the title, the employer and the
 * place; what the posting says about level; the pips; every requirement the evidence meets, in the posting's words
 * and the person's own; the requirements that are not on the resume; how the role was found; and, in a framed box at
 * the end, what to do next: apply, prepare, see how you fit, save, then compare, share and "not for me".
 *
 * On wide screens it is a panel beside the grid (the grid dims behind it); under 760 px it is a bottom sheet with the
 * actions pinned under the scrolling details. Escape closes either, and focus goes back to the title that opened it.
 * Nothing here is a score: counts, quotes, and words for the fit and the level. A salary shows only when the employer
 * states one.
 */
import { useEffect, useId, useRef } from 'react';
import type { ReactNode } from 'react';
import type { AiJob } from '../../../shared/jobsearch';
import { LEVEL_WORD, freshnessLine, placeLine, provenanceLine, salaryLine } from '../jobsearch/words';
import { useLayout } from '../layout';
import { IconBookmark, IconChevron, IconClose, IconPrepare, IconSearch } from './icons';
import { NotForMe } from './NotForMe';
import { ApplyLink, AskLine, FitChip } from './parts';
import type { RoleCtl } from './JobCard';
import { Sheet } from './Sheet';

const NEEDS_RUN = 'Available once the search has finished';
const COMPARE_FULL = 'You can compare up to 3 roles';

export interface FocusProps {
  job: AiJob;
  now: number;
  ctl: RoleCtl;
  onClose: () => void;
}

export function Focus({ job, now, ctl, onClose }: FocusProps) {
  const { narrow } = useLayout();
  if (narrow) {
    return (
      <Sheet open onClose={onClose} label={`${job.title} at ${job.employer}, details`} className="dc-sheet-detail" footer={<SheetActions job={job} ctl={ctl} />}>
        <Body job={job} now={now} ctl={ctl} onClose={onClose} narrow />
      </Sheet>
    );
  }
  return <Panel job={job} now={now} ctl={ctl} onClose={onClose} />;
}

/** The side panel: a dialog beside the grid, not a trap, so the person can still reach the header. */
function Panel({ job, now, ctl, onClose }: FocusProps) {
  const root = useRef<HTMLElement>(null);
  const jobId = job.id;

  // focus moves in when the panel opens and goes back to the role's title when it closes
  useEffect(() => {
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    root.current?.focus({ preventScroll: true });
    return () => {
      const back = opener && document.contains(opener) ? opener : document.querySelector<HTMLElement>(`[data-job="${CSS.escape(jobId)}"] .dc-title-btn`);
      back?.focus({ preventScroll: true });
    };
  }, [jobId]);

  return (
    <aside
      ref={root}
      className="dc-focus"
      role="dialog"
      aria-label={`${job.title} at ${job.employer}, details`}
      tabIndex={-1}
      onKeyDown={(e) => {
        if (e.key === 'Escape' && !e.defaultPrevented) {
          e.stopPropagation();
          onClose();
        }
      }}
    >
      <Body job={job} now={now} ctl={ctl} onClose={onClose} />
      <PanelActions job={job} ctl={ctl} />
    </aside>
  );
}

/* ───────────────────────── the details ───────────────────────── */

function Section({ title, children }: { title: string; children: ReactNode }) {
  const id = useId();
  return (
    <section className="dc-detail-sec" aria-labelledby={id}>
      <h3 id={id} className="kicker">
        {title}
      </h3>
      {children}
    </section>
  );
}

function Body({ job, now, ctl, onClose, narrow = false }: FocusProps & { narrow?: boolean }) {
  const sal = salaryLine(job);
  return (
    <div className="dc-detail">
      <div className="dc-detail-top">
        <div className="dc-fitline">
          <FitChip fit={job.fit} />
          <span className="dc-lvl">{LEVEL_WORD[job.level]}</span>
        </div>
        <button type="button" className="dc-icon-btn is-large" aria-label="Close role details" onClick={onClose}>
          <IconClose />
        </button>
      </div>

      <div className="dc-detail-head">
        <h2 className="serif dc-detail-title">{job.title}</h2>
        {narrow ? (
          <p className="dc-detail-emp">
            <span className="dc-strong">{job.employer}</span> <span className="dc-muted">· {placeLine(job)}</span>
          </p>
        ) : (
          <>
            <p className="dc-detail-emp">{job.employer}</p>
            <p className="dc-muted">{placeLine(job)}</p>
          </>
        )}
      </div>

      <div className="dc-detail-level">
        {job.levelQuote && (
          <p className="dc-muted">
            The posting says: <span className="dc-ink">“{job.levelQuote}”</span>
          </p>
        )}
        <AskLine asks={job.asks} />
      </div>

      {job.met.length > 0 && (
        <Section title="What it asks, and what you show">
          {job.met.map((m, i) => (
            <div key={i} className="dc-ask-item">
              <span className={`dc-pip dc-pip-${m.partly ? 'part' : 'met'}`} aria-hidden="true" />
              <div className="dc-ask-grid">
                <span className="dc-lbl">Asks:</span>
                <span>“{m.ask}”</span>
                <span className="dc-lbl">You:</span>
                <span>
                  “{m.cite.text}”
                  {m.cite.source && <span className="dc-cite dc-cite-block">{m.cite.source}</span>}
                  {m.partly && <span className="dc-partly dc-cite-block">Meets part of it</span>}
                </span>
              </div>
            </div>
          ))}
        </Section>
      )}

      {job.gaps.length > 0 && (
        <Section title="Not on your resume">
          {job.gaps.map((g, i) => (
            <div key={i} className="dc-ask-item">
              <span className="dc-pip dc-pip-gap" aria-hidden="true" />
              <p className="dc-gaptext">“{g}”</p>
            </div>
          ))}
          <p className="dc-muted dc-small dc-detail-hint">Prepare for this role shows what you can show now, and a small step before you apply.</p>
        </Section>
      )}

      <Section title="How we found it">
        <p>{provenanceLine(job)}</p>
        <p className="dc-muted">{freshnessLine(job, now)}</p>
        {sal && <p className="dc-muted">{sal}</p>}
      </Section>
    </div>
  );
}

/* ───────────────────────── what to do next ───────────────────────── */

/** The three big choices of the panel, and the apply link above them. */
function PanelActions({ job, ctl }: { job: AiJob; ctl: RoleCtl }) {
  return (
    <section className="dc-next" aria-label="What to do next">
      <div className="kicker">What next</div>
      <ApplyLink job={job} className="btn btn-primary dc-btn dc-apply" />
      <div className="dc-choices">
        {ctl.runReady && ctl.prepareHref ? (
          <a
            className="btn dc-choice"
            href={ctl.prepareHref}
            onClick={(e) => {
              if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button !== 0) return;
              e.preventDefault();
              ctl.onPrepare();
            }}
          >
            <Choice icon={<IconPrepare />} title="Prepare for this role" desc="Gaps to close, likely questions, your stories" />
            <IconChevron />
          </a>
        ) : (
          <button type="button" className="btn dc-choice" aria-disabled="true" title={NEEDS_RUN} onClick={ctl.onPrepare}>
            <Choice icon={<IconPrepare />} title="Prepare for this role" desc="Gaps to close, likely questions, your stories" />
            <IconChevron />
          </button>
        )}
        <button
          type="button"
          className="btn dc-choice"
          aria-disabled={ctl.matchBusy || ctl.matchLocked || !ctl.runReady || undefined}
          title={ctl.runReady ? undefined : NEEDS_RUN}
          onClick={() => !ctl.matchBusy && ctl.onSeeMatch()}
        >
          <Choice icon={<IconSearch />} title={ctl.matchBusy ? 'Opening…' : 'See how you fit'} desc="Every requirement against your evidence, then a tailored resume and cover note" />
          <IconChevron />
        </button>
        <button type="button" className={`btn dc-choice${ctl.saved ? ' is-on' : ''}`} aria-pressed={ctl.saved} onClick={ctl.onToggleSave}>
          <Choice
            icon={<IconBookmark filled={ctl.saved} />}
            title={ctl.saved ? 'Saved · in Your applications' : 'Save'}
            desc={ctl.saved ? 'Change its stage or add a note there' : 'Add it to Your applications, then move it along as you go'}
          />
          <span aria-hidden="true" className="dc-choice-pad" />
        </button>
      </div>
      <p className="dc-notice" role="status">
        {ctl.notice}
      </p>
      <div className="dc-next-small">
        <button
          type="button"
          className="btn btn-sm dc-btn-sm"
          aria-pressed={ctl.comparing}
          aria-disabled={(ctl.compareFull && !ctl.comparing) || undefined}
          title={ctl.compareFull && !ctl.comparing ? COMPARE_FULL : 'Put up to 3 roles side by side'}
          onClick={ctl.onToggleCompare}
        >
          Compare
        </button>
        <button type="button" className="btn btn-sm dc-btn-sm" aria-disabled={!ctl.runReady || undefined} title={ctl.runReady ? 'Share the employer’s posting link with your note' : NEEDS_RUN} onClick={ctl.onShare}>
          Share
        </button>
        <NotForMe job={job} variant="text" align="left" onChoose={ctl.onHide} />
      </div>
    </section>
  );
}

function Choice({ icon, title, desc }: { icon: ReactNode; title: string; desc: string }) {
  return (
    <>
      <span className="dc-choice-icon">{icon}</span>
      <span className="dc-choice-text">
        <span className="dc-choice-title">{title}</span>
        <span className="dc-choice-desc">{desc}</span>
      </span>
    </>
  );
}

/** The sheet's pinned actions: full-width 44 px buttons, then Save · Compare · Share · Not for me. */
function SheetActions({ job, ctl }: { job: AiJob; ctl: RoleCtl }) {
  return (
    <>
      <ApplyLink job={job} className="btn btn-primary dc-btn dc-apply" />
      {ctl.runReady && ctl.prepareHref ? (
        <a
          className="btn dc-btn dc-apply"
          href={ctl.prepareHref}
          onClick={(e) => {
            if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button !== 0) return;
            e.preventDefault();
            ctl.onPrepare();
          }}
        >
          Prepare for this role
        </a>
      ) : (
        <button type="button" className="btn dc-btn dc-apply" aria-disabled="true" title={NEEDS_RUN} onClick={ctl.onPrepare}>
          Prepare for this role
        </button>
      )}
      <button
        type="button"
        className="btn dc-btn dc-apply"
        aria-disabled={ctl.matchBusy || ctl.matchLocked || !ctl.runReady || undefined}
        title={ctl.runReady ? undefined : NEEDS_RUN}
        onClick={() => !ctl.matchBusy && ctl.onSeeMatch()}
      >
        {ctl.matchBusy ? 'Opening…' : 'See how you fit'}
      </button>
      <p className="dc-notice" role="status">
        {ctl.notice}
      </p>
      <div className="dc-sheet-four">
        <button type="button" className="btn dc-btn dc-btn-fit" aria-pressed={ctl.saved} onClick={ctl.onToggleSave}>
          {ctl.saved ? 'Saved' : 'Save'}
        </button>
        <button
          type="button"
          className="btn dc-btn dc-btn-fit"
          aria-pressed={ctl.comparing}
          aria-disabled={(ctl.compareFull && !ctl.comparing) || undefined}
          title={ctl.compareFull && !ctl.comparing ? COMPARE_FULL : undefined}
          onClick={ctl.onToggleCompare}
        >
          Compare
        </button>
        <button type="button" className="btn dc-btn dc-btn-fit" aria-disabled={!ctl.runReady || undefined} title={ctl.runReady ? undefined : NEEDS_RUN} onClick={ctl.onShare}>
          Share
        </button>
        <NotForMe job={job} variant="wide" align="right" onChoose={ctl.onHide} />
      </div>
    </>
  );
}

