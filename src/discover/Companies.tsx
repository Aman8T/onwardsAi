/**
 * The companies lane: up to three employers that hire people like you elsewhere or beyond your reach, with nothing
 * open in the current view. Each says why it is here and links to the employer's own careers page, naming the host.
 */
import { useId } from 'react';
import type { CompanyLead } from '../../shared/discover';
import { hostOf, safeHref } from './model';

export function CompaniesLane({ companies }: { companies: readonly CompanyLead[] }) {
  const titleId = useId();
  if (companies.length === 0) return null;
  return (
    <section className="dc-companies" aria-labelledby={titleId}>
      <h2 id={titleId} className="kicker">
        Companies that hire people like you · nothing open near you now
      </h2>
      <ul className="dc-company-list">
        {companies.map((c) => {
          const href = safeHref(c.careersUrl);
          const host = hostOf(c.careersUrl);
          return (
            <li key={c.employerId} className="dc-company">
              <div className="dc-company-name">
                <span className="dc-strong">{c.name}</span>
                {c.city && <span className="dc-muted dc-small"> · {c.city}</span>}
              </div>
              <p className="dc-muted dc-company-reason">{c.reason}</p>
              {href && (
                <a className="btn btn-sm btn-quiet dc-btn-sm" href={href} target="_blank" rel="noopener" aria-label={`${c.name} careers page${host ? ` on ${host}` : ''}, opens in a new tab`}>
                  {host ? `Careers page on ${host} ↗` : 'Careers page ↗'}
                </a>
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}
