/**
 * Clips (writing samples): title, publication, date and a "Read" link per `clip` fact.
 */
import { factsOf, formatPeriod, safeHref } from '../model';
import { EmptyBlock, MoreButton, SectionShell, useLimit, usePf } from './SectionShell';
import type { BlockProps } from './SectionShell';

export function Clips({ block, index }: BlockProps) {
  const { content } = usePf();
  const facts = factsOf(content, 'clip');
  const lim = useLimit(facts);
  if (!facts.length) return <EmptyBlock block={block} index={index} label="Clips" />;
  return (
    <SectionShell block={block} index={index} label="Clips" meta={`${facts.length} ${facts.length === 1 ? 'piece' : 'pieces'}`}>
      <ul className="pf-clips">
        {lim.visible.map((f) => {
          const href = safeHref(f.url);
          const meta = [f.org, formatPeriod(f)].filter(Boolean).join(' · ');
          return (
            <li key={f.id} className="pf-clip">
              <div className="pf-clip-main">
                <h3 className="pf-clip-title">{f.title}</h3>
                {meta ? <p className="pf-clip-meta">{meta}</p> : null}
              </div>
              {href ? (
                <a
                  className="pf-clip-read"
                  href={href}
                  target={/^mailto:/i.test(href) ? undefined : '_blank'}
                  rel="noopener noreferrer"
                  aria-label={`Read: ${f.title}`}
                >
                  Read <span aria-hidden="true">↗</span>
                </a>
              ) : null}
            </li>
          );
        })}
      </ul>
      <MoreButton {...lim} />
    </SectionShell>
  );
}
