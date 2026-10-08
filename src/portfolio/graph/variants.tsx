/**
 * The seven Skill Graph variants, ported from the approved boards (GraphVariants, Main, Senior, Uneven,
 * Student, Mobile): peaks, halo, ledger, stellar, atlas, spotlight and focus. Each takes the same context and
 * draws the same data; `SkillGraph` picks one. Only `focus` and the ledger rows are ranked or listed by
 * value, and only for rows, never for axes.
 */
import { useMemo } from 'react';
import type { ReactNode } from 'react';
import type { GraphDomain } from '../../../shared/portfolio';
import { graphCaption, growing, peaks, sourcesLine } from '../model';
import { AxisPopover, useAxisPopover } from './AxisPopover';
import { PlotStage } from './PlotStage';
import { StatsStrip } from './StatsStrip';
import {
  DOT_R,
  LEDGER_DOT_SCALE,
  MINI_DOT_SCALE,
  MINI_LABEL_R,
  MINI_VIEW,
  PEAK_DOT_R,
  TIGHT_VIEW,
  buildPlot,
  fmt,
  glyphView,
  rankDomains,
  topDomainId,
  topSkills,
  viewBoxAttr,
  visibleSkills,
} from './geometry';
import { Caption, DataTable, Kicker, ValueLine, hasListedSkills, metaLine } from './parts';
import type { GraphCtx } from './parts';

interface VariantProps {
  ctx: GraphCtx;
}

/** The stats slot, when the spec switches it on. */
function statsOf(ctx: GraphCtx, layout: 'row' | 'list' = 'row'): ReactNode {
  if (!ctx.showStats) return null;
  return <StatsStrip stats={ctx.content.stats} practice={ctx.content.practice} showFacts={ctx.showFacts} layout={layout} />;
}

function Bubble({ score, label }: { score: number; label: string }) {
  return (
    <div className="pg-centre" aria-hidden="true">
      <span className="pg-centre-n">{score}</span>
      <span className="pg-centre-l">{label}</span>
    </div>
  );
}

const areaCount = (n: number) => `${n} ${n === 1 ? 'area' : 'areas'}`;

/* ───────────────────────── peaks ───────────────────────── */

/**
 * Radar with the two strongest axes emphasised. Inside the split hero it is just the figure (the proof
 * block carries the stats); as a section it sits beside a "Strongest skills" list and the stats.
 */
export function Peaks({ ctx }: VariantProps) {
  const { domains, placement } = ctx;
  const emphasis = useMemo(() => new Set(peaks(domains, 2).map((d) => d.id)), [domains]);
  const hero = placement === 'hero';
  const figure = (
    <figure className="pg-fig" aria-labelledby={ctx.capId}>
      <PlotStage domains={domains} emphasis={emphasis} max={500} />
      <Caption ctx={ctx} />
      <DataTable domains={domains} />
      {hero ? statsOf(ctx) : null}
    </figure>
  );
  if (hero) return figure;

  const skills = topSkills(domains, 4);
  return (
    <div className="pg-split">
      {figure}
      <div className="pg-aside pg-in" data-late="">
        <Kicker />
        {skills.length > 0 && (
          <div className="pg-aside-block">
            <p className="pg-aside-title">Strongest skills</p>
            <ul className="pg-skill-list">
              {skills.map((s) => (
                <li key={s.id} className="pg-skill-li">
                  <span className="pg-skill-top">
                    <span className="pg-skill-name">{s.name}</span>
                    <span className="pg-skill-val">{s.score}</span>
                  </span>
                  <ValueLine score={s.score} />
                </li>
              ))}
            </ul>
          </div>
        )}
        {statsOf(ctx)}
      </div>
    </div>
  );
}

/* ───────────────────────── halo / stellar ───────────────────────── */

function Centred({ ctx, stellar }: VariantProps & { stellar: boolean }) {
  const { domains, graph, placement } = ctx;
  const m = graph.mastery;
  return (
    <div className="pg-centred">
      {placement === 'section' && <Kicker centred />}
      <figure className="pg-fig" aria-labelledby={ctx.capId}>
        <PlotStage
          domains={domains}
          mode={stellar ? 'stellar' : 'radar'}
          max={stellar ? 500 : 560}
          centre={!stellar && m ? <Bubble score={m.score} label="/ mastery" /> : undefined}
        />
        <Caption
          ctx={ctx}
          extra={
            !stellar && m ? (
              <span className="pg-mastery">
                Role mastery {m.score} · {m.role}
              </span>
            ) : undefined
          }
        />
        <DataTable domains={domains} />
      </figure>
      {statsOf(ctx)}
    </div>
  );
}

