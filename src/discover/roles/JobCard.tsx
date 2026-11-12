/**
 * One role the search found, as a card (board "Main" of the Discover canvas), in the order a person needs it: the fit
 * in a word and the level in a word; the title (it opens the details) and who is hiring where; the pips and the count
 * line; the first requirement the evidence meets, in the posting's own words and the person's own; where it was found
 * and that the employer's board listed it; Apply for me with the employer's own link quietly under it (or, where Apply
 * for me is not offered, the employer's link and why); then Prepare, See how you fit and three small buttons: Save,
 * Compare, Not for me. Pressed on a card, Apply for me runs the full posting match first and shows its steps here.
 *
 * There is no score and no percentage anywhere on it: the fit is a word worked out in code from quotes that were
 * checked against the posting and the profile. A role the person said no to becomes a slim "Hidden" row with Undo, and
 * a role still being read is an outlined skeleton: no shimmer, nothing that moves on its own.
 */
import { motion, useReducedMotion } from 'motion/react';
import { useId } from 'react';
import { cssVars } from '../../lib/ui';
import type { AiJob } from '../../../shared/jobsearch';
import { EASE } from '../../lib/ui';
import { NO_REASON_LABEL } from '../jobsearch/avoid';
import type { NoReason } from '../jobsearch/avoid';
import { employerLine, freshnessLine, provenanceLine } from '../jobsearch/words';
import { IconBookmark, IconCompare } from './icons';
import { NotForMe } from './NotForMe';
import { ApplyFor, AskLine, FitLine, Pair } from './parts';
import type { ApplyCtl } from './parts';

/**
 * Everything the buttons on a role need, made once per role by the screen that owns them. The Apply for me fields
 * (`ApplyCtl`): `apply` (offer, signed out, checking, applied, own account or off; absent while auto-apply is off),
 * `applyProgress` while it is being checked, `onApplyForMe` and `onCancelApply`.
 */
export interface RoleCtl extends ApplyCtl {
  /** A finished search stored this role: Prepare, See how you fit and Share work. */
  runReady: boolean;
  /** Where Prepare goes (a real link, so it opens in a new tab too). */
  prepareHref?: string;
  saved: boolean;
  comparing: boolean;
  /** Three roles are chosen already and this is not one of them. */
  compareFull: boolean;
  matchBusy: boolean;
  /** Another posting is being read, so a second match would be refused. */
  matchLocked: boolean;
  /** A sentence about the last attempt ("A match compares a posting with your evidence…"). */
  notice?: string;
  /** The person's own note on this role, when it is saved in Your applications: it sticks to the card. */
  note?: string;
  onOpen: () => void;
  onPrepare: () => void;
  onSeeMatch: () => void;
  onToggleSave: () => void;
  onToggleCompare: () => void;
  onShare: () => void;
  onHide: (reason: NoReason) => void;
  /** Take back a "not for me". */
  onUnhide: () => void;
}

export interface JobCardProps {
  job: AiJob;
  now: number;
  ctl: RoleCtl;
  /** The short form shown while a search is still running (a phone): fit, title, pips, freshness and Save. */
  compact?: boolean;
  /** Open the "Why not this one?" popover on first paint (the dev gallery). */
  popoverOpen?: boolean;
  titleRef?: (el: HTMLButtonElement | null) => void;
}

const COMPARE_FULL = 'You can compare up to 3 roles';
const NEEDS_RUN = 'Available once the search has finished';

export function JobCard({ job, now, ctl, compact = false, popoverOpen = false, titleRef }: JobCardProps) {
  const reduced = useReducedMotion() ?? false;
  const titleId = useId();
  const pair = job.met[0];
  const gap = job.gaps[0];

  return (
    <li className="dc-cell">
      <motion.article
        className={`dc-card m-graph${compact ? ' is-compact' : ''}`}
        aria-labelledby={titleId}
        data-job={job.id}
        initial={reduced ? false : { opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.25, ease: EASE }}
      >
        <FitLine job={job} />

        <div className="dc-card-head">
          <div>
            <h3 className="dc-card-title">
              <button ref={titleRef} id={titleId} type="button" className="dc-title-btn" aria-haspopup="dialog" onClick={ctl.onOpen}>
                {job.title}
              </button>
            </h3>
            <p className="dc-card-sub">{employerLine(job)}</p>
          </div>
          {compact && <SaveButton ctl={ctl} large />}
        </div>

        <AskLine asks={job.asks} />

        {!compact && pair && <Pair met={pair} />}
        {!compact && !pair && gap && (
          <p className="dc-gapline">
            <span className="dc-gaplbl">Not on your resume:</span> “{gap}”
          </p>
        )}

        <p className="dc-prov">
          {!compact && (
            <>
              {provenanceLine(job)}
              <br />
            </>
          )}
          {freshnessLine(job, now)}
        </p>

        {!compact && ctl.note ? <NoteStick note={ctl.note} /> : null}

        {!compact && (
          <>
            <div className="dc-grow" />
            <ApplyFor
              job={job}
              ctl={ctl}
              size="sm"
              primaryClass="btn btn-primary btn-sm dc-btn-sm dc-apply"
              buttonClass="dc-btn-sm dc-apply"
              quietClass="btn btn-quiet btn-sm dc-btn-sm dc-apply"
            />
            <RoleActions job={job} ctl={ctl} popoverOpen={popoverOpen} />
          </>
        )}
      </motion.article>
    </li>
  );
}

