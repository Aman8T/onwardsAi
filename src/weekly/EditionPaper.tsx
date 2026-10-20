/**
 * One edition of The Leafout Weekly as a newspaper page (design canvas https://claude.ai/artifact/BpwdFiwGf6tHjy7H1VqM12,
 * "An edition, read in the app"): the ears line, the blackletter nameplate, the double-rule strap, the lead story of
 * openings that fit with its stretches below it, the right column (Your week, Tip of the week, a pull quote, Letters),
 * the news from the reader's field in three columns, and the footer. It reflows to one column when the sheet is narrower
 * than 760 px (a container query, so it holds in a narrow pane as well as on a phone).
 *
 * Everything shown comes from the edition: a part the edition lacks is left out, never filled in. The paper stays paper
 * in both schemes (the press tokens). Links to employers' boards and articles open in a new tab; an address that is not a
 * web link renders as plain text.
 */
import { useEffect, useId } from 'react';
import type { WeeklyEdition, WeeklyJob, WeeklyNews, WeeklyProgress } from '../../shared/weekly';
import { ensurePressFonts } from '../portfolio/press/fonts';
import { FIT_WORD, dayName, deckFor, isPlaceholder, longDate, safeHref, shortDate } from './format';
import '../styles/weekly.css';

export interface EditionPaperProps {
  edition: WeeklyEdition;
  /** Footer links; the footer shows each one only when its handler is given. */
  onManage?: () => void;
  onUnsubscribe?: () => void;
  /** A sample edition: its listings are not real postings, so no link to an employer's board is drawn. */
  sample?: boolean;
}

export function EditionPaper({ edition, onManage, onUnsubscribe, sample = false }: EditionPaperProps) {
  useEffect(() => ensurePressFonts(), []);
  const uid = useId();
  const [lead, ...rest] = edition.jobs;
  const hasSide = !!edition.progress || !!edition.tip || !!edition.quote;
  const day = dayName(edition.weekOf);

  return (
    <article className="wk-paper" aria-label={`The Leafout Weekly, number ${edition.issue}, for ${edition.reader}`}>
      <div className="wk-in">
        <p className="wk-ears">
          <span>{edition.place ? `${edition.place} edition` : 'Your edition'}</span>
          <span>
            {longDate(edition.weekOf)} · Vol. I, No. {edition.issue}
          </span>
          <span>Free · Every {day}</span>
        </p>
        <h1 className="wk-name">The Leafout Weekly</h1>
        <p className="wk-strap">All the openings fit to print · for {edition.reader} · every line cited</p>

        <div className={`wk-grid${hasSide ? '' : ' wk-grid--solo'}`}>
          <section className="wk-lead" aria-labelledby={`${uid}-lead`}>
            <p className="wk-kick">Openings that fit</p>
            <h2 className="wk-hl" id={`${uid}-lead`}>
              {edition.headline}
            </h2>
            {edition.jobs.length > 0 && <p className="wk-deck">{deckFor(edition.jobs)}</p>}
            {lead ? (
              <LeadJob job={lead} links={!sample} />
            ) : (
              <p className="wk-quiet">Nothing new on an employer's own board fit your graph this week. The next edition looks again.</p>
            )}
            {rest.length > 0 && (
              <>
                <div className="wk-rule" aria-hidden="true" />
                <div className="wk-two">
                  {rest.map((j) => (
                    <OtherJob key={j.listingId} job={j} links={!sample} />
                  ))}
                </div>
              </>
            )}
          </section>

          {hasSide && (
            <aside className="wk-side" aria-label="Your week, a tip and a letter">
              {edition.progress && <WeekBox progress={edition.progress} />}
              {edition.tip && (
                <div className="wk-box">
                  <h2 className="wk-box-t">Tip of the week</h2>
                  <p className="wk-box-p">{edition.tip}</p>
                </div>
              )}
              {edition.quote && (
                <blockquote className="wk-pull">
                  <p>“{edition.quote}”</p>
                </blockquote>
              )}
              <div className="wk-box">
                <h2 className="wk-box-t">Letters</h2>
                <p className="wk-box-p">Reply to the emailed edition with what's missing from it. We read every one.</p>
              </div>
            </aside>
          )}
        </div>

        {edition.news.length > 0 && (
          <>
            <div className="wk-rule" aria-hidden="true" />
            <section aria-labelledby={`${uid}-field`}>
              <h2 className="wk-kick" id={`${uid}-field`}>
                From your field this week
              </h2>
              <div className="wk-three">
                {edition.news.map((n, i) => (
                  <NewsItem key={`${i}-${n.headline}`} item={n} />
                ))}
              </div>
            </section>
          </>
        )}

        <footer className="wk-foot">
          <span>Every line cites your resume, your links, an employer's own board, or a named article.</span>
          {(onManage || onUnsubscribe) && (
            <span className="wk-foot-links">
              {onManage && (
                <button type="button" className="wk-link" onClick={onManage}>
                  Manage subscription
                </button>
              )}
              {onManage && onUnsubscribe && ' · '}
              {onUnsubscribe && (
                <button type="button" className="wk-link" onClick={onUnsubscribe}>
                  Unsubscribe
                </button>
              )}
            </span>
          )}
        </footer>
      </div>
    </article>
  );
}

