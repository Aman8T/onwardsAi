/**
 * Compare two or three roles side by side (board 9 of the "Discover with AI" canvas).
 *
 * One column per role and one row per thing worth comparing: the fit word, the level and the posting's own words about
 * it, the place, the pips with their count line, the asks met, what is not on the resume, how the role was found and how
 * fresh it is, the salary, and what to do next. Nothing is scored and nothing is ranked: the columns stay in the order the
 * person picked them, and every quote is the posting's or the profile's own words.
 *
 * The first column sticks while the table scrolls sideways (it is a focusable region, so a keyboard can scroll it). With
 * fewer than two roles there is nothing to put side by side, and the screen says so.
 */
import { useEffect, useRef } from 'react';
import type { ReactNode } from 'react';
import type { AiJob } from '../../../shared/jobsearch';
import { AskPips, PipsKey } from '../prepare/Pips';
import { quoted } from '../prepare/parts';
import { FIT_CLASS, FIT_WORD, LEVEL_WORD, applyHref, applyText, asksTotal, foundLine, freshLines, placeLine, salaryLine } from '../prepare/words';
import { cssVars } from '../../lib/ui';
import '../../styles/discover-more.css';

export interface CompareViewProps {
  jobs: readonly AiJob[];
  onRemove: (id: string) => void;
  onClear: () => void;
  onPrepare: (job: AiJob) => void;
  onSeeMatch: (job: AiJob) => void;
  onBack: () => void;
}

/** Most roles that sit side by side: more would not fit a screen. */
const MAX_COLUMNS = 3;

const COUNT_WORD = ['No', 'One', 'Two', 'Three'];

const label = (job: Pick<AiJob, 'title' | 'employer'>) => `${job.title} at ${job.employer}`;