/** Large centred radar with the role-mastery numeral in the middle (balanced graphs). */
export function Halo({ ctx }: VariantProps) {
  return <Centred ctx={ctx} stellar={false} />;
}

/** One tapering spear per axis, no polygon: no area distortion (6–8 axes on Editorial and Technical pages). */
export function Stellar({ ctx }: VariantProps) {
  return <Centred ctx={ctx} stellar />;
}

/* ───────────────────────── ledger ───────────────────────── */

/** A compact radar beside ranked rows; the rows are the buttons that open the popover. */
export function Ledger({ ctx }: VariantProps) {
  const { domains, graph, placement } = ctx;
  const pop = useAxisPopover();
  const ranked = useMemo(() => rankDomains(domains), [domains]);
  const plot = useMemo(() => buildPlot(domains.map((d) => d.score), { view: TIGHT_VIEW }), [domains]);
  const skillsShown = domains.length === graph.domains.length ? graph.skillCount : 0;

  return (
    <div className="pg-ledger-wrap">
      {placement === 'section' && <Kicker />}
      <figure className="pg-fig pg-fig-left" aria-labelledby={ctx.capId}>
        <div className="pg-ledger" ref={pop.stageRef}>
          <div className="pg-ledger-glyph">
            <svg className="pg-ledger-svg" viewBox={viewBoxAttr(TIGHT_VIEW)} aria-hidden="true" focusable="false">
              <path className="pg-ring" d={plot.rings} />
              <path className="pg-spoke" d={plot.spokes} />
              <path className="pg-poly pg-grow" d={plot.poly} />
              <g className="pg-fade">
                {domains.map((d, i) => (
                  <circle key={d.id} className="pg-dot" cx={fmt(plot.vertices[i].x)} cy={fmt(plot.vertices[i].y)} r={DOT_R * LEDGER_DOT_SCALE} />
                ))}
              </g>
            </svg>
            <span className="pg-ledger-meta">
              {areaCount(domains.length)}
              {skillsShown > 0 ? ` · ${skillsShown} ${skillsShown === 1 ? 'skill' : 'skills'}` : ''}
            </span>
          </div>
          <ol className="pg-rows">
            {ranked.map((d) => {
              const body = (
                <>
                  <span className="pg-row-top">
                    <span className="pg-row-name">{d.name}</span>
                    <span className="pg-row-val">{d.score}</span>
                  </span>
                  <ValueLine score={d.score} />
                </>
              );
              return (
                <li key={d.id}>
                  {hasListedSkills(d) ? (
                    <button
                      type="button"
                      className="pg-row"
                      data-pg-trigger=""
                      aria-haspopup="dialog"
                      aria-expanded={pop.state?.id === d.id}
                      onClick={(e) => pop.toggle(d.id, e.currentTarget, e.detail === 0)}
                    >
                      {body}
                    </button>
                  ) : (
                    <div className="pg-row">{body}</div>
                  )}
                </li>
              );
            })}
          </ol>
          <AxisPopover api={pop} domains={domains} />
        </div>
        <Caption ctx={ctx} left />
        <DataTable domains={domains} />
      </figure>
      {statsOf(ctx)}
    </div>
  );
}

/* ───────────────────────── atlas ───────────────────────── */

