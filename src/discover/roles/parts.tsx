/**
 * Small parts every role view shares: the fit chip, the pips, an Asks / You pair, and the apply link.
 *
 * The pips are the signature of a role: one 10 px square per requirement the search checked, filled when your evidence
 * meets it, half filled when it meets part of it, outlined in red when it is not on your resume. They are a picture of
 * counts and never a score; the count line beside them says the same in words, so the pips themselves are hidden from
 * assistive technology. Everything a posting or a profile wrote is a text node, and the apply link renders only when
 * it is a plain web address.
 */
import type { AiFitWord, AiJob, AskCounts, AskMet } from '../../../shared/jobsearch';
import { FIT_WORD, LEVEL_WORD, countsLine, pipsOf } from '../jobsearch/words';
import { applyLabel, safeHref } from '../util';

export function FitChip({ fit }: { fit: AiFitWord }) {
  return <span className={`chip dc-fit dc-fit-${fit}`}>{FIT_WORD[fit]}</span>;
}

/** The fit word and the level word, side by side. */
export function FitLine({ job }: { job: Pick<AiJob, 'fit' | 'level'> }) {
  return (
    <div className="dc-fitline">
      <FitChip fit={job.fit} />
      <span className="dc-lvl">{LEVEL_WORD[job.level]}</span>
    </div>
  );
}

export function Pips({ asks }: { asks: AskCounts }) {
  return (
    <span className="dc-pips" aria-hidden="true">
      {pipsOf(asks).map((kind, i) => (
        <span key={i} className={`dc-pip dc-pip-${kind}`} />
      ))}
    </span>
  );
}

/** The pips and the count line: "3 asks checked · 2 met · 1 not on your resume". */
export function AskLine({ asks }: { asks: AskCounts }) {
  return (
    <div className="dc-asks">
      <Pips asks={asks} />
      <span className="dc-count">{countsLine(asks)}</span>
    </div>
  );
}

/** "Asks “…”" and "You “…” Resume": the posting's own words, and the line of evidence that meets them. */
export function Pair({ met, className }: { met: AskMet; className?: string }) {
  return (
    <div className={['dc-pair', className].filter(Boolean).join(' ')}>
      <p>
        <span className="dc-lbl">Asks </span>“{met.ask}”
      </p>
      <p>
        <span className="dc-lbl">You </span>“{met.cite.text}”
        {met.cite.source && <span className="dc-cite"> {met.cite.source}</span>}
        {met.partly && <span className="dc-partly"> · partly</span>}
      </p>
    </div>
  );
}

/** "Apply on boards.greenhouse.io ↗", opening the employer's own posting in a new tab. */
export function ApplyLink({ job, className }: { job: Pick<AiJob, 'applyUrl' | 'applyHost' | 'title' | 'employer'>; className: string }) {
  const href = safeHref(job.applyUrl);
  if (!href) return <span className="dc-muted">{`Apply on ${job.applyHost}`}</span>;
  return (
    <a className={className} href={href} target="_blank" rel="noopener noreferrer" aria-label={`Apply on ${job.applyHost} for ${job.title} at ${job.employer}, opens in a new tab`}>
      {applyLabel(job)}
    </a>
  );
}
