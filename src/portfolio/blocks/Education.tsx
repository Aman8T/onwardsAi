/**
 * Education: degree, institution, years. Variants: row (details with the period on the right; two or more
 * entries sit side by side) and compact (one line each). The grade appears only with `privacy.grades`.
 */
import type { Fact } from '../../../shared/portfolio';
import { factsOf, formatPeriod } from '../model';
import { EmptyBlock, MoreButton, SectionShell, cx, useLimit, usePf } from './SectionShell';
import type { BlockProps } from './SectionShell';

/** "CGPA 7.9 / 10" -> { label: "CGPA", value: "7.9", out: "10" }; anything else stays text. */
function gradeParts(grade: string): { label: string; value: string; out: string } | undefined {
  const m = /^(.*?)(\d+(?:\.\d+)?)\s*\/\s*(\d+(?:\.\d+)?)\s*$/.exec(grade.trim());
  return m ? { label: m[1].replace(/[:\s]+$/, ''), value: m[2], out: m[3] } : undefined;
}

function Grade({ grade }: { grade: string }) {
  const parts = gradeParts(grade);
  if (!parts) return <span className="pf-grade-text">{grade}</span>;
  return (
    <span className="pf-grade">
      <span className="pf-grade-v">
        {parts.value}
        <span className="pf-grade-out"> / {parts.out}</span>
      </span>
      {parts.label ? <span className="pf-grade-l">{parts.label}</span> : null}
    </span>
  );
}

function Row({ f, grades, several }: { f: Fact; grades: boolean; several: boolean }) {
  const place = [f.org, f.location].filter(Boolean).join(', ');
  const period = formatPeriod(f);
  return (
    <li className={cx('pf-edu-item', several && 'pf-edu-item--col')}>
      <div className="pf-edu-main">
        {period && several ? <span className="pf-edu-when">{period}</span> : null}
        <h3 className="pf-edu-title">{f.title}</h3>
        {place ? <p className="pf-edu-org">{place}</p> : null}
        {f.detail ? <p className="pf-edu-detail">{f.detail}</p> : null}
      </div>
      {!several && (period || (grades && f.grade)) ? (
        <div className="pf-edu-side">
          {period ? <span className="pf-edu-when">{period}</span> : null}
          {grades && f.grade ? <Grade grade={f.grade} /> : null}
        </div>
      ) : null}
      {several && grades && f.grade ? <Grade grade={f.grade} /> : null}
    </li>
  );
}

function Compact({ facts, grades }: { facts: Fact[]; grades: boolean }) {
  const lim = useLimit(facts);
  return (
    <>
      <ul className="pf-edu-compact">
        {lim.visible.map((f) => {
          const parts = [f.org, formatPeriod(f), grades ? f.grade : undefined].filter(Boolean).join(' · ');
          return (
            <li key={f.id} className="pf-edu-line">
              <h3 className="pf-edu-title pf-edu-title--sm">{f.title}</h3>
              {parts ? <span className="pf-edu-meta">{parts}</span> : null}
            </li>
          );
        })}
      </ul>
      <MoreButton {...lim} />
    </>
  );
}

function Rows({ facts, grades }: { facts: Fact[]; grades: boolean }) {
  const lim = useLimit(facts);
  const several = facts.length > 1;
  return (
    <>
      <ul className={cx('pf-edu', several && 'pf-edu--cols')}>
        {lim.visible.map((f) => (
          <Row key={f.id} f={f} grades={grades} several={several} />
        ))}
      </ul>
      <MoreButton {...lim} />
    </>
  );
}

export function Education({ block, index }: BlockProps) {
  const { content, spec } = usePf();
  const facts = factsOf(content, 'education');
  if (!facts.length) return <EmptyBlock block={block} index={index} label="Education" />;
  return (
    <SectionShell block={block} index={index} label="Education">
      {block.variant === 'compact' ? (
        <Compact facts={facts} grades={spec.privacy.grades} />
      ) : (
        <Rows facts={facts} grades={spec.privacy.grades} />
      )}
    </SectionShell>
  );
}