function AtlasCard({ domain }: { domain: GraphDomain }) {
  const skills = visibleSkills(domain, 6);
  const mini = skills.length >= 3;
  const plot = mini
    ? buildPlot(skills.map((s) => s.score), { view: MINI_VIEW, labelRadius: MINI_LABEL_R, centredLabels: true })
    : null;
  const top = skills.reduce((best, s, i) => (s.score > skills[best].score ? i : best), 0);
  return (
    <article className="pg-card">
      <div className="pg-card-head">
        <h4 className="pg-card-name">{domain.name}</h4>
        <span className="pg-card-val">{domain.score}</span>
      </div>
      {plot && (
        <div className="pg-mini" aria-hidden="true">
          <svg className="pg-mini-svg" viewBox={viewBoxAttr(MINI_VIEW)} focusable="false">
            <path className="pg-ring" d={plot.rings} />
            <path className="pg-spoke" d={plot.spokes} />
            <path className="pg-poly pg-grow" d={plot.poly} />
            <g className="pg-fade">
              {skills.map((s, i) => (
                <circle
                  key={s.id}
                  className={i === top ? 'pg-dot-peak' : 'pg-dot'}
                  cx={fmt(plot.vertices[i].x)}
                  cy={fmt(plot.vertices[i].y)}
                  r={(i === top ? PEAK_DOT_R : DOT_R) * MINI_DOT_SCALE}
                />
              ))}
            </g>
          </svg>
          {skills.map((s, i) => (
            <span
              key={s.id}
              className="pg-mini-n pg-fade"
              data-emph={i === top ? '' : undefined}
              style={{ left: `${plot.labels[i].left}%`, top: `${plot.labels[i].top}%` }}
            >
              {i + 1}
            </span>
          ))}
        </div>
      )}
      <ul className="pg-card-list">
        {skills.map((s, i) => (
          <li key={s.id} className="pg-card-li">
            <span className="pg-card-top">
              <span className="pg-card-idx" data-emph={mini && i === top ? '' : undefined}>
                {mini ? i + 1 : ''}
              </span>
              <span className="pg-card-skill">{s.name}</span>
              <span className="pg-card-sv" data-emph={mini && i === top ? '' : undefined}>
                {s.score}
              </span>
            </span>
            <ValueLine score={s.score} className="pg-vl-indent" />
          </li>
        ))}
      </ul>
    </article>
  );
}

