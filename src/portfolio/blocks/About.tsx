/**
 * About: the `aboutLede` and `aboutBody` slots. Variants: lede (big first sentence, body, facts),
 * prose (plain paragraphs, one measure), facts (bio beside a definition list). The facts are fact fields
 * only: location, first job start, latest education.
 */
import type { Fact } from '../../../shared/portfolio';
import { factsOf, formatPeriod } from '../model';
import { EmptyBlock, SectionShell, Slot, blockHasContent, earliestStart, usePf } from './SectionShell';
import type { BlockProps } from './SectionShell';

function useAboutFacts(): { k: string; v: string }[] {
  const { content } = usePf();
  const out: { k: string; v: string }[] = [];
  if (content.person.location) out.push({ k: 'Based in', v: content.person.location });
  const experience = factsOf(content, 'experience');
  const since = earliestStart(experience);
  if (since && experience[0]) {
    const f: Fact = { ...experience[0], start: since, end: undefined, current: false };
    out.push({ k: 'Working since', v: formatPeriod(f) });
  }
  const edu = factsOf(content, 'education')[0];
  if (edu) out.push({ k: 'Studied', v: [edu.title, edu.org].filter(Boolean).join(', ') });
  return out.slice(0, 4);
}

function Facts({ stacked }: { stacked?: boolean }) {
  const rows = useAboutFacts();
  if (!rows.length) return null;
  return (
    <dl className={stacked ? 'pf-facts pf-facts--stacked' : 'pf-facts'}>
      {rows.map((r) => (
        <div key={r.k}>
          <dt>{r.k}</dt>
          <dd>{r.v}</dd>
        </div>
      ))}
    </dl>
  );
}

export function About({ block, index }: BlockProps) {
  const { content, spec, graph } = usePf();
  if (!blockHasContent('about', content, spec, graph)) return <EmptyBlock block={block} index={index} label="About" />;

  let body;
  if (block.variant === 'prose') {
    body = (
      <div className="pf-about pf-about--prose">
        <Slot id="aboutLede" className="pf-prose" />
        <Slot id="aboutBody" className="pf-prose pf-prose--muted" />
      </div>
    );
  } else if (block.variant === 'facts') {
    body = (
      <div className="pf-about pf-about--facts">
        <div className="pf-about-bio">
          <Slot id="aboutLede" className="pf-prose" />
          <Slot id="aboutBody" className="pf-prose pf-prose--muted" />
        </div>
        <Facts stacked />
      </div>
    );
  } else {
    body = (
      <div className="pf-about">
        <Slot id="aboutLede" className="pf-lede" />
        <Slot id="aboutBody" className="pf-body-muted" />
        <Facts />
      </div>
    );
  }

  return (
    <SectionShell block={block} index={index} label="About">
      {body}
    </SectionShell>
  );
}