/** The bookmark: pressed when the role is on Your applications. */
export function SaveButton({ ctl, large = false }: { ctl: Pick<RoleCtl, 'saved' | 'onToggleSave'>; large?: boolean }) {
  return (
    <button
      type="button"
      className={`dc-icon-btn${large ? ' is-large' : ''}`}
      aria-label="Save this role"
      aria-pressed={ctl.saved}
      title={ctl.saved ? 'Saved. Press to remove' : 'Save this role'}
      onClick={ctl.onToggleSave}
    >
      <IconBookmark filled={ctl.saved} />
    </button>
  );
}

/** A role the person said no to: a slim row, with the reason (when this visit knows it) and Undo. */
export function HiddenRow({ job, reason, onUndo }: { job: Pick<AiJob, 'title' | 'employer'>; reason?: NoReason; onUndo: () => void }) {
  return (
    <li className="dc-cell">
      <div className="panel dc-hidden">
        <div className="dc-hidden-text">
          <p>
            <span className="dc-strong">Hidden</span>
            {reason ? ` · ${NO_REASON_LABEL[reason]}` : ''}
          </p>
          <p className="dc-muted dc-small">
            {job.title} at {job.employer}
          </p>
        </div>
        <button type="button" className="btn btn-quiet btn-sm dc-btn-sm" onClick={onUndo}>
          Undo
          <span className="sr-only">{` hiding ${job.title} at ${job.employer}`}</span>
        </button>
      </div>
    </li>
  );
}

/** A role still being read: outlined boxes, no motion. `reading` says what is happening on the first one. */
export function SkeletonCard({ reading = false, compact = false, i = 0 }: { reading?: boolean; compact?: boolean; i?: number }) {
  // widths vary a little from one card to the next so they do not read as one block
  const title = ['62%', '74%', '56%', '68%'][i % 4];
  const sub = ['38%', '44%', '34%', '40%'][i % 4];
  const line = ['78%', '64%', '86%', '72%'][i % 4];
  return (
    <li className="dc-cell">
      <div className={`dc-skel-card${compact ? ' is-compact' : ''}`} {...(reading ? { role: 'group', 'aria-label': 'Another role is being read' } : { 'aria-hidden': true })}>
        <div className="dc-skel-row" aria-hidden={reading || undefined}>
          <span className="dc-sk dc-sk-chip" />
          <span className="dc-sk" style={{ width: 64, height: 12 }} />
        </div>
        <div className="dc-skel-col" aria-hidden={reading || undefined}>
          <span className="dc-sk dc-sk-title" style={{ width: title, height: 17 }} />
          <span className="dc-sk" style={{ width: sub, height: 13 }} />
        </div>
        <div className="dc-skel-row" aria-hidden={reading || undefined}>
          <span className="dc-pips">
            <span className="dc-pip dc-pip-empty" />
            <span className="dc-pip dc-pip-empty" />
            <span className="dc-pip dc-pip-empty" />
          </span>
          <span className="dc-sk" style={{ width: '44%', height: 12 }} />
        </div>
        {!compact &&
          (reading ? (
            <div className="dc-skel-read">
              <span className="dc-muted dc-small">Reading the posting against your evidence…</span>
              <span className="dc-sk" aria-hidden="true" style={{ width: '100%', height: 12 }} />
              <span className="dc-sk" aria-hidden="true" style={{ width: '72%', height: 12 }} />
            </div>
          ) : (
            <>
              <div className="dc-skel-col" aria-hidden="true">
                <span className="dc-sk" style={{ width: '100%', height: 12 }} />
                <span className="dc-sk" style={{ width: line, height: 12 }} />
              </div>
              <span className="dc-skel-btn" aria-hidden="true" />
            </>
          ))}
      </div>
    </li>
  );
}

