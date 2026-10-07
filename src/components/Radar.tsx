/**
 * Radar — the centrepiece chart of the calm theme.
 *
 * Decorative SVG (rings, spokes, polygons, dots) under a layer of real HTML
 * buttons for the axis labels, a centre slot, a transparent hit layer for the
 * vertices, and a visually hidden data table.
 * Values tween along their spokes through a motion value; a change of axis set
 * crossfades two layers pivoting on the clicked vertex. Every stroke colour is
 * a token in radar.css, so the chart follows the ambient hue for free.
 */
import { forwardRef, useEffect, useImperativeHandle, useLayoutEffect, useMemo, useRef, useState } from 'react';
import type { KeyboardEvent, MouseEvent, ReactNode, RefObject } from 'react';
import {
  AnimatePresence,
  animate,
  motion,
  useMotionValue,
  useMotionValueEvent,
  useReducedMotion,
  useTransform,
} from 'motion/react';
import type { Variants } from 'motion/react';
import { RADAR_VIEW, angleOf, labelAnchor, pivotFor, pointAt, polygonPath, radarGeom, ringPath } from '../lib/radar';
import '../styles/radar.css';

/* ------------------------------------------------------------------ */
/* Public types                                                        */
/* ------------------------------------------------------------------ */

export interface RadarAxis {
  id: string;
  label: string;
  /** 0–100. */
  value: number;
  /** 0–100; the dashed target series when present on any axis. */
  secondary?: number;
  /** Free-form note carried for the caller (a tier, a source); not rendered by the radar. */
  meta?: string;
}

/** A vertex in CSS px relative to the wrapper's top-left; `bounds` is the wrapper size. */
export interface VertexGeom {
  x: number;
  y: number;
  /** Radians, clockwise from 12 o'clock. */
  angle: number;
  bounds: { w: number; h: number };
  /** The axis label's box (same coordinates), so a popover can stay clear of it. */
  label?: { x: number; y: number; w: number; h: number };
}

export interface RadarHandle {
  /** Move the roving tabindex to that axis and focus its button. */
  focusAxis(id: string): void;
}

export interface RadarProps {
  /** At least three axes, in a fixed order (never sorted by value). */
  axes: RadarAxis[];
  /** Identifies the axis set; changing it crossfades instead of tweening. */
  axisSetKey: string;
  series: { primary: string; secondary?: string };
  selectedId?: string | null;
  /**
   * How an axis button announces being the selected one: `pressed` (a toggle, `aria-pressed`) or
   * `expanded` (it opens a dialog, `aria-haspopup` + `aria-expanded`). Omit for axes that navigate.
   */
  axisState?: 'pressed' | 'expanded';
  hoveredId?: string | null;
  /** Axes to keep at full strength while `dim` is on. */
  relatedIds?: ReadonlySet<string>;
  /** Fade every vertex that is neither selected nor related. */
  dim?: boolean;
  /** The axis a set change pivots on (looked up in the old set, then the new). */
  pivotId?: string | null;
  /** Draw the first set from the centre over 900 ms (once per mount). */
  reveal?: boolean;
  ariaLabel: string;
  tableCaption: string;
  centre?: ReactNode;
  onSelect?: (id: string, geom: VertexGeom, meta: { viaKeyboard: boolean }) => void;
  onHover?: (id: string | null) => void;
  /** A click on the plot that hit no vertex, label or centre. */
  onBackgroundClick?: () => void;
  className?: string;
}

/* ------------------------------------------------------------------ */
/* Constants and helpers                                               */
/* ------------------------------------------------------------------ */

const EASE = [0.2, 0.8, 0.2, 1] as const;
const REVEAL_S = 0.9;
const TWEEN_S = 0.4;
const CROSSFADE_S = 0.4;
const CROSSFADE_REDUCED_S = 0.15;
const RING_LEVELS = [0.25, 0.5, 0.75, 1] as const;
const g = radarGeom();

type Pivot = { x: number; y: number };

function cx(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(' ');
}

/** Interpolate two 0–100 series at `t`. */
function lerpAll(from: number[], to: number[], t: number): number[] {
  return to.map((v, i) => {
    const a = from[i] ?? 0;
    return a + (v - a) * t;
  });
}

