/**
 * Experience: one entry per `experience` fact, bullets from its `exp:<id>` slot (else the fact's own
 * highlights). Variants: timeline (dated spine), ledger (years | role | highlights, hairline rows),
 * thenNow (current work in full, everything before it compressed into one entry).
 */
import type { Fact } from '../../../shared/portfolio';
import { experienceLines, factsOf, formatPeriod } from '../model';
import { EmptyBlock, MoreButton, SectionShell, cx, fmtYears, useLimit, usePf, useSlotEditor } from './SectionShell';
import type { BlockProps } from './SectionShell';

/** The entry's bullet lines, or the editor for its slot. */
function Lines({ f, className }: { f: Fact; className?: string }) {
  const { content } = usePf();
  const editor = useSlotEditor(`exp:${f.id}`);
  if (editor) return <>{editor}</>;
  const lines = experienceLines(content, f);
  if (!lines.length) return null;
  return (
    <ul className={cx('pf-bullets', className)}>
      {lines.map((line, i) => (
        <li key={i}>{line}</li>
      ))}
    </ul>
  );
}

function Title({ f, className }: { f: Fact; className?: string }) {
  return (
    <h3 className={cx('pf-title', className)}>
      {f.title}
      {f.org ? <span className="pf-org"> · {f.org}</span> : null}
    </h3>
  );
}

function Sub({ f }: { f: Fact }) {
  const text = [f.location, f.detail].filter(Boolean).join(' · ');
  return text ? <p className="pf-sub">{text}</p> : null;
}

/* ───────────── timeline ───────────── */

function Timeline({ facts }: { facts: Fact[] }) {
  const lim = useLimit(facts);
  return (
    <>
      <ol className="pf-tl">
        {lim.visible.map((f, i) => (
          <li key={f.id} className="pf-tl-item">
            <p className="pf-when">{formatPeriod(f)}</p>
            <div className="pf-tl-body">
              <span className={cx('pf-dot', (f.current || i === 0) && 'pf-dot--on')} aria-hidden="true" />
              <Title f={f} />
              <Sub f={f} />
              <Lines f={f} />
            </div>
          </li>
        ))}
      </ol>
      <MoreButton {...lim} />
    </>
  );
}

/* ───────────── ledger ───────────── */

function Ledger({ facts }: { facts: Fact[] }) {
  const lim = useLimit(facts);
  return (
    <>
      <ol className="pf-lg">
        {lim.visible.map((f) => (
          <li key={f.id} className="pf-lg-row">
            <p className="pf-when">{formatPeriod(f)}</p>
            <div className="pf-lg-id">
              <Title f={f} />
              <Sub f={f} />
            </div>
            <div className="pf-lg-lines">
              <Lines f={f} />
            </div>
          </li>
        ))}
      </ol>
      <MoreButton {...lim} />
    </>
  );
}

/* ───────────── then / now ───────────── */

function ThenNow({ facts }: { facts: Fact[] }) {
  const current = facts.filter((f) => f.current);
  const now = current.length ? current : facts.slice(0, 1);
  const before = facts.filter((f) => !now.includes(f));
  const lim = useLimit(before);

  let span = '';
  if (before.length) {
    const starts = before.map((f) => f.start).filter((s): s is string => !!s).sort();
    const ends = before.map((f) => f.end ?? f.start).filter((s): s is string => !!s).sort();
    span = formatPeriod({ ...before[0], start: starts[0], end: ends[ends.length - 1], current: false });
  }

  return (
    <div className="pf-tn">
      <div className="pf-tn-group">
        <p className="pf-group">
          <span className="pf-dot pf-dot--on pf-dot--inline" aria-hidden="true" />
          Now
        </p>
        {now.map((f) => (
          <article key={f.id} className="pf-tn-now">
            <p className="pf-when">{formatPeriod(f)}</p>
            <div className="pf-tn-main">
              <Title f={f} className="pf-title--lg" />
              <Sub f={f} />
              <Lines f={f} />
            </div>
          </article>
        ))}
      </div>
      {before.length ? (
        <div className="pf-tn-group">
          <p className="pf-group">
            <span className="pf-dot pf-dot--inline" aria-hidden="true" />
            Before
          </p>
          <article className="pf-tn-before">
            <p className="pf-when">{span}</p>
            <div className="pf-tn-main">
              {lim.visible.map((f) => (
                <div key={f.id} className="pf-tn-row">
                  <Title f={f} className="pf-title--sm" />
                  <Sub f={f} />
                  <Lines f={f} className="pf-bullets--muted" />
                </div>
              ))}
              <MoreButton {...lim} />
            </div>
          </article>
        </div>
      ) : null}
    </div>
  );
}

/* ───────────── block ───────────── */

export function Experience({ block, index }: BlockProps) {
  const { content } = usePf();
  const facts = factsOf(content, 'experience');
  if (!facts.length) return <EmptyBlock block={block} index={index} label="Experience" />;

  const years = content.person.yearsExperience;
  const meta = [
    years && years > 0 ? `${fmtYears(years)} ${years === 1 ? 'year' : 'years'}` : '',
    `${facts.length} ${facts.length === 1 ? 'role' : 'roles'}`,
  ]
    .filter(Boolean)
    .join(' · ');

  return (
    <SectionShell block={block} index={index} label="Experience" meta={meta}>
      {block.variant === 'ledger' ? (
        <Ledger facts={facts} />
      ) : block.variant === 'thenNow' ? (
        <ThenNow facts={facts} />
      ) : (
        <Timeline facts={facts} />
      )}
    </SectionShell>
  );
}