/** Prepare, See how you fit, the three small buttons (Save, Compare, Not for me) and the note about the last attempt. */
export function RoleActions({ job, ctl, popoverOpen = false }: { job: AiJob; ctl: RoleCtl; popoverOpen?: boolean }) {
  return (
    <>
      <div className="dc-card-row">
        {ctl.runReady && ctl.prepareHref ? (
          <a
            className="btn btn-sm dc-btn-sm"
            href={ctl.prepareHref}
            onClick={(e) => {
              if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button !== 0) return;
              e.preventDefault();
              ctl.onPrepare();
            }}
          >
            Prepare
          </a>
        ) : (
          <button type="button" className="btn btn-sm dc-btn-sm" aria-disabled="true" title={NEEDS_RUN} onClick={ctl.onPrepare}>
            Prepare
          </button>
        )}
        <button
          type="button"
          className="btn btn-sm dc-btn-sm"
          aria-disabled={ctl.matchBusy || ctl.matchLocked || !ctl.runReady || undefined}
          title={ctl.runReady ? undefined : NEEDS_RUN}
          onClick={() => !ctl.matchBusy && ctl.onSeeMatch()}
        >
          {ctl.matchBusy ? 'Opening…' : 'See how you fit'}
        </button>
        <div className="dc-grow" />
        <SaveButton ctl={ctl} />
        <button
          type="button"
          className="dc-icon-btn"
          aria-label="Add to compare"
          aria-pressed={ctl.comparing}
          aria-disabled={(ctl.compareFull && !ctl.comparing) || undefined}
          title={ctl.compareFull && !ctl.comparing ? COMPARE_FULL : undefined}
          onClick={ctl.onToggleCompare}
        >
          <IconCompare />
        </button>
        <NotForMe job={job} variant="icon" defaultOpen={popoverOpen} onChoose={ctl.onHide} />
      </div>
      <p className="dc-notice" role="status">
        {ctl.notice}
      </p>
    </>
  );
}

/** The person's own note on a saved role, stuck to the thing it is about. */
export function NoteStick({ note }: { note: string }) {
  return (
    <div className="stick is-flow dc-stick">
      <div className="postit" role="note" style={cssVars({ '--pp': 'var(--paper-yellow)' })}>
        <p className="pen">{note}</p>
        <span className="pcite pcite-end">your note on this role</span>
      </div>
    </div>
  );
}

/**
 * The role that opens the results: the first one in the order the search computed (strong fits, then good fits, then
 * stretches). The fit is a field (where the person stands), what was measured is on graph paper (every ask the evidence
 * meets, with the evidence), and Apply for me is the one ink block on the page. Same controls as a card.
 */
export function RoleHero({ job, now, ctl, popoverOpen = false }: { job: AiJob; now: number; ctl: RoleCtl; popoverOpen?: boolean }) {
  const titleId = useId();
  const checking = ctl.apply?.kind === 'checking';
  return (
    <article className="dc-lead" aria-labelledby={titleId} data-job={job.id}>
      <div className="m-tile m-field dc-lead-main">
        <p className="kicker">{job.fit === 'strong' ? 'Strongest fit in this search' : 'Top of your list'}</p>
        <FitLine job={job} />
        <h2 className="serif dc-lead-title">
          <button id={titleId} type="button" className="dc-title-btn" aria-haspopup="dialog" onClick={ctl.onOpen}>
            {job.title}
          </button>
        </h2>
        <p className="dc-card-sub">{employerLine(job)}</p>
        <AskLine asks={job.asks} />
        {ctl.note ? <NoteStick note={ctl.note} /> : null}
        <div className="dc-grow" />
        <RoleActions job={job} ctl={ctl} popoverOpen={popoverOpen} />
      </div>
      <div className="m-tile m-graph dc-lead-facts">
        <p className="kicker">What the posting asks, and what you show</p>
        {job.met.map((m, i) => (
          <Pair key={i} met={m} />
        ))}
        {job.gaps.map((g, i) => (
          <p key={i} className="dc-gapline">
            <span className="dc-gaplbl">Not on your resume:</span> “{g}”
          </p>
        ))}
        <p className="dc-prov">
          {provenanceLine(job)}
          <br />
          {freshnessLine(job, now)}
        </p>
      </div>
      {!ctl.applyClosed && (
        <div className={`m-tile ${checking ? 'm-plain' : 'm-ink'} dc-lead-apply`}>
          <ApplyFor job={job} ctl={ctl} size="sm" primaryClass="btn btn-primary btn-sm dc-btn-sm dc-apply" buttonClass="dc-btn-sm dc-apply" quietClass="btn btn-quiet btn-sm dc-btn-sm dc-apply" />
        </div>
      )}
    </article>
  );
}
