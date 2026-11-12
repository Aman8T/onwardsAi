/**
 * "A second file arrives": how a file's lines met the blocks the person already has. Measured content, so it is
 * straight: no tilt, no turned piece except the one "new" stamp. Four groups: Same (a count, nothing to do), New (each
 * new block), Near (the two wordings side by side) and Disagree (the two texts, what differs named in words, and the
 * differing words marked with the existing word diff). Nothing is resolved here: each file keeps its own words.
 */
import { useState } from 'react';
import type { ReactNode } from 'react';
import { Now } from './diff';
import { countsOfReport, differsSentence, mergeSentence } from './model';
import { ScreenLink } from './parts';
import type { ResumeQuery } from './route';
import type { MergeEntry, MergeItem } from './types';

/** Show this many of a long group, then the rest on request. */
const SHOWN = 6;

function Capped({ items, children }: { items: readonly MergeItem[]; children: (item: MergeItem) => ReactNode }) {
  const [all, setAll] = useState(false);
  const shown = all ? items : items.slice(0, SHOWN);
  return (
    <>
      {shown.map(children)}
      {items.length > SHOWN && !all && (
        <div className="rs-row rs-row-single">
          <button type="button" className="btn btn-sm rs-btn" onClick={() => setAll(true)}>
            Show the other {items.length - SHOWN}
          </button>
        </div>
      )}
    </>
  );
}

/** Two wordings, side by side (stacked under 760 px). With `mark`, the words that differ are marked on both sides. */
function Sides({ mine, theirs, mark }: { mine: string; theirs: string; mark: boolean }) {
  return (
    <div className="rs-sides">
      <p className="rs-side">
        <span className="rs-cite">this file</span>
        <span className="rs-small">{mark ? <Now before={theirs} after={mine} /> : mine}</span>
      </p>
      <p className="rs-side">
        <span className="rs-cite">what you had</span>
        <span className="rs-small">{mark ? <Now before={mine} after={theirs} /> : theirs}</span>
      </p>
    </div>
  );
}

export function MergeCard({ entry, onClose, onNavigate }: { entry: MergeEntry; onClose: () => void; onNavigate: (to: ResumeQuery) => void }) {
  const { report } = entry;
  const counts = countsOfReport(report);
  const total = counts.same + counts.new + counts.near + counts.disagree;
  const titleId = `rs-merge-${report.fileId}`;

  return (
    <section className="m-tile m-graph rs-card rs-merge" aria-labelledby={titleId}>
      <div className="rs-card-head">
        <div className="rs-merge-head">
          <h2 id={titleId} className="kicker">
            A second file arrives
          </h2>
          <p className="title rs-merge-name">{report.filename}</p>
          <p className="rs-small">{mergeSentence(counts)}</p>
        </div>
        <button type="button" className="btn btn-sm btn-quiet rs-btn" onClick={onClose}>
          Close
        </button>
      </div>

      {total > 0 && (
        <div className="rs-merge-groups">
          {counts.same > 0 && (
            <details className="rs-group">
              <summary className="rs-group-head">
                <span className="rs-small rs-strong">
                  Same · <span className="numeral">{counts.same}</span> {counts.same === 1 ? 'line' : 'lines'}
                </span>
                <span className="rs-fine">A block already says each of these. Nothing to do.</span>
              </summary>
              <ul className="rs-rows rs-rows-in">
                <Capped items={report.same}>
                  {(item) => (
                    <li key={`${item.blockId}:${item.text}`} className="rs-row rs-row-single">
                      <span className="rs-small">{item.text}</span>
                    </li>
                  )}
                </Capped>
              </ul>
            </details>
          )}

          {counts.new > 0 && (
            <section className="rs-group" aria-label="New">
              <div className="rs-group-head">
                <span className="rs-small rs-strong">
                  New · <span className="numeral">{counts.new}</span> {counts.new === 1 ? 'block' : 'blocks'}
                </span>
                <span className="stamp">new</span>
              </div>
              <ul className="rs-rows rs-rows-in">
                <Capped items={report.new}>
                  {(item) => (
                    <li key={item.blockId} className="rs-row rs-row-single">
                      <span className="rs-small">{item.text}</span>
                    </li>
                  )}
                </Capped>
              </ul>
            </section>
          )}

          {counts.near > 0 && (
            <section className="rs-group" aria-label="Worded differently">
              <div className="rs-group-head">
                <span className="rs-small rs-strong">
                  Near · <span className="numeral">{counts.near}</span> · the same work, said two ways
                </span>
              </div>
              <ul className="rs-rows rs-rows-in">
                <Capped items={report.near}>
                  {(item) => (
                    <li key={item.blockId} className="rs-row rs-row-single">
                      <Sides mine={item.text} theirs={item.existing ?? ''} mark={false} />
                    </li>
                  )}
                </Capped>
              </ul>
              <p className="rs-fine">The block keeps its words. This file's wording is listed under it.</p>
            </section>
          )}

          {counts.disagree > 0 && (
            <section className="rs-group" aria-label="Disagree">
              <div className="rs-group-head">
                <span className="rs-small rs-strong">
                  Disagree · <span className="numeral">{counts.disagree}</span> · two files, two answers
                </span>
              </div>
              <ul className="rs-rows rs-rows-in">
                <Capped items={report.disagree}>
                  {(item) => (
                    <li key={item.blockId} className="rs-row rs-row-single">
                      <Sides mine={item.text} theirs={item.existing ?? ''} mark />
                      {item.differs && item.differs.length > 0 && <span className="rs-fine rs-accent">{differsSentence(item.differs)}</span>}
                    </li>
                  )}
                </Capped>
              </ul>
              <p className="rs-fine">Both are kept. The difference shows on the block and stops no export.</p>
            </section>
          )}
        </div>
      )}

      <p className="rs-fine">
        Each file keeps its own words. New resumes follow your default.{' '}
        <ScreenLink to={{ screen: 'resume', id: entry.resumeId }} onNavigate={onNavigate} className="rs-link">
          Open this file as a resume
        </ScreenLink>
      </p>
    </section>
  );
}