export function CompareView({ jobs: all, onRemove, onClear, onPrepare, onSeeMatch, onBack }: CompareViewProps) {
  const jobs = all.slice(0, MAX_COLUMNS);
  const countRef = useRef<HTMLSpanElement>(null);
  const titleRef = useRef<HTMLHeadingElement>(null);
  const seen = useRef(jobs.length);

  // a removed column takes the button that was pressed with it: focus goes to the count, which says what is left
  // (or to the title, when too few are left to compare)
  useEffect(() => {
    if (jobs.length < seen.current) (countRef.current ?? titleRef.current)?.focus();
    seen.current = jobs.length;
  }, [jobs.length]);

  if (jobs.length < 2) {
    return (
      <div className="dm-page dm-compare">
        <header className="dm-head">
          <button type="button" className="dm-back" onClick={onBack}>
            <span aria-hidden="true">←</span> Discover
          </button>
          <p className="kicker">Compare</p>
          <h1 className="serif dm-title" ref={titleRef} tabIndex={-1}>
            Pick two or three roles to compare
          </h1>
          <p className="dm-muted dm-lede">
            {jobs.length === 1 ? `${label(jobs[0])} is picked. Pick one or two more from the list, then come back.` : 'Press Compare on a role in the list. Two or three side by side is the most that reads well.'}
          </p>
          <div className="dm-actions">
            <button type="button" className="btn btn-primary dm-btn" onClick={onBack}>
              Back to roles
            </button>
            {jobs.length === 1 && (
              <button type="button" className="btn btn-quiet dm-btn" onClick={onClear}>
                Clear
              </button>
            )}
          </div>
        </header>
      </div>
    );
  }

  return (
    <div className="dm-page dm-compare">
      <header className="dm-head">
        <button type="button" className="dm-back" onClick={onBack}>
          <span aria-hidden="true">←</span> Discover
        </button>
        <p className="kicker">Compare</p>
        <h1 className="serif dm-title">{COUNT_WORD[jobs.length]} roles, side by side</h1>
        <p className="dm-muted dm-lede">Quotes are the posting's own words, or your profile's. Nothing here is scored.</p>
      </header>

      <div className="dm-toolbar" role="toolbar" aria-label="Comparison">
        <div className="dm-toolbar-count">
          <span ref={countRef} tabIndex={-1} className="dm-strong" aria-live="polite">
            Comparing {jobs.length} roles
          </span>
          <span className="dm-muted" aria-hidden="true">
            ·
          </span>
          <button type="button" className="btn btn-quiet btn-sm dm-btn-sm" onClick={onClear}>
            Clear
          </button>
        </div>
        <PipsKey />
        <p className="dm-muted dm-small">Up to {MAX_COLUMNS} roles. Go back to the list to add or swap one.</p>
      </div>

      <div className="panel dm-tablewrap" role="region" aria-label={`Comparison of ${jobs.length} roles. Scrolls sideways on a small screen.`} tabIndex={0}>
        <table className="dm-cmp" style={cssVars({ '--dm-cols': jobs.length })}>
          <caption className="sr-only">{jobs.map(label).join(', ')}, compared row by row</caption>
          <thead>
            <tr>
              <th scope="col">
                <span className="sr-only">What is compared</span>
              </th>
              {jobs.map((job) => (
                <th scope="col" key={job.id}>
                  <div className="dm-colhead">
                    <div>
                      <p className="dm-coltitle">{job.title}</p>
                      <p className="dm-muted">{job.employer}</p>
                    </div>
                    <button type="button" className="dm-iconbtn" aria-label={`Remove ${label(job)} from the comparison`} onClick={() => onRemove(job.id)}>
                      <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" aria-hidden="true">
                        <path d="M4 4l8 8M12 4l-8 8" />
                      </svg>
                    </button>
                  </div>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            <Row name="Fit" jobs={jobs} cell={(job) => <span className={FIT_CLASS[job.fit]}>{FIT_WORD[job.fit]}</span>} />
            <Row
              name="Level"
              jobs={jobs}
              cell={(job) => (
                <div className="dm-stack">
                  <span className="dm-lvl">{LEVEL_WORD[job.level]}</span>
                  {job.levelQuote && (
                    <span className="dm-muted">
                      The posting says: <span className="dm-ink">{quoted(job.levelQuote)}</span>
                    </span>
                  )}
                </div>
              )}
            />
            <Row name="Place and mode" jobs={jobs} cell={(job) => placeLine(job)} />
            <Row name="Asks checked" jobs={jobs} cell={(job) => <AskPips asks={job.asks} />} />
            <Row
              name="Asks met"
              jobs={jobs}
              cell={(job) =>
                job.met.length === 0 ? (
                  <span className="dm-muted">No ask met yet</span>
                ) : (
                  <ul className="dm-asklist">
                    {job.met.map((m, i) => (
                      <li key={i} className="dm-askitem">
                        <span className={`dm-pip ${m.partly ? 'dm-pip-part' : 'dm-pip-met'}`} aria-hidden="true" />
                        <span>
                          {quoted(m.ask)}
                          {m.partly && <span className="dm-muted"> · partly</span>}
                        </span>
                      </li>
                    ))}
                  </ul>
                )
              }
            />
            <Row
              name="Not on your resume"
              jobs={jobs}
              cell={(job) => {
                const more = Math.max(0, (job.asks?.notShown ?? 0) - job.gaps.length);
                if (job.gaps.length === 0 && more === 0) {
                  const total = asksTotal(job.asks);
                  return <span className="dm-muted">{total > 0 ? `None among the ${total} asks checked` : 'None among the asks checked'}</span>;
                }
                return (
                  <div className="dm-stack">
                    <ul className="dm-asklist">
                      {job.gaps.map((g, i) => (
                        <li key={i} className="dm-askitem">
                          <span className="dm-pip dm-pip-gap" aria-hidden="true" />
                          <span className="dm-danger">{quoted(g)}</span>
                        </li>
                      ))}
                    </ul>
                    {more > 0 && <span className="dm-muted">{more === 1 ? 'and 1 more' : `and ${more} more`} in the full match</span>}
                  </div>
                );
              }}
            />
            <Row
              name="How we found it"
              jobs={jobs}
              cell={(job) => (
                <div className="dm-stack">
                  <span>{foundLine(job)}</span>
                  {freshLines(job).map((l, i) => (
                    <span key={l} className={i === 1 && job.openLong ? 'dm-strong' : 'dm-muted'}>
                      {l}
                    </span>
                  ))}
                </div>
              )}
            />
            <Row name="Salary" jobs={jobs} cell={(job) => <span className="dm-muted">{salaryLine(job)}</span>} />
            <Row
              name="What next"
              jobs={jobs}
              cell={(job) => {
                const href = applyHref(job);
                return (
                  <div className="dm-stack">
                    {href ? (
                      <a className="btn btn-primary btn-sm dm-btn-sm" href={href} target="_blank" rel="noopener noreferrer" aria-label={`${applyText(job)} for ${label(job)}, opens in a new tab`}>
                        {applyText(job)}
                      </a>
                    ) : (
                      <span className="dm-muted">{`Apply on ${job.applyHost}`}</span>
                    )}
                    <div className="dm-actions dm-actions-tight">
                      <button type="button" className="btn btn-sm dm-btn-sm" onClick={() => onPrepare(job)}>
                        Prepare
                      </button>
                      <button type="button" className="btn btn-sm dm-btn-sm" onClick={() => onSeeMatch(job)}>
                        See how you fit
                      </button>
                    </div>
                  </div>
                );
              }}
            />
          </tbody>
        </table>
      </div>

      <p className="dm-footer">Everything quoted comes word for word from the posting or your profile. Roles come from employers' own job boards. We never use LinkedIn data.</p>
    </div>
  );
}

function Row({ name, jobs, cell }: { name: string; jobs: readonly AiJob[]; cell: (job: AiJob) => ReactNode }) {
  return (
    <tr>
      <th scope="row">
        <span className="kicker">{name}</span>
      </th>
      {jobs.map((job) => (
        <td key={job.id}>{cell(job)}</td>
      ))}
    </tr>
  );
}
