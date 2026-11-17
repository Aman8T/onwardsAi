/**
 * What a visitor sees: the server's `public` content drawn small, in the app's colours (canvas boards "the report as a
 * page" and "When the resume backs only one role"). It draws `public` as given and nothing else, so the preview is
 * exactly the page: hidden quotes, employer names while they are off, and the near misses while they are private are
 * simply not in it. The counts are dots with the same count in words, never a percentage.
 *
 * The page's one serif line is the headline; inside the sheet that line belongs to the sheet's own title, so here the
 * headline is the sans `.title`.
 */
import type { ReportContent, ReportRole } from '../../shared/report';
import { Pips } from '../components/materials';
import { askCount, askPips, etaLabel, headline, offerHref, topAreas, whoOf } from './format';
import '../styles/report.css';

export interface ReportPreviewProps {
  content: ReportContent;
  /** The paid step's configured link; each role gets one only when it is present. */
  offerUrl?: string;
  /** Pins the narrow layout (the dev page's phone frame). */
  narrow?: boolean;
}

export function ReportPreview({ content, offerUrl, narrow = false }: ReportPreviewProps) {
  const { roles, nearMisses, person, openToWork } = content;
  const areas = topAreas(content.areas);
  const who = whoOf(content);

  return (
    <article className="rp-page" data-narrow={narrow ? 'true' : 'false'} aria-label={`The report as a visitor sees it: ${headline(content)}`}>
      <header className="rp-page-head">
        <p className="m-fine">Where can {who === 'This person' ? 'this person' : who} go next</p>
        <h3 className="title rp-page-title">{headline(content)}</h3>
        {person.line && <p className="rp-page-line">{person.line}</p>}
        {(openToWork || areas.names.length > 0) && (
          <ul className="rp-chips" aria-label="Areas and status">
            {openToWork && (
              <li>
                <span className="stamp stamp-ok rp-open">Open to work</span>
              </li>
            )}
            {areas.names.map((n) => (
              <li key={n}>
                <span className="chip">{n}</span>
              </li>
            ))}
            {areas.more > 0 && (
              <li>
                <span className="chip">
                  +{areas.more} more {areas.more === 1 ? 'area' : 'areas'}
                </span>
              </li>
            )}
          </ul>
        )}
      </header>

      {roles.length > 0 && (
        <ol className="rp-roles" aria-label="Roles">
          {roles.map((r) => (
            <RoleCard key={r.id} role={r} offerUrl={offerUrl} />
          ))}
        </ol>
      )}

      {nearMisses.length > 0 && (
        <section className="rp-close m-graph" aria-label="Close, not yet">
          <h4 className="rp-close-title">Close, not yet</h4>
          <ul className="rp-close-list">
            {nearMisses.map((m) => (
              <li key={m.id} className="rp-close-item">
                <span className="rp-role-title">{m.title}</span>
                {m.missing.length > 0 && <span className="rp-muted">Not shown yet: {m.missing.join(' · ')}</span>}
              </li>
            ))}
          </ul>
        </section>
      )}
    </article>
  );
}

function RoleCard({ role, offerUrl }: { role: ReportRole; offerUrl?: string }) {
  const count = askCount(role);
  const offer = offerHref(offerUrl, role.title);
  return (
    <li className="rp-role">
      <div className="rp-role-head">
        <h4 className="rp-role-title">{role.title}</h4>
        {role.ready ? <span className="stamp stamp-ok">Ready now</span> : role.eta ? <span className="rp-muted">{etaLabel(role.eta)}</span> : null}
      </div>
      <div className="rp-count">
        <Pips pips={askPips(role)} label={`${count} with evidence`} r={5} gap={6} />
        <span className="rp-muted" aria-hidden="true">
          {count}
        </span>
      </div>
      {role.quotes.length > 0 && (
        <div className="rp-quotes">
          {role.quotes.map((q) => (
            <blockquote key={q.id} className="rp-quote">
              <p>“{q.text}”</p>
              <cite className="m-cite">{q.source}</cite>
            </blockquote>
          ))}
        </div>
      )}
      {role.missing.length > 0 && (
        <p className="rp-missing">
          <span className="rp-muted">Not shown yet: </span>
          {role.missing.join(' · ')}
        </p>
      )}
      {offer && (
        <a className="rp-offer" href={offer} target="_blank" rel="noopener noreferrer">
          Get Day One for this role
        </a>
      )}
    </li>
  );
}
