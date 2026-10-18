/**
 * Where you stand: the role's gaps as bars (today → what the role expects, from the graph), the biggest marked, one
 * "why" per gap behind a tap with its evidence chips, and one quiet "Already strong" line. Drawn at once from the
 * `gaps` event; the headline and the whys fill in when `standing` lands.
 */
import type { PrepareEvidence, PrepareGap, PreparePlan } from '../../../shared/prepare';
import { IconCheck, IconMinus, IconPlus } from '../../components/Icons';
import { clamp } from '../../lib/ui';
import { numbersLine } from '../plan';
import { EvidenceChips } from './Evidence';
import { SkeletonLine } from './Skeletons';

interface StandingProps {
  plan: PreparePlan;
  evidence: Map<string, PrepareEvidence>;
  /** The headline and the whys are still on their way. */
  waiting: boolean;
  openGap: string | null;
  onToggleGap: (name: string) => void;
  /** "Help me with just this": a plan for one gap. Absent on a plan that is already for one gap. */
  onJustThis?: (name: string) => void;
}

export function Standing({ plan, evidence, waiting, openGap, onToggleGap, onJustThis }: StandingProps) {
  const many = plan.gaps.length > 1;
  return (
    <section id="pp-stand" className="panel pp-block" aria-labelledby="pp-stand-h">
      <div className="pp-head">
        <h2 id="pp-stand-h" className="pp-h">
          Where you stand
        </h2>
        <span className="numeral pp-meta">today → what the role expects</span>
      </div>

      {plan.headline ? (
        <p className="serif pp-headline">{plan.headline}</p>
      ) : waiting ? (
        <p className="pp-headline-wait" role="status">
          <span className="sr-only">Reading your evidence…</span>
          <SkeletonLine width="88%" tall />
          <SkeletonLine width="46%" tall />
        </p>
      ) : null}

      {plan.gaps.length > 0 ? (
        <div className="pp-gaps">
          {plan.gaps.map((g) => (
            <GapRow
              key={g.name}
              gap={g}
              evidence={evidence}
              waiting={waiting}
              open={openGap === g.name}
              many={many}
              onToggle={() => onToggleGap(g.name)}
              onJustThis={onJustThis}
            />
          ))}
        </div>
      ) : (
        <p className="pp-muted">Nothing on the graph stands between you and this role.</p>
      )}

      {plan.strong.length > 0 && (
        <p className="pp-strong">
          <IconCheck size={16} strokeWidth={1.5} className="pp-strong-ic" />
          <span>
            Already strong:{' '}
            {plan.strong.map((s, i) => (
              <span key={s.name}>
                {i > 0 && ', '}
                {s.name} <span className="numeral pp-num">{s.current}</span>
                <span className="pp-muted"> · expects </span>
                <span className="numeral pp-num">{s.target}</span>
              </span>
            ))}
            .
          </span>
        </p>
      )}
    </section>
  );
}

interface GapRowProps {
  gap: PrepareGap;
  evidence: Map<string, PrepareEvidence>;
  waiting: boolean;
  open: boolean;
  /** More than one gap: the biggest is marked. */
  many: boolean;
  onToggle: () => void;
  onJustThis?: (name: string) => void;
}

function GapRow({ gap, evidence, waiting, open, many, onToggle, onJustThis }: GapRowProps) {
  const bodyId = `pp-gap-${gap.name.replace(/[^a-z0-9]+/gi, '-').toLowerCase()}`;
  const biggest = gap.biggest && many;
  return (
    <div className="pp-gap">
      <button
        type="button"
        className="pp-gap-btn"
        aria-expanded={open}
        aria-controls={bodyId}
        aria-label={`${gap.name}: ${gap.current} today, the role expects ${gap.target}${biggest ? ', the biggest gap' : ''}`}
        onClick={onToggle}
      >
        <span className="pp-gap-name">
          <span>{gap.name}</span>
          {biggest && <span className="pp-gap-tag">the biggest gap</span>}
        </span>
        <span className="pp-bar" aria-hidden="true">
          <span className="pp-bar-cur" style={{ transform: `scaleX(${clamp(gap.current, 0, 100) / 100})` }} />
          <span className="pp-bar-tgt" style={{ left: `${clamp(gap.target, 0, 100)}%` }} />
        </span>
        <span className="numeral pp-gap-nums" aria-hidden="true">
          {gap.current} → {gap.target}
        </span>
        <span className="pp-gap-chev" aria-hidden="true">
          {open ? <IconMinus size={16} strokeWidth={1.5} /> : <IconPlus size={16} strokeWidth={1.5} />}
        </span>
      </button>
      <div id={bodyId} className="pp-gap-body" hidden={!open}>
        {open &&
          (gap.why ? (
            <p className="pp-why">{gap.why}</p>
          ) : waiting ? (
            <p className="pp-why-wait" role="status">
              <span className="sr-only">Looking for the line behind this gap…</span>
              <SkeletonLine width="72%" />
            </p>
          ) : (
            <p className="pp-why">{numbersLine(gap)}</p>
          ))}
        {open && (
          <EvidenceChips ids={gap.evidenceIds ?? []} evidence={evidence}>
            {onJustThis && (
              <button type="button" className="btn btn-quiet pp-link" onClick={() => onJustThis(gap.name)}>
                Help me with just this
              </button>
            )}
          </EvidenceChips>
        )}
      </div>
    </div>
  );
}
