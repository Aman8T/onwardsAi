/**
 * A radar (or stellar chart) with its axis labels, centre slot, phone ledger and "Why NN?" popover.
 *
 * The drawing is decorative SVG; the labels are real buttons (roving tabindex like the app radar: arrow
 * keys, Home and End move between them) laid over it by percentage of the viewBox, so nothing depends on
 * measured pixels. A label opens the popover only when its domain has skills that may be listed; the
 * others are plain text. Axes keep the snapshot's fixed order.
 */
import { useMemo, useRef, useState } from 'react';
import type { KeyboardEvent, ReactNode } from 'react';
import { cssVars } from '../../lib/ui';
import type { GraphDomain } from '../../../shared/portfolio';
import { AxisPopover, useAxisPopover } from './AxisPopover';
import {
  DOT_R,
  PEAK_DOT_R,
  SPOT_DOT_R,
  buildPlot,
  fmt,
  spokePath,
  viewBoxAttr,
} from './geometry';
import { PhoneLedger, hasListedSkills } from './parts';

export interface PlotStageProps {
  domains: GraphDomain[];
  /** `radar`: connected polygon; `stellar`: one spear per axis, no polygon. */
  mode?: 'radar' | 'stellar';
  /** Domains drawn with a bigger dot and a bold label. */
  emphasis?: ReadonlySet<string>;
  /** Domain to spotlight: its spoke in the accent, a big dot, the other labels quiet. */
  spotId?: string;
  /** Slot at the centre of the plot (the mastery bubble). */
  centre?: ReactNode;
  /** Maximum width in px. */
  max?: number;
  /** Render the ledger rows that replace the labels' job on phones. */
  phone?: boolean;
  ariaLabel?: string;
}

const NONE: ReadonlySet<string> = new Set();

export function PlotStage({
  domains,
  mode = 'radar',
  emphasis = NONE,
  spotId,
  centre,
  max = 500,
  phone = true,
  ariaLabel = 'Skill Graph areas',
}: PlotStageProps) {
  const n = domains.length;
  const pop = useAxisPopover();
  const plot = useMemo(() => buildPlot(domains.map((d) => d.score)), [domains]);
  const spotIndex = spotId === undefined ? -1 : domains.findIndex((d) => d.id === spotId);

  // Roving tabindex over the labels that are buttons.
  const liveIds = domains.filter(hasListedSkills).map((d) => d.id);
  const [rove, setRove] = useState(0);
  const active = Math.min(rove, Math.max(liveIds.length - 1, 0));
  const buttons = useRef(new Map<string, HTMLButtonElement>());

  const focusAt = (k: number) => {
    const id = liveIds[k];
    if (id === undefined) return;
    setRove(k);
    buttons.current.get(id)?.focus();
  };

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const target = e.target;
    if (!(target instanceof HTMLElement) || !target.classList.contains('pg-axis')) return;
    const count = liveIds.length;
    if (count < 2) return;
    let next: number;
    switch (e.key) {
      case 'ArrowRight':
      case 'ArrowDown':
        next = (active + 1) % count;
        break;
      case 'ArrowLeft':
      case 'ArrowUp':
        next = (active - 1 + count) % count;
        break;
      case 'Home':
        next = 0;
        break;
      case 'End':
        next = count - 1;
        break;
      default:
        return;
    }
    e.preventDefault();
    focusAt(next);
  };

  return (
    <div className="pg-stage" ref={pop.stageRef} style={cssVars({ '--pg-max': `${max}px` })}>
      <div className="pg-plot">
        <div className="pg-plot-in">
          <svg className="pg-svg" viewBox={viewBoxAttr(plot.view)} aria-hidden="true" focusable="false">
            <path className="pg-ring" d={plot.rings} />
            <path className="pg-spoke" d={plot.spokes} />
            {spotIndex >= 0 && <path className="pg-spoke pg-spoke-hot" d={spokePath(spotIndex, n)} />}
            {mode === 'stellar' ? (
              <>
                {/* Two stacked fills read as the denser wash the spears need; the second also strokes. */}
                <path className="pg-spears-wash pg-grow" d={plot.spears} />
                <path className="pg-spears pg-grow" d={plot.spears} />
              </>
            ) : (
              <path className="pg-poly pg-grow" d={plot.poly} />
            )}
            <g className="pg-fade">
              {domains.map((d, i) => {
                const spot = d.id === spotId;
                const lit = spot || emphasis.has(d.id);
                const v = plot.vertices[i];
                return (
                  <circle
                    key={d.id}
                    className={lit ? 'pg-dot-peak' : 'pg-dot'}
                    cx={fmt(v.x)}
                    cy={fmt(v.y)}
                    r={spot ? SPOT_DOT_R : lit ? PEAK_DOT_R : DOT_R}
                  />
                );
              })}
            </g>
          </svg>

          <div className="pg-axes pg-fade" role="group" aria-label={ariaLabel} onKeyDown={onKeyDown}>
            {domains.map((d, i) => {
              const at = plot.labels[i];
              const lit = d.id === spotId || emphasis.has(d.id);
              const quiet = spotId !== undefined && d.id !== spotId;
              const style = { left: `${at.left}%`, top: `${at.top}%` };
              const inner = (
                <>
                  <span className="pg-axis-name">{d.short || d.name}</span>
                  <span className="pg-axis-val">{d.score}</span>
                </>
              );
              if (!hasListedSkills(d)) {
                return (
                  <div
                    key={d.id}
                    className="pg-axis"
                    data-side={at.side}
                    data-emph={lit ? '' : undefined}
                    data-quiet={quiet ? '' : undefined}
                    style={style}
                  >
                    {inner}
                  </div>
                );
              }
              return (
                <button
                  key={d.id}
                  type="button"
                  ref={(el) => {
                    if (el) buttons.current.set(d.id, el);
                    else buttons.current.delete(d.id);
                  }}
                  className="pg-axis"
                  data-side={at.side}
                  data-emph={lit ? '' : undefined}
                  data-quiet={quiet ? '' : undefined}
                  data-pg-trigger=""
                  style={style}
                  tabIndex={liveIds[active] === d.id ? 0 : -1}
                  aria-label={`${d.name}, ${d.score} out of 100`}
                  aria-haspopup="dialog"
                  aria-expanded={pop.state?.id === d.id}
                  onFocus={() => setRove(liveIds.indexOf(d.id))}
                  onClick={(e) => pop.toggle(d.id, e.currentTarget, e.detail === 0)}
                >
                  {inner}
                </button>
              );
            })}
          </div>

          {centre}
        </div>
      </div>

      {phone && <PhoneLedger domains={domains} emphasis={spotId ? new Set([spotId]) : emphasis} pop={pop} />}
      <AxisPopover api={pop} domains={domains} />
    </div>
  );
}