function ApplyLink({ job, children }: { job: WeeklyJob; children: string }) {
  const href = safeHref(job.applyUrl);
  if (!href) return null;
  return (
    <a className="wk-apply" href={href} target="_blank" rel="noopener noreferrer">
      {children} →<span className="sr-only"> (opens the employer's own board in a new tab)</span>
    </a>
  );
}

function LeadJob({ job, links }: { job: WeeklyJob; links: boolean }) {
  return (
    <div className="wk-cols">
      <p>
        <span className="wk-dl">{job.place} —</span> {job.employer} is hiring a <b>{job.title}</b>, listed on its own board this week.{' '}
        {FIT_WORD[job.fit]}.
      </p>
      {(job.met.length > 0 || job.notShown.length > 0) && (
        <ul className="wk-met" aria-label={`The asks of the ${job.title} role against your record`}>
          {job.met.map((m, i) => (
            <li key={`m${i}`}>
              <b>Met</b> · {m.ask}
              <br />
              <span className="wk-cite">
                {m.cite.source} · {m.cite.text}
              </span>
            </li>
          ))}
          {job.notShown.map((n, i) => (
            <li key={`n${i}`}>
              <b>Not shown</b> · {n}
            </li>
          ))}
        </ul>
      )}
      {job.note && <p>{job.note}</p>}
      {links && <ApplyLink job={job}>{`Read it on ${job.employer}'s board`}</ApplyLink>}
    </div>
  );
}

function OtherJob({ job, links }: { job: WeeklyJob; links: boolean }) {
  return (
    <div>
      <p className="wk-kick">{FIT_WORD[job.fit]}</p>
      <h3 className="wk-small-hl">
        {job.employer} · {job.title}
      </h3>
      <p className="wk-p">{[job.place, job.note].filter(Boolean).join('. ')}</p>
      {links && <ApplyLink job={job}>Read the posting</ApplyLink>}
    </div>
  );
}

function WeekBox({ progress }: { progress: WeeklyProgress }) {
  const rows: Array<[string, string]> = [];
  const hasWeek = progress.planWeek !== undefined && progress.planWeeks !== undefined;
  if (progress.planRole) {
    rows.push(
      hasWeek ? [`Prepare plan · ${progress.planRole}`, `week ${progress.planWeek} of ${progress.planWeeks}`] : ['Prepare plan', progress.planRole],
    );
  }
  if (progress.ticksThisWeek !== undefined && progress.ticksPlanned !== undefined)
    rows.push(['Ticked this week', `${progress.ticksThisWeek} of ${progress.ticksPlanned}`]);
  if (progress.closestRole) rows.push(['Closest role', [progress.closestRole, progress.closestEta].filter(Boolean).join(' · ')]);
  if (progress.lastPractice) rows.push(['Last practice', shortDate(progress.lastPractice)]);
  return (
    <div className="wk-box">
      <h2 className="wk-box-t">Your week</h2>
      {rows.length > 0 && (
        <dl className="wk-rows">
          {rows.map(([k, v]) => (
            <div className="wk-row" key={k}>
              <dt>{k}</dt>
              <dd>{v}</dd>
            </div>
          ))}
        </dl>
      )}
      {progress.nudge && <p className="wk-box-p wk-nudge">{progress.nudge}</p>}
      {rows.length === 0 && !progress.nudge && <p className="wk-box-p">Nothing to report yet. Open a role on your graph and ask how to prepare.</p>}
    </div>
  );
}

function NewsItem({ item }: { item: WeeklyNews }) {
  const href = safeHref(item.url);
  const ph = isPlaceholder(item.headline);
  const cite = [item.publication, item.date ? shortDate(item.date) : isPlaceholder(item.publication) ? '[date]' : ''].filter(Boolean).join(' · ');
  return (
    <div className="wk-news">
      <h3 className={`wk-small-hl${ph ? ' wk-ph' : ''}`}>
        {href ? (
          <a href={href} target="_blank" rel="noopener noreferrer">
            {item.headline}
            <span className="sr-only"> (opens the article in a new tab)</span>
          </a>
        ) : (
          item.headline
        )}
      </h3>
      <p className="wk-cite">{cite}</p>
      <p className={`wk-p${isPlaceholder(item.why) ? ' wk-ph' : ''}`}>{item.why}</p>
      {!isPlaceholder(item.why) && <p className="wk-cite">On your graph · {item.skill}</p>}
    </div>
  );
}