/** Polygon path for a 0–100 series. */
function pathOf(values100: number[]): string {
  return polygonPath(g, values100.map((v) => v / 100));
}

const originOf = (p: Pivot) => `${p.x}px ${p.y}px`;

/** Crossfade variants: scale pivots on the clicked vertex; reduced motion fades only. */
function layerVariants(reduced: boolean): Variants {
  return {
    enter: (p: Pivot) => ({ opacity: 0, scale: reduced ? 1 : 0.96, transformOrigin: originOf(p) }),
    idle: (p: Pivot) => ({ opacity: 1, scale: 1, transformOrigin: originOf(p) }),
    exit: (p: Pivot) => ({ opacity: 0, scale: reduced ? 1 : 1.04, transformOrigin: originOf(p) }),
  };
}

/** Wrapper border box plus its content box (the plot), which the padding hook `--radar-pad` insets. */
interface Box {
  w: number;
  h: number;
  ox: number;
  oy: number;
  iw: number;
  ih: number;
}
const ZERO_BOX: Box = { w: 0, h: 0, ox: 0, oy: 0, iw: 0, ih: 0 };

/**
 * Measures the wrapper with a ResizeObserver into a ref rather than state:
 * nothing in the render depends on pixels (positions are percentages of the
 * viewBox), so a resize must not re-render the chart. Read on click only.
 */
