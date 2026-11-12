/**
 * The middle of the results page: the filter chips with the hidden-roles control, and the sections of cards (strong
 * fits, good fits, stretches worth a try), each a grid that collapses to one column on a phone.
 *
 * The filters are plain toggle buttons (All · Strong fit · Good fit · A stretch · Saved); Strong fit appears only when
 * there is one. A role the person said no to stays in the grid as a slim "Hidden" row while it is fresh or when "Show"
 * is pressed, with Undo; "Show" also offers to forget every "not for me" at once.
 */
import type { AiFitWord, AiJob } from '../../../shared/jobsearch';
import { plural } from '../../lib/ui';
import type { NoReason } from '../jobsearch/avoid';
import { HiddenRow, JobCard, RoleHero } from './JobCard';
import type { RoleCtl } from './JobCard';
import { IconDeck } from './icons';

export type Filter = 'all' | AiFitWord | 'saved';

export interface Counts {
  all: number;
  strong: number;
  good: number;
  stretch: number;
  saved: number;
}

export const countsOf = (jobs: readonly AiJob[], isSaved: (id: string) => boolean): Counts => ({
  all: jobs.length,
  strong: jobs.filter((j) => j.fit === 'strong').length,
  good: jobs.filter((j) => j.fit === 'good').length,
  stretch: jobs.filter((j) => j.fit === 'stretch').length,
  saved: jobs.filter((j) => isSaved(j.id)).length,
});

export interface ToolbarProps {
  filter: Filter;
  onFilter: (f: Filter) => void;
  counts: Counts;
  /** Roles hidden among these results. */
  hiddenCount: number;
  showHidden: boolean;
  onShowHidden: (on: boolean) => void;
  /** Anything at all the search has been taught to skip. */
  anyAvoided: boolean;
  onResetHidden: () => void;
  /** Phones: back to the deck. */
  onDeck?: () => void;
}

export function Toolbar({ filter, onFilter, counts, hiddenCount, showHidden, onShowHidden, anyAvoided, onResetHidden, onDeck }: ToolbarProps) {
  const chips: { id: Filter; label: string }[] = [
    { id: 'all', label: `All · ${counts.all}` },
    ...(counts.strong > 0 ? [{ id: 'strong' as const, label: `Strong fit · ${counts.strong}` }] : []),
    { id: 'good', label: `Good fit · ${counts.good}` },
    { id: 'stretch', label: `A stretch · ${counts.stretch}` },
    { id: 'saved', label: `Saved · ${counts.saved}` },
  ];
  return (
    <div className="dc-toolbar">
      <div className="dc-toolbar-row">
        <div className="dc-filters" role="group" aria-label="Show">
          {chips.map((c) => (
            <button key={c.id} type="button" className="dc-filter" aria-pressed={filter === c.id} onClick={() => onFilter(c.id)}>
              {c.label}
            </button>
          ))}
        </div>
        <div className="dc-grow" />
        {onDeck && (
          <button type="button" className="btn btn-sm dc-btn-sm" onClick={onDeck}>
            <IconDeck />
            Show as a deck
          </button>
        )}
        {(hiddenCount > 0 || anyAvoided) && (
          <span className="dc-hidden-ctl dc-muted dc-small">
            {hiddenCount > 0 ? `${hiddenCount} hidden · ` : 'Skipping roles you said no to · '}
            <button type="button" className="dc-link dc-link-pad" aria-expanded={showHidden} onClick={() => onShowHidden(!showHidden)}>
              {showHidden ? 'Hide again' : 'Show'}
            </button>
          </span>
        )}
      </div>
      {showHidden && (
        <p className="dc-muted dc-small dc-hidden-note">
          The next search skips roles like these.{' '}
          <button type="button" className="dc-link dc-link-pad" onClick={onResetHidden}>
            Forget everything I said no to
          </button>
        </p>
      )}
    </div>
  );
}

const SECTIONS: { fit: AiFitWord; title: string; sub: (n: number) => string }[] = [
  { fit: 'strong', title: 'Strong fits', sub: (n) => `${plural(n, 'role')} where your evidence meets what they ask` },
  { fit: 'good', title: 'Good fits', sub: (n) => `${plural(n, 'role')} where your evidence meets much of what they ask` },
  { fit: 'stretch', title: 'Stretches worth a try', sub: (n) => `${plural(n, 'role')} a step up, or where more is not on your resume yet` },
];

export interface SectionsProps {
  /** The roles to draw, in order: the visible ones and the hidden ones that are showing as slim rows. */
  rows: readonly AiJob[];
  hidden: ReadonlySet<string>;
  now: number;
  ctlOf: (job: AiJob) => RoleCtl;
  reasonOf: (id: string) => NoReason | undefined;
  onUnhide: (job: AiJob) => void;
  /** Open the "Why not this one?" popover on this card first (the dev gallery). */
  popoverId?: string;
}

export function Sections({ rows, hidden, now, ctlOf, reasonOf, onUnhide, popoverId }: SectionsProps) {
  // the first role in the order the search computed (strong, then good, then stretch) opens the page as the hero
  const lead = SECTIONS.flatMap(({ fit }) => rows.filter((j) => j.fit === fit && !hidden.has(j.id)))[0];
  return (
    <>
      {lead && <RoleHero job={lead} now={now} ctl={ctlOf(lead)} popoverOpen={popoverId === lead.id} />}
      {SECTIONS.map(({ fit, title, sub }) => {
        const inSection = rows.filter((j) => j.fit === fit);
        const shown = inSection.filter((j) => !hidden.has(j.id)).length;
        const tiles = inSection.filter((j) => j !== lead);
        if (tiles.length === 0) return null;
        return (
          <section key={fit} className="dc-section" aria-label={title}>
            <div className="dc-section-head">
              <h2 className="title dc-section-title">{title}</h2>
              <span className="dc-muted">{sub(shown)}</span>
            </div>
            <ul className={`dc-grid is-${fit}`}>
              {tiles.map((job) =>
                hidden.has(job.id) ? (
                  <HiddenRow key={job.id} job={job} reason={reasonOf(job.id)} onUndo={() => onUnhide(job)} />
                ) : (
                  <JobCard key={job.id} job={job} now={now} ctl={ctlOf(job)} popoverOpen={popoverId === job.id} />
                ),
              )}
            </ul>
          </section>
        );
      })}
    </>
  );
}
