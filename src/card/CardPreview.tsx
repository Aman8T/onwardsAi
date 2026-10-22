/**
 * The card drawn in the app's own colours: what the share sheet shows before sharing is on (and what it falls back to
 * if the real image cannot load). It is the same card as the server's image, drawn from the same leaf geometry
 * (`lib/radar.ts`): the areas in the graph's order, their 0-100 scores, up to three named skills, one role as a lighter
 * dashed outline, and the line that every score rests on cited evidence. Fewer than three areas make no shape, so it
 * is bars and no leaf. It follows the page's colours (tokens), so the theme choice shows only on the real image.
 *
 * The leaf and the data breathe like every radar in the app (radar.css; still under reduced motion). The card that
 * goes into a README never moves.
 */
import { useMemo } from 'react';
import type { CardArea, CardSkill } from '../../shared/card';
import { angleOf, pointAt, polygonPath, radarGeom, radarLeaf, ringPath } from '../lib/radar';
import { cardLabel, dayLabel, hasShape, shownAreas } from './format';
import '../styles/radar.css';
import '../styles/card.css';

const g = radarGeom();
const RING_LEVELS = [0.25, 0.5, 0.75, 1] as const;
/** The plot is 600 wide; the wide card's rim labels need room on both sides, so its box is wider than it is tall. */
const VIEW_BOX_MD = '-150 -45 900 690';
const VIEW_BOX_SM = '-40 -40 680 680';
const LABEL_R = 262;

export interface CardPreviewProps {
  areas: CardArea[];
  /** The skills named on the card (at most three), with their scores. */
  skills: CardSkill[];
  /** The role drawn as the lighter outline; needs `roleTarget` on the areas. */
  roleTitle?: string;
  size: 'md' | 'sm';
  /** The snapshot's time (ISO); today when absent. */
  updatedAt?: string;
}

const clampPct = (v: number) => Math.max(0, Math.min(100, v));

export function CardPreview({ areas: all, skills, roleTitle, size, updatedAt }: CardPreviewProps) {
  const areas = useMemo(() => shownAreas(all), [all]);
  const md = size === 'md';
  const n = areas.length;
  const shape = hasShape(areas);
  const compare = md && !!roleTitle && shape && areas.some((a) => a.roleTarget !== undefined);

  const geo = useMemo(() => {
    if (!shape) return null;
    const you = areas.map((a) => clampPct(a.score) / 100);
    const role = areas.map((a) => clampPct(a.roleTarget ?? a.score) / 100);
    return {
      leaf: radarLeaf(g, n),
      rings: RING_LEVELS.map((l) => ringPath(g, n, l)),
      spokes: areas.map((_, i) => {
        const end = pointAt(g, i, n, 1);
        return { x: end.x, y: end.y };
      }),
      you: polygonPath(g, you),
      role: polygonPath(g, role),
      dots: you.map((v, i) => pointAt(g, i, n, v)),
      labels: areas.map((a, i) => {
        const ang = angleOf(i, n);
        const cos = Math.cos(ang);
        return {
          text: a.short || a.name,
          x: g.cx + cos * LABEL_R,
          y: g.cy + Math.sin(ang) * LABEL_R,
          anchor: Math.abs(cos) < 0.2 ? 'middle' : cos > 0 ? 'start' : 'end',
        } as const;
      }),
    };
  }, [areas, n, shape]);

  // the wide card lists every area; the small one its three strongest
  const rows = md ? areas : areas.slice().sort((a, b) => b.score - a.score).slice(0, 3);
  const named = md ? skills.slice(0, 3) : [];
  const foot = md ? `Every score rests on cited evidence · Updated ${dayLabel(updatedAt)}` : `Cited from evidence · ${dayLabel(updatedAt)}`;

  return (
    <div className="cs-card" data-size={size} role="img" aria-label={cardLabel(areas)}>
      <div className="cs-card-top">
        <span className="cs-card-kicker">Skill shape</span>
        <span className="cs-card-site">leafout.in</span>
      </div>

      <div className="cs-card-body">
        {geo && (
          <svg className="cs-card-chart" viewBox={md ? VIEW_BOX_MD : VIEW_BOX_SM} aria-hidden="true" focusable="false">
            {geo.leaf && (
              <g className="radar-leaf">
                <path className="radar-leaf-blade" d={geo.leaf.blade} />
                <path className="radar-leaf-stalk" d={geo.leaf.stalk} />
              </g>
            )}
            {geo.rings.map((d, i) => (
              <path key={i} className="radar-ring" d={d} />
            ))}
            {geo.spokes.map((p, i) => (
              <line key={i} className="radar-spoke" x1={g.cx} y1={g.cy} x2={p.x} y2={p.y} />
            ))}
            <g className="radar-heart">
              {compare && <path className="radar-poly-2" d={geo.role} />}
              <path className={compare ? 'radar-poly has-target' : 'radar-poly'} d={geo.you} />
              {geo.dots.map((p, i) => (
                <circle key={i} className="radar-dot" cx={p.x} cy={p.y} r={9} />
              ))}
            </g>
            {md &&
              geo.labels.map((l, i) => (
                <text key={i} className="cs-card-axis" x={l.x} y={l.y} textAnchor={l.anchor} dominantBaseline="middle">
                  {l.text}
                </text>
              ))}
          </svg>
        )}

        <div className={['cs-card-rows', !shape && 'is-bars', rows.length > 6 && 'is-dense'].filter(Boolean).join(' ')}>
          {rows.map((a) => (
            <div key={a.id} className="cs-card-row">
              <span className="cs-card-name">{a.name}</span>
              <span className="numeral cs-card-score">{Math.round(a.score)}</span>
              {!shape && (
                <span className="cs-card-bar" aria-hidden="true">
                  <span style={{ width: `${clampPct(a.score)}%` }} />
                </span>
              )}
            </div>
          ))}
          {compare && roleTitle && (
            <div className="cs-card-key">
              <span>
                <i className="cs-key-now" aria-hidden="true" />
                Now
              </span>
              <span>
                <i className="cs-key-role" aria-hidden="true" />
                {roleTitle} expects
              </span>
            </div>
          )}
        </div>
      </div>

      {named.length > 0 && (
        <div className="cs-card-skills">
          {named.map((s) => (
            <span key={s.id} className="cs-card-skill">
              {s.name} <span className="numeral">{Math.round(s.score)}</span>
            </span>
          ))}
        </div>
      )}

      <div className="cs-card-foot">{foot}</div>
    </div>
  );
}
