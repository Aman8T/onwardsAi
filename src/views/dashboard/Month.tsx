/**
 * The bottom band of the hero: the current month as a torn calendar page, taped and turned two degrees, with the weeks in a
 * row beside it. All of it is the person's own doing, so all of it is hand-touched: the grid is ruled by hand, a cross is a
 * day with a dated record, a scribble sits under a day with two or more, today is ringed (calendar.ts draws them, seeded
 * by the year and month, so the page looks the same every time it is opened). A visit is not recorded anywhere, so a cross
 * means something was saved that day.
 */
import type { Streak } from './deskModel';
import { CAL_VIEWBOX, monthSpoken } from './calendar';
import type { MonthPage as Page } from './calendar';

const WEEKDAYS = ['M', 'T', 'W', 'T', 'F', 'S', 'S'];
const RUN_ARROW = 'M61 6C44 1 22 7 5 24M5 24l2.5-11M5 24l11-1.5';
const RUN_RING = 'M52 5C24 3 5 15 6 31c1 16 24 25 49 24 24-1 40-11 39-26C93 15 74 4 46 7';

export function MonthPage({ page }: { page: Page }) {
  const name = new Date(page.year, page.month, 1).toLocaleDateString('en-GB', { month: 'long' });
  return (
    <div className="cal" role="img" aria-label={monthSpoken(page)}>
      <i className="tape" />
      <div className="cal-head">
        <span className="serif cal-month">{name}</span>
        <span className="mono fine">showing up</span>
      </div>
      <div className="cal-wd" aria-hidden="true">
        {WEEKDAYS.map((d, i) => (
          <span key={i}>{d}</span>
        ))}
      </div>
      <div className="cal-grid" aria-hidden="true">
        <svg className="cal-lines" viewBox={CAL_VIEWBOX}>
          <path d={page.lines} />
        </svg>
        {page.days.map((d) => (
          <span key={d.key} className={`cal-d${d.inMonth ? '' : ' out'}${d.today ? ' today' : ''}`}>
            {d.n}
            {(d.mark || d.ring) && (
              <svg viewBox="0 0 32 22">
                {d.fill && <path className="sc" d={d.fill} />}
                {d.mark && <path className="mk" d={d.mark} />}
                {d.ring && <path className="rg" d={d.ring} />}
              </svg>
            )}
          </span>
        ))}
      </div>
    </div>
  );
}

/** The run of weeks, in the person's pen: the number with its hand ring, the margin line, the longest run. */
export function RunNotes({ streak, weekday, anyDay }: { streak: Streak; weekday: string | null; anyDay: boolean }) {
  const on = streak.weeks > 0;
  return (
    <div className="run">
      {on ? (
        <p className="run-n">
          <svg viewBox="0 0 64 34" width="56" height="30" aria-hidden="true" className="run-arrow">
            <path d={RUN_ARROW} />
          </svg>
          <span className="scribble run-ring">
            <span className="pen run-big">{streak.weeks}</span>
            <svg viewBox="0 0 100 60" preserveAspectRatio="none" aria-hidden="true">
              <path d={RUN_RING} />
            </svg>
          </span>
          <span className="pen">{streak.weeks === 1 ? 'week' : 'weeks in a row'}</span>
        </p>
      ) : (
        <p className="pen run-fresh">A fresh week.</p>
      )}
      {weekday ? <p className="pen run-margin">{weekday} are your day.</p> : !anyDay && <p className="pen run-margin">Your first cross goes here.</p>}
      <p className="fine">
        A cross is a day you did something here.
        {streak.longest > 1 ? (
          <>
            {' '}
            Your longest run is <span className="numeral">{streak.longest}</span> weeks.
          </>
        ) : (
          on || ' One thing this week starts a run.'
        )}
      </p>
    </div>
  );
}
