/**
 * Your blocks: everything the person has done, held once. One group per job, project and degree, each block with the
 * source it comes from and how many of the person's resumes use it. The counts are the server's; nothing here is
 * worked out from a resume on screen.
 */
import { useState } from 'react';
import { groupBlocks, sourceCounts, sourceOf } from './model';
import { ScreenLink, SourceLine, StatePanel } from './parts';
import type { ResumeQuery } from './route';
import type { BlocksResponse, Failure, LoadStatus, ResumeBlock } from './types';

type Kind = 'resume' | 'other' | 'user';
type Filter = 'all' | Kind;

const FILTERS: readonly { id: Filter; label: string }[] = [
  { id: 'all', label: 'All' },
  { id: 'resume', label: 'From your file' },
  { id: 'other', label: 'From GitHub or other sources' },
  { id: 'user', label: 'Your words' },
];

/** Where a block comes from, told apart by its sources: the person's own words, their resume file, anything else. */
const kindOf = (b: ResumeBlock): Kind => (b.fact.refs.some((r) => r.source === 'user') ? 'user' : b.fact.refs[0]?.source === 'resume' ? 'resume' : 'other');

export interface BlocksViewProps {
  data: BlocksResponse | null;
  status: LoadStatus;
  error?: Failure;
  onRetry: () => void;
  /** Where "Your resumes" goes back to. */
  back: ResumeQuery;
  onNavigate: (to: ResumeQuery) => void;
}

export function BlocksView({ data, status, error, onRetry, back, onNavigate }: BlocksViewProps) {
  const [filter, setFilter] = useState<Filter>('all');
  if (!data) {
    if (status === 'error') {
      return (
        <StatePanel
          title="Your blocks didn't load"
          action={
            <button type="button" className="btn btn-primary rs-btn" onClick={onRetry}>
              Try again
            </button>
          }
        >
          {error?.message}
        </StatePanel>
      );
    }
    return <StatePanel title="Opening your blocks…" busy />;
  }

  const kinds = new Map<Kind, number>();
  for (const b of data.blocks) kinds.set(kindOf(b), (kinds.get(kindOf(b)) ?? 0) + 1);
  // a chip only where the blocks really fall on both sides of it
  const chips = FILTERS.filter((f) => f.id === 'all' || (kinds.get(f.id) ?? 0) > 0);
  const active: Filter = chips.some((c) => c.id === filter) ? filter : 'all';
  const groups = groupBlocks(data)
    .map((g) => (active === 'all' ? g : { ...g, blocks: g.blocks.filter((b) => kindOf(b) === active) }))
    .filter((g) => active === 'all' || g.blocks.length > 0);
  const n = data.blocks.length;
  const from = sourceCounts(data);

  if (n === 0) {
    return (
      <StatePanel title="No blocks yet">
        Blocks are read from your resume and the sources you gave. Once an analysis has read them, each job, project, degree and bullet shows here.
      </StatePanel>
    );
  }

  return (
    <>
      <header className="rs-intro">
        <p className="kicker">Your blocks · everything you have done, held once</p>
        <h1 className="serif rs-title" data-rs-h1 tabIndex={-1}>
          {n === 1 ? '1 block.' : `${n} blocks.`} Every resume is one way of arranging them.
        </h1>
        <p className="rs-lede">
          A block is one fact, kept with the words of its source. The fact never changes. What changes from resume to resume is whether the block is in, where it sits, and which words it uses.
        </p>
        {from && <p className="rs-fine">{from}</p>}
        {chips.length > 2 && (
          <div className="rs-chips" role="group" aria-label="Show blocks">
            {chips.map((c) => (
              <button key={c.id} type="button" className="rs-chipbtn" aria-pressed={active === c.id} onClick={() => setFilter(c.id)}>
                {c.label} <span className="numeral">{c.id === 'all' ? n : kinds.get(c.id)}</span>
              </button>
            ))}
          </div>
        )}
      </header>

      <div className="rs-two">
          <div className="rs-stack">
            {groups.map((g) => (
              <section key={g.key} className="panel rs-card rs-blockgroup" aria-labelledby={`rs-bg-${g.key}`}>
                <div className="rs-card-head">
                  <h2 id={`rs-bg-${g.key}`} className="rs-small rs-strong">
                    {g.title}
                  </h2>
                  <span className="rs-cite">{g.note}</span>
                </div>
                {g.blocks.length > 0 ? (
                  <ul className="rs-rows rs-rows-in">
                    {g.blocks.map((b) => {
                      const source = sourceOf(b.fact);
                      return (
                        <li key={b.fact.id} className="rs-row rs-row-top" data-block={b.fact.id}>
                          <span className="rs-row-main">
                            <span className="rs-small">{b.fact.text}</span>
                            <span className="rs-fine">
                              in <span className="numeral">{b.usedIn}</span> of your <span className="numeral">{data.resumes}</span> {data.resumes === 1 ? 'resume' : 'resumes'}
                            </span>
                            {source && <SourceLine source={source} quote={source.quote !== b.fact.text} />}
                          </span>
                          {source?.fromUser && <span className="rs-cite rs-accent">your words</span>}
                        </li>
                      );
                    })}
                  </ul>
                ) : (
                  <p className="rs-fine">No bullet under this one.</p>
                )}
              </section>
            ))}
          </div>

          <div className="rs-stack">
            <section className="panel rs-card" aria-labelledby="rs-bl-how">
              <h2 id="rs-bl-how" className="kicker">
                What changes, and what never does
              </h2>
              <ul className="rs-list rs-small">
                <li>From one resume to the next: which bullets are in, their order inside a job, and their wording.</li>
                <li>Never: an employer, a title, a date, or anything no block supports.</li>
                <li>A line you typed and kept as your own words becomes a block too, labelled as yours.</li>
              </ul>
              <p className="rs-fine">
                A block is moved, left out or reworded inside a resume.{' '}
                <ScreenLink to={back} onNavigate={onNavigate}>
                  Back to your resumes
                </ScreenLink>
              </p>
            </section>
          </div>
      </div>
    </>
  );
}
