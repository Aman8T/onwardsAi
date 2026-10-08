/**
 * Projects: one entry per `project` fact; the blurb is the `proj:<id>` slot (else the fact's detail).
 * Variants: featureList (the first project large, the rest as numbered rows), grid (two-column tiles),
 * list (numbered rows). Tags, org, period and links are fact fields.
 */
import type { Fact } from '../../../shared/portfolio';
import { factsOf, formatPeriod, projectBlurb } from '../model';
import { EmptyBlock, ExtLink, MoreButton, SectionShell, cx, useLimit, usePf, useSlotEditor } from './SectionShell';
import type { BlockProps } from './SectionShell';

const num = (i: number): string => String(i + 1).padStart(2, '0');

function Blurb({ f, className }: { f: Fact; className: string }) {
  const { content } = usePf();
  const editor = useSlotEditor(`proj:${f.id}`);
  if (editor) return <>{editor}</>;
  const text = projectBlurb(content, f);
  return text ? <p className={className}>{text}</p> : null;
}

function Tags({ f }: { f: Fact }) {
  return f.tags.length ? <p className="pf-tags">{f.tags.slice(0, 6).join(' · ')}</p> : null;
}

const metaOf = (f: Fact): string =>
  [f.org, formatPeriod(f)].filter(Boolean).join(' · ') || f.tags.slice(0, 2).join(' · ');

function Row({ f, n }: { f: Fact; n: number }) {
  const meta = metaOf(f);
  return (
    <li className="pf-row">
      <span className="pf-row-n">{num(n)}</span>
      <div className="pf-row-main">
        <h3 className="pf-row-title">{f.title}</h3>
        <Blurb f={f} className="pf-row-blurb" />
        <ExtLink url={f.url} className="pf-row-link" />
      </div>
      {meta ? <span className="pf-row-meta">{meta}</span> : null}
    </li>
  );
}

/* ───────────── featureList ───────────── */

function FeatureList({ facts }: { facts: Fact[] }) {
  const [feature, ...rest] = facts;
  const lim = useLimit(rest);
  return (
    <div className="pf-pj">
      <article className="pf-feature pf-glass">
        <div className="pf-feature-main">
          <span className="pf-kicker">{feature.org ? `01 · ${feature.org}` : '01'}</span>
          <h3 className="pf-feature-title">{feature.title}</h3>
          <Blurb f={feature} className="pf-feature-blurb" />
          <Tags f={feature} />
          <ExtLink url={feature.url} className="pf-feature-link" />
        </div>
        {feature.highlights.length ? (
          <ul className="pf-feature-points">
            {feature.highlights.slice(0, 4).map((h, i) => (
              <li key={i}>{h}</li>
            ))}
          </ul>
        ) : null}
      </article>
      {rest.length ? (
        <>
          <ol className="pf-rows">
            {lim.visible.map((f, i) => (
              <Row key={f.id} f={f} n={i + 1} />
            ))}
          </ol>
          <MoreButton {...lim} />
        </>
      ) : null}
    </div>
  );
}

/* ───────────── grid ───────────── */

function Grid({ facts }: { facts: Fact[] }) {
  const lim = useLimit(facts);
  const wideFirst = lim.visible.length > 1 && lim.visible.length % 2 === 1;
  return (
    <>
      <ul className="pf-grid">
        {lim.visible.map((f, i) => (
          <li key={f.id} className={cx('pf-tile pf-glass', wideFirst && i === 0 && 'pf-tile--wide')}>
            <span className="pf-kicker">{f.org ? `${num(i)} · ${f.org}` : num(i)}</span>
            <h3 className="pf-tile-title">{f.title}</h3>
            <Blurb f={f} className="pf-tile-blurb" />
            <Tags f={f} />
            <ExtLink url={f.url} className="pf-tile-link" />
          </li>
        ))}
      </ul>
      <MoreButton {...lim} />
    </>
  );
}

/* ───────────── list ───────────── */

function List({ facts }: { facts: Fact[] }) {
  const lim = useLimit(facts);
  return (
    <>
      <ol className="pf-rows">
        {lim.visible.map((f, i) => (
          <Row key={f.id} f={f} n={i} />
        ))}
      </ol>
      <MoreButton {...lim} />
    </>
  );
}

/* ───────────── block ───────────── */

export function Projects({ block, index }: BlockProps) {
  const { content } = usePf();
  const facts = factsOf(content, 'project');
  const label = facts.length === 1 ? 'Featured project' : 'Selected work';
  if (!facts.length) return <EmptyBlock block={block} index={index} label={label} />;
  return (
    <SectionShell block={block} index={index} label={label} meta={`${facts.length} ${facts.length === 1 ? 'project' : 'projects'}`}>
      {block.variant === 'grid' ? (
        <Grid facts={facts} />
      ) : block.variant === 'list' ? (
        <List facts={facts} />
      ) : (
        <FeatureList facts={facts} />
      )}
    </SectionShell>
  );
}
