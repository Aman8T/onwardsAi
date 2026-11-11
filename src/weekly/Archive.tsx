/**
 * Past editions (canvas "Subscribing, and past editions"): the archive, newest first, with a "1 new" chip while any
 * edition is unread. A row is a real link to `/weekly?e=<id>`; a plain click opens it in place.
 */
import { useId } from 'react';
import type { WeeklyEditionSummary } from '../../shared/weekly';
import { isPlaceholder, shortDate } from './format';
import { weeklyPath } from './route';

export interface ArchiveProps {
  editions: WeeklyEditionSummary[];
  /** The edition on screen. */
  current?: string;
  onOpen: (id: string) => void;
}

export function Archive({ editions, current, onOpen }: ArchiveProps) {
  const uid = useId();
  const fresh = editions.filter((e) => !e.read).length;
  return (
    <section className="panel wk-panel" aria-labelledby={`${uid}-h`}>
      <div className="wk-archive-head">
        <p className="kicker">Past editions</p>
        {fresh > 0 && <span className="chip wk-new">{fresh} new</span>}
      </div>
      <h2 className="serif wk-panel-h" id={`${uid}-h`}>
        The archive
      </h2>
      {editions.length === 0 ? (
        <>
          <p className="hand">The first edition is on its way.</p>
          <p className="wk-hint">Nothing printed yet. Your first edition will be here.</p>
        </>
      ) : (
        <ul className="wk-archive" role="list">
          {editions.map((e) => (
            <li key={e.id}>
              <a
                className="wk-ed"
                href={weeklyPath({ edition: e.id })}
                aria-current={e.id === current ? 'page' : undefined}
                onClick={(ev) => {
                  if (ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.altKey || ev.button !== 0) return;
                  ev.preventDefault();
                  onOpen(e.id);
                }}
              >
                <span className={`wk-ed-t${isPlaceholder(e.headline) ? ' wk-ph' : ''}`}>
                  No. {e.issue} · {e.headline}
                  {!e.read && <span className="sr-only"> (unread)</span>}
                </span>
                <span className="wk-ed-d">
                  {!e.read && <span className="wk-dot" aria-hidden="true" />}
                  {shortDate(e.weekOf)}
                </span>
              </a>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