/** The domain radar plus one small multiple per domain: its skills, numbered clockwise (rich profiles). */
export function Atlas({ ctx }: VariantProps) {
  const { domains, graph } = ctx;
  const emphasis = useMemo(() => new Set(peaks(domains, 2).map((d) => d.id)), [domains]);
  const floor = Math.min(...domains.map((d) => d.score));
  const m = graph.mastery;
  const cards = domains.filter((d) => visibleSkills(d).length > 0);
  return (
    <div className="pg-atlas">
      <div className="pg-atlas-head">
        <Kicker />
        <span className="pg-meta">{metaLine(graph, domains)}</span>
      </div>
      <figure className="pg-atlas-fig" aria-labelledby={ctx.capId}>
        <div className="pg-atlas-plot">
          <PlotStage
            domains={domains}
            emphasis={emphasis}
            max={620}
            centre={m ? <Bubble score={m.score} label="mastery" /> : undefined}
          />
        </div>
        <figcaption className="pg-atlas-cap pg-in" data-late="">
          <span id={ctx.capId} className="pg-cap pg-cap-lg">
            {graphCaption(domains, ctx.variant)}
          </span>
          {floor >= 55 && (
            <span className="pg-balance">
              All {domains.length} areas score {floor} or higher.
            </span>
          )}
          {m && (
            <span className="pg-mastery">
              Role mastery {m.score} · {m.role}
            </span>
          )}
          {statsOf(ctx, 'list')}
        </figcaption>
        <DataTable domains={domains} />
      </figure>
      {cards.length > 0 && (
        <div className="pg-atlas-by">
          <h3 className="pg-kicker">Skills by area</h3>
          <div className="pg-cards">
            {cards.map((d) => (
              <AtlasCard key={d.id} domain={d} />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

/* ───────────────────────── spotlight ───────────────────────── */

/** One axis spotlit (uneven graphs); the next areas are framed "Growing". */
export function Spotlight({ ctx }: VariantProps) {
  const { domains, placement } = ctx;
  const top = peaks(domains, 1)[0];
  const next = growing(domains, 2);
  const figure = (
    <figure className="pg-fig" aria-labelledby={ctx.capId}>
      <PlotStage domains={domains} spotId={top.id} max={500} />
      <Caption ctx={ctx} />
      <DataTable domains={domains} />
    </figure>
  );
  const detail = (
    <>
      <p className="pg-strong">
        <span className="pg-eyebrow">Strongest:</span>
        <span className="pg-strong-name">{top.name}</span>
        <span className="pg-strong-val">{top.score}</span>
      </p>
      {next.length > 0 && (
        <p className="pg-growing">
          <span className="pg-eyebrow">Growing:</span>
          {next.map((g, k) => (
            <span key={g.id} className="pg-growing-item">
              {g.name} <span className="pg-growing-val">{g.score}</span>
              {k < next.length - 1 ? ' ·' : ''}
            </span>
          ))}
        </p>
      )}
    </>
  );
  if (placement === 'hero') {
    return (
      <div className="pg-spot-hero">
        {figure}
        <div className="pg-spot-under pg-in" data-late="">
          {detail}
        </div>
        {statsOf(ctx)}
      </div>
    );
  }
  return (
    <div className="pg-split">
      {figure}
      <div className="pg-aside pg-spot pg-in" data-late="">
        <Kicker />
        {detail}
        {statsOf(ctx)}
      </div>
    </div>
  );
}

/* ───────────────────────── focus ───────────────────────── */

/** Sparse or brand-new graphs: no radar, "Areas of focus" as rows with value lines and a small numbered glyph. */
export function Focus({ ctx }: VariantProps) {
  const { domains, graph } = ctx;
  const pop = useAxisPopover();
  const n = domains.length;
  const view = useMemo(() => glyphView(n), [n]);
  const plot = useMemo(() => (n >= 3 ? buildPlot(domains.map((d) => d.score), { view }) : null), [domains, n, view]);
  const topId = topDomainId(domains);
  const strong = topSkills(domains, 4);
  const from = sourcesLine(graph);

  return (
    <div className="pg-focus">
      <div className="pg-focus-card">
        <div className="pg-focus-head">
          <Kicker>Areas of focus</Kicker>
          <p className="pg-focus-lede">
            {from ? (
              <>
                <span className="pg-nowrap">Just getting started ·</span> scored from {from}
              </>
            ) : (
              'Just getting started'
            )}
          </p>
        </div>
        <div className="pg-focus-body">
          <figure className="pg-glyph-fig" aria-labelledby={ctx.capId}>
            {plot && (
              <div className="pg-glyph" style={{ aspectRatio: `${view.w} / ${view.h}` }} aria-hidden="true">
                <svg className="pg-svg" viewBox={viewBoxAttr(view)} focusable="false">
                  <path className="pg-ring" d={plot.rings} />
                  <path className="pg-spoke" d={plot.spokes} />
                  <path className="pg-poly pg-grow" d={plot.poly} />
                  <g className="pg-fade">
                    {domains.map((d, i) => (
                      <circle
                        key={d.id}
                        className={d.id === topId ? 'pg-dot-peak' : 'pg-dot'}
                        cx={fmt(plot.vertices[i].x)}
                        cy={fmt(plot.vertices[i].y)}
                        r={d.id === topId ? PEAK_DOT_R : DOT_R}
                      />
                    ))}
                  </g>
                </svg>
                {domains.map((d, i) => (
                  <span
                    key={d.id}
                    className="pg-glyph-n pg-fade"
                    data-side={plot.labels[i].side}
                    style={{ left: `${plot.labels[i].left}%`, top: `${plot.labels[i].top}%` }}
                  >
                    {i + 1}
                  </span>
                ))}
              </div>
            )}
            <Caption ctx={ctx} left />
            <DataTable domains={domains} />
          </figure>

          <div className="pg-focus-list" ref={pop.stageRef}>
            <ol className="pg-focus-rows">
              {domains.map((d, i) => {
                const body = (
                  <>
                    <span className="pg-focus-top">
                      <span className="pg-focus-l">
                        <span className="pg-focus-idx">{i + 1}</span>
                        <span className="pg-focus-name">{d.name}</span>
                      </span>
                      <span className="pg-focus-val">{d.score}</span>
                    </span>
                    <ValueLine score={d.score} />
                  </>
                );
                return (
                  <li key={d.id}>
                    {hasListedSkills(d) ? (
                      <button
                        type="button"
                        className="pg-focus-row"
                        data-pg-trigger=""
                        aria-haspopup="dialog"
                        aria-expanded={pop.state?.id === d.id}
                        onClick={(e) => pop.toggle(d.id, e.currentTarget, e.detail === 0)}
                      >
                        {body}
                      </button>
                    ) : (
                      <div className="pg-focus-row">{body}</div>
                    )}
                  </li>
                );
              })}
            </ol>
            {strong.length > 0 && (
              <p className="pg-focus-strong">
                Strongest skills so far:{' '}
                <span className="pg-focus-skills">{strong.map((s) => `${s.name} ${s.score}`).join(' · ')}</span>
              </p>
            )}
            <p className="pg-focus-note">Scores out of 100</p>
            <AxisPopover api={pop} domains={domains} />
          </div>
        </div>
      </div>
      {statsOf(ctx)}
    </div>
  );
}
