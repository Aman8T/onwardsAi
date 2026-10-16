/**
 * One role the search found, in the order a person needs it: the role and who is hiring; where it is and how it is
 * worked; the fit in a word and the level in a word (with the posting's own words about it); up to three things the
 * posting asks for next to the line of the person's own evidence that meets each; what the posting asks that the
 * resume does not show; where the role was found and that the employer's board listed it today; the salary only if
 * the employer states it; then the actions, with the apply link naming where it goes.
 *
 * There is no score and no percentage anywhere on it: the fit is a word worked out in code from quotes that were
 * checked against the posting and the profile. Every string a posting or a scout wrote is a text node, and the apply
 * link renders only when it is a plain web address. The look is Discover's own: the fit chip and the mono citation
 * line come from the listing row and card.
 */
import { motion, useReducedMotion } from 'motion/react';
import { useId } from 'react';
import type { AiJob } from '../../../shared/jobsearch';
import { EASE } from '../../lib/ui';
import { applyLabel, safeHref } from '../model';
import { FIT_LOOK, FIT_WORD, LEVEL_WORD, jobPlaceLine, listedLine, postedLine } from './words';

export interface JobCardProps {
  job: AiJob;
  now: number;
  /** Absent: the card has no "See full match" (a screen that cannot start a match). */
  onSeeMatch?: () => void;
  /** This card's match is being started. */
  matchBusy: boolean;
  /** Another match is already being read; starting a second would be refused, so the button reads as unavailable. */
  matchLocked: boolean;
  /** A sentence about the last attempt ("A match compares a posting with your evidence…"). */
  notice?: string;
}

export function JobCard({ job, now, onSeeMatch, matchBusy, matchLocked, notice }: JobCardProps) {
  const reduced = useReducedMotion() ?? false;
  const titleId = useId();
  const href = safeHref(job.applyUrl);
  const when = [listedLine(job.checkedAt, now), postedLine(job.postedAt, now)].filter(Boolean).join(' · ');

  return (
    <li className="dc-js-item">
      <motion.article
        className="panel dc-js-job"
        aria-labelledby={titleId}
        initial={reduced ? false : { opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.25, ease: EASE }}
      >
        <div className="dc-js-job-head">
          <h3 id={titleId} className="dc-js-job-title">
            {job.title}
            <span className="dc-emp"> · {job.employer}</span>
          </h3>
          <p className="dc-row-sub">{jobPlaceLine(job)}</p>
        </div>

        <div className="dc-row-fit">
          <span className={`dc-fit dc-fit-${FIT_LOOK[job.fit]}`} data-word={job.fit}>
            {FIT_WORD[job.fit]}
          </span>
          <span className="dc-count">{LEVEL_WORD[job.level]}</span>
        </div>
        {job.levelQuote && <p className="dc-js-line dc-js-levelquote">The posting says: “{job.levelQuote}”</p>}

        {job.met.length > 0 && (
          <ul className="dc-js-met" aria-label="What the posting asks, and what you show">
            {job.met.map((m, i) => (
              <li key={i}>
                <p className="dc-js-line">
                  <span className="dc-js-lbl">Asks:</span> “{m.ask}”
                </p>
                <p className="dc-js-line">
                  <span className="dc-js-lbl">You:</span> “{m.cite.text}”
                  {m.cite.source && <span className="dc-cite dc-js-src">{m.cite.source}</span>}
                  {m.partly && <span className="chip dc-js-partly">Partly met</span>}
                </p>
              </li>
            ))}
          </ul>
        )}

        {job.gaps.length > 0 && (
          <ul className="dc-js-gaps" aria-label="Asked by the posting and not on your resume">
            {job.gaps.map((g, i) => (
              <li key={i} className="dc-js-line">
                <span className="dc-js-gaplbl">Not on your resume:</span> “{g}”
              </li>
            ))}
          </ul>
        )}

        {job.salary && <p className="dc-faint dc-small">{job.salary.text} · stated by employer</p>}

        <div className="dc-js-prov dc-faint dc-small">
          <p>
            {job.foundVia === 'web' ? (
              <>
                Found on the web
                {job.foundBy && (
                  <>
                    {' · Search: '}
                    <span className="dc-js-by">{job.foundBy}</span>
                  </>
                )}
              </>
            ) : (
              "From Tayyari's employers"
            )}
          </p>
          <p>{when}</p>
        </div>

        <div className="dc-js-actions">
          {href ? (
            <a
              className="btn btn-primary btn-sm dc-btn-sm"
              href={href}
              target="_blank"
              rel="noopener noreferrer"
              aria-label={`Apply on ${job.applyHost} for ${job.title} at ${job.employer}, opens in a new tab`}
            >
              {applyLabel(job)}
            </a>
          ) : (
            <span className="dc-faint">{`Apply on ${job.applyHost}`}</span>
          )}
          {onSeeMatch && (
            // pressed while another posting is being read, it says so in this card's notice (the panel decides)
            <button type="button" className="btn btn-sm dc-btn-sm" aria-disabled={matchBusy || matchLocked || undefined} onClick={() => !matchBusy && onSeeMatch()}>
              {matchBusy ? 'Opening…' : 'See full match'}
            </button>
          )}
        </div>
        <p className="dc-notice" role="status">
          {notice}
        </p>
      </motion.article>
    </li>
  );
}