function useBoxRef(ref: RefObject<HTMLElement | null>): RefObject<Box> {
  const box = useRef<Box>(ZERO_BOX);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    // The callback runs once on observe and then on every size change.
    const ro = new ResizeObserver(([entry]) => {
      const c = entry.contentRect;
      box.current = { w: el.offsetWidth, h: el.offsetHeight, ox: c.x, oy: c.y, iw: c.width, ih: c.height };
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, [ref]);
  return box;
}

/* ------------------------------------------------------------------ */
/* One axis set: rings, spokes, polygons, dots, and the live tween      */
/* ------------------------------------------------------------------ */

interface LayerProps {
  axes: RadarAxis[];
  /** Draw from the centre on mount (the first set of a revealed radar). */
  reveal: boolean;
  reduced: boolean;
  hasSecondary: boolean;
  selectedId: string | null;
  hoveredId: string | null;
  relatedIds: ReadonlySet<string> | undefined;
  dim: boolean;
}

function Layer({ axes, reveal, reduced, hasSecondary, selectedId, hoveredId, relatedIds, dim }: LayerProps) {
  const n = axes.length;
  const values = axes.map((a) => a.value);
  const values2 = axes.map((a) => a.secondary ?? 0);
  const valuesKey = `${values.join(',')}/${values2.join(',')}`;

  // Captured once: the reveal decision must not flip when the parent re-renders mid-draw.
  const drawFromCentre = useRef(reveal && !reduced).current;

  // Progress 0 → 1 of the current tween; `from`/`to` are read inside the transformer
  // so retargeting never has to rebuild the derived motion value.
  const p = useMotionValue(drawFromCentre ? 0 : 1);
  const from = useRef<number[]>(drawFromCentre ? values.map(() => 0) : values);
  const to = useRef<number[]>(values);
  // The target series never draws from the centre: it fades in instead.
  const from2 = useRef<number[]>(values2);
  const to2 = useRef<number[]>(values2);

  const pathRef = useRef<SVGPathElement>(null);
  const path2Ref = useRef<SVGPathElement>(null);
  const d = useTransform(p, (t) => pathOf(lerpAll(from.current, to.current, t)));
  const d2 = useTransform(p, (t) => pathOf(lerpAll(from2.current, to2.current, t)));
  useMotionValueEvent(d, 'change', (v) => pathRef.current?.setAttribute('d', v));
  useMotionValueEvent(d2, 'change', (v) => path2Ref.current?.setAttribute('d', v));
  // React writes `d` once; every later frame goes straight to the attribute.
  const [initialD] = useState(() => d.get());
  const [initialD2] = useState(() => d2.get());

  const seenKey = useRef<string | null>(null);

  // How long the dots wait before fading in: the tween the effect below is about to start.
  // Keyed on the values (not every render) so a hover re-render cannot restart the CSS animation.
  const dotsDelayMs = useMemo(() => {
    if (reduced) return 0;
    if (seenKey.current === null) return drawFromCentre ? REVEAL_S * 1000 : 0;
    return TWEEN_S * 1000;
  }, [valuesKey]);

  useEffect(() => {
    const changed = seenKey.current !== null && seenKey.current !== valuesKey;
    seenKey.current = valuesKey;
    if (changed) {
      // Retarget from whatever is drawn right now so an interrupted tween never jumps.
      const t = p.get();
      from.current = lerpAll(from.current, to.current, t);
      to.current = values;
      from2.current = lerpAll(from2.current, to2.current, t);
      to2.current = values2;
    }
    const seconds = changed ? TWEEN_S : drawFromCentre ? REVEAL_S : 0;
    if (reduced || seconds === 0) {
      // `jump` only notifies on a real change, so write the final paths by hand.
      p.jump(1);
      pathRef.current?.setAttribute('d', pathOf(values));
      path2Ref.current?.setAttribute('d', pathOf(values2));
      return;
    }
    p.set(0);
    const controls = animate(p, 1, { duration: seconds, ease: EASE });
    return () => controls.stop();
  }, [valuesKey]);

  const hot = (id: string) => id === hoveredId || id === selectedId;

  return (
    <>
      {RING_LEVELS.map((level) => (
        <path key={level} className="radar-ring" d={ringPath(g, n, level)} />
      ))}
      {axes.map((a, i) => {
        const end = pointAt(g, i, n, 1);
        return (
          <line
            key={a.id}
            className={cx('radar-spoke', hot(a.id) && 'is-hot')}
            x1={g.cx}
            y1={g.cy}
            x2={end.x}
            y2={end.y}
          />
        );
      })}
      {hasSecondary && (
        <path ref={path2Ref} className={cx('radar-poly-2', drawFromCentre && 'is-reveal')} d={initialD2} />
      )}
      <path ref={pathRef} className="radar-poly" d={initialD} />
      {/* Re-keyed on every values change so the fade restarts after the tween. */}
      <g key={valuesKey} className="radar-dots" style={{ animationDelay: `${dotsDelayMs}ms` }}>
        {hasSecondary &&
          axes.map((a, i) => {
            if (a.secondary === undefined) return null;
            const pt = pointAt(g, i, n, a.secondary / 100);
            return <circle key={a.id} className="radar-dot-2" cx={pt.x} cy={pt.y} r={2.5} />;
          })}
        {axes.map((a, i) => {
          const pt = pointAt(g, i, n, a.value / 100);
          const sel = a.id === selectedId;
          const rel = relatedIds?.has(a.id) ?? false;
          return (
            <circle
              key={a.id}
              className={cx('radar-dot', sel && 'is-sel', rel && 'is-rel', dim && !sel && !rel && 'is-dim')}
              cx={pt.x}
              cy={pt.y}
              r={sel ? 4 : 3}
            />
          );
        })}
      </g>
    </>
  );
}

/* ------------------------------------------------------------------ */
/* Radar                                                               */
/* ------------------------------------------------------------------ */

/** The set on screen, remembered so the next set change can find its pivot. */
interface Shown {
  key: string;
  axes: RadarAxis[];
  pivot: Pivot;
}

export const Radar = forwardRef<RadarHandle, RadarProps>(function Radar(
  {
    axes,
    axisSetKey,
    series,
    selectedId = null,
    axisState,
    hoveredId = null,
    relatedIds,
    dim = false,
    pivotId = null,
    reveal = false,
    ariaLabel,
    tableCaption,
    centre,
    onSelect,
    onHover,
    onBackgroundClick,
    className,
  },
  ref,
) {
  const n = axes.length;
  const reduced = useReducedMotion() ?? false;
  const wrapRef = useRef<HTMLDivElement>(null);
  const box = useBoxRef(wrapRef);
  const buttons = useRef(new Map<string, HTMLButtonElement>());
  const variants = useMemo(() => layerVariants(reduced), [reduced]);

  // Only the layer mounted with the radar reveals; later sets crossfade in at full value.
  const revealFirst = useRef(reveal);
  useEffect(() => {
    revealFirst.current = false;
  }, []);

  // Pivot for the crossfade, decided the moment the set key changes
  // (storing information from previous renders, per the React docs).
  const [shown, setShown] = useState<Shown>(() => ({ key: axisSetKey, axes, pivot: { x: g.cx, y: g.cy } }));
  let current = shown;
  if (shown.key !== axisSetKey) {
    current = { key: axisSetKey, axes, pivot: pivotFor(shown.axes, axes, pivotId, g) };
    setShown(current);
  }
  const pivot = current.pivot;

  // Roving tabindex, reset to the first axis whenever the set changes.
  const [rove, setRove] = useState({ key: axisSetKey, i: 0 });
  const activeIndex = rove.key === axisSetKey ? Math.min(rove.i, Math.max(n - 1, 0)) : 0;

  const vertexGeom = (i: number, a: RadarAxis): VertexGeom => {
    const b = box.current;
    const pt = pointAt(g, i, n, a.value / 100);
    const geom: VertexGeom = {
      x: b.ox + (pt.x / RADAR_VIEW) * b.iw,
      y: b.oy + (pt.y / RADAR_VIEW) * b.ih,
      angle: angleOf(i, n),
      bounds: { w: b.w, h: b.h },
    };
    const wrap = wrapRef.current;
    const btn = buttons.current.get(a.id);
    if (wrap && btn) {
      const wr = wrap.getBoundingClientRect();
      const lr = btn.getBoundingClientRect();
      geom.label = { x: lr.left - wr.left, y: lr.top - wr.top, w: lr.width, h: lr.height };
    }
    return geom;
  };

  const select = (i: number, viaKeyboard: boolean) => {
    const a = axes[i];
    if (a) onSelect?.(a.id, vertexGeom(i, a), { viaKeyboard });
  };

  const focusIndex = (i: number) => {
    const a = axes[i];
    if (!a) return;
    setRove({ key: axisSetKey, i });
    buttons.current.get(a.id)?.focus();
  };

  useImperativeHandle(ref, () => ({
    focusAxis(id) {
      const i = axes.findIndex((a) => a.id === id);
      if (i >= 0) focusIndex(i);
    },
  }));

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const target = e.target as HTMLElement;
    if (target !== e.currentTarget && !target.classList.contains('radar-axis')) return;
    let next: number;
    switch (e.key) {
      case 'ArrowRight':
      case 'ArrowDown':
        next = (activeIndex + 1) % n;
        break;
      case 'ArrowLeft':
      case 'ArrowUp':
        next = (activeIndex - 1 + n) % n;
        break;
      case 'Home':
        next = 0;
        break;
      case 'End':
        next = n - 1;
        break;
      default:
        return;
    }
    e.preventDefault();
    focusIndex(next);
  };

  const onClick = (e: MouseEvent<HTMLDivElement>) => {
    if ((e.target as Element).closest('.radar-hit, .radar-axis, .radar-centre')) return;
    onBackgroundClick?.();
  };

  if (n < 3) {
    return <div className={cx('radar radar-empty', className)}>Not enough data for a radar</div>;
  }

  const hasSecondary = axes.some((a) => a.secondary !== undefined);
  const crossfade = {
    duration: reduced ? CROSSFADE_REDUCED_S : CROSSFADE_S,
    ease: EASE,
    // The pivot is set, never interpolated.
    transformOrigin: { duration: 0 },
  };

  return (
    <div
      ref={wrapRef}
      className={cx('radar', className)}
      role="group"
      aria-label={ariaLabel}
      tabIndex={-1}
      onKeyDown={onKeyDown}
      onClick={onClick}
    >
      <svg className="radar-svg" viewBox={`0 0 ${RADAR_VIEW} ${RADAR_VIEW}`} aria-hidden="true" focusable="false">
        <AnimatePresence initial={false} mode="sync" custom={pivot}>
          <motion.g
            key={axisSetKey}
            custom={pivot}
            variants={variants}
            initial="enter"
            animate="idle"
            exit="exit"
            transition={crossfade}
            style={{ transformBox: 'view-box' }}
          >
            <Layer
              axes={axes}
              reveal={revealFirst.current}
              reduced={reduced}
              hasSecondary={hasSecondary}
              selectedId={selectedId}
              hoveredId={hoveredId}
              relatedIds={relatedIds}
              dim={dim}
            />
          </motion.g>
        </AnimatePresence>
      </svg>

      {/* Re-keyed per set so the incoming labels fade in. */}
      <div className="radar-axes" key={axisSetKey}>
        {axes.map((a, i) => {
          const anchor = labelAnchor(g, i, n);
          const sel = a.id === selectedId;
          const hov = a.id === hoveredId;
          const rel = relatedIds?.has(a.id) ?? false;
          const label =
            `${a.label}, ${Math.round(a.value)} out of 100` +
            (a.secondary !== undefined && series.secondary ? `, ${series.secondary} ${Math.round(a.secondary)}` : '');
          return (
            <button
              key={a.id}
              type="button"
              ref={(el) => {
                if (el) buttons.current.set(a.id, el);
                else buttons.current.delete(a.id);
              }}
              className={cx('radar-axis', sel && 'is-sel', hov && 'is-hov', rel && 'is-rel', dim && !sel && !rel && 'is-dim')}
              style={{ left: `${(anchor.x / RADAR_VIEW) * 100}%`, top: `${(anchor.y / RADAR_VIEW) * 100}%` }}
              data-side={anchor.side}
              tabIndex={i === activeIndex ? 0 : -1}
              aria-label={label}
              aria-pressed={axisState === 'pressed' ? sel : undefined}
              aria-haspopup={axisState === 'expanded' ? 'dialog' : undefined}
              aria-expanded={axisState === 'expanded' ? sel : undefined}
              onClick={(e) => select(i, e.detail === 0)}
              onFocus={() => setRove({ key: axisSetKey, i })}
              onPointerEnter={() => onHover?.(a.id)}
              onPointerLeave={() => onHover?.(null)}
            >
              <span className="radar-lbl">{a.label}</span>
              <span className="numeral radar-val">{Math.round(a.value)}</span>
            </button>
          );
        })}
      </div>

      {centre != null && <div className="radar-centre">{centre}</div>}

      {/*
       * Hit targets: a transparent layer over everything, so a low-value vertex under the centre
       * slot still takes the click, and an outgoing set (inside the crossfade) never can.
       */}
      <svg className="radar-hits" viewBox={`0 0 ${RADAR_VIEW} ${RADAR_VIEW}`} aria-hidden="true" focusable="false">
        {axes.map((a, i) => {
          const pt = pointAt(g, i, n, a.value / 100);
          return (
            <circle
              key={a.id}
              className="radar-hit"
              cx={pt.x}
              cy={pt.y}
              r={14}
              onClick={() => select(i, false)}
              onPointerEnter={() => onHover?.(a.id)}
              onPointerLeave={() => onHover?.(null)}
            />
          );
        })}
      </svg>

      {/* The wrapper, not the table, is the hidden box: a table keeps its min-content width and would scroll the page sideways. */}
      <div className="sr-only">
        <table>
          <caption>{tableCaption}</caption>
        <thead>
          <tr>
            <th scope="col">Axis</th>
            <th scope="col">{series.primary}</th>
            {series.secondary && <th scope="col">{series.secondary}</th>}
          </tr>
        </thead>
          <tbody>
            {axes.map((a) => (
              <tr key={a.id}>
                <th scope="row">{a.label}</th>
                <td>{Math.round(a.value)}</td>
                {series.secondary && <td>{a.secondary === undefined ? '' : Math.round(a.secondary)}</td>}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
});

/* ------------------------------------------------------------------ */
/* RadarGlyph — a 20 px sparkline for rail rows                        */
/* ------------------------------------------------------------------ */

export function RadarGlyph({ values, size = 20, className }: { values: number[]; size?: number; className?: string }) {
  const n = values.length;
  return (
    <svg
      className={cx('radar-glyph', className)}
      width={size}
      height={size}
      viewBox={`0 0 ${RADAR_VIEW} ${RADAR_VIEW}`}
      aria-hidden="true"
      focusable="false"
    >
      {n >= 3 ? (
        <>
          <path className="radar-glyph-ring" d={ringPath(g, n, 1)} />
          <path className="radar-glyph-poly" d={polygonPath(g, values.map((v) => v / 100))} />
        </>
      ) : (
        <circle className="radar-glyph-ring" cx={g.cx} cy={g.cy} r={g.r} />
      )}
    </svg>
  );
}
