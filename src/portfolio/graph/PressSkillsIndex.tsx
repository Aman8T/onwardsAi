/**
 * The newspaper skin's Skill Graph: "The Skills Index" (a printed radar with a halftone-dot fill beside an
 * AREA / SCORE table with dotted leaders, the top score flagged "▲ high" in text) and, for focus graphs or
 * fewer than three domains, "The Skills Watch" (a short table of focus areas, no radar). Same numbers as
 * the standard skin; rows carry `data-pf-domain` / `data-pf-score` for the parity script. Press tokens only.
 */
import { useId, useMemo } from 'react';
import type { GraphDomain, GraphSnapshot } from '../../../shared/portfolio';
import { cssVars } from '../../lib/ui';
import '../../styles/portfolio-graph.css';
import { asOfLabel, graphCaption, peaks, sourcesLine } from '../model';
import type { PressIndexArgs } from '../types';
import { DOT_R, HALFTONE, PEAK_DOT_R, buildPlot, fmt, pct, topDomainId } from './geometry';

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

/** "Skill Graph · 6 areas · 21 skills" (the skill count only when no domain is hidden). */
function footLine(graph: GraphSnapshot, domains: GraphDomain[]): string {
  const areas = plural(domains.length, 'area', 'areas');
  const skills =
    domains.length === graph.domains.length && graph.skillCount > 0 ? ` · ${plural(graph.skillCount, 'skill', 'skills')}` : '';
  return `Skill Graph · ${areas}${skills}`;
}

function SkillsIndex({ domains, variant, graph }: PressIndexArgs) {
  const raw = useId();
  const titleId = `pgx-t${raw.replace(/[^a-zA-Z0-9_-]/g, '')}`;
  const patternId = `pgx-h${raw.replace(/[^a-zA-Z0-9_-]/g, '')}`;
  const plot = useMemo(() => buildPlot(domains.map((d) => d.score)), [domains]);
  const lit = useMemo(() => new Set(peaks(domains, 2).map((d) => d.id)), [domains]);
  const high = topDomainId(domains);

  return (
    <section className="pgx" aria-labelledby={titleId}>
      <header className="pgx-head">
        <h2 id={titleId} className="pgx-title">
          The Skills Index
        </h2>
        <p className="pgx-sub">Closing scores · {asOfLabel(graph)}</p>
      </header>
      <div className="pgx-body">
        <figure className="pgx-fig">
          <div className="pgx-radar">
            <svg className="pgx-svg" viewBox={`0 0 ${plot.view.w} ${plot.view.h}`} aria-hidden="true" focusable="false">
              <defs>
                <pattern id={patternId} width={HALFTONE.size} height={HALFTONE.size} patternUnits="userSpaceOnUse">
                  {HALFTONE.dots.map((d) => (
                    <circle key={`${d.cx}-${d.cy}`} className="pgx-ink" cx={d.cx} cy={d.cy} r={d.r} />
                  ))}
                </pattern>
              </defs>
              <path className="pgx-ring" d={plot.rings} />
              <path className="pgx-spoke" d={plot.spokes} />
              <path className="pgx-poly" d={plot.poly} fill={`url(#${patternId})`} />
              {domains.map((d, i) => (
                <circle
                  key={d.id}
                  className={lit.has(d.id) ? 'pgx-dot-peak' : 'pgx-ink'}
                  cx={fmt(plot.vertices[i].x)}
                  cy={fmt(plot.vertices[i].y)}
                  r={lit.has(d.id) ? PEAK_DOT_R : DOT_R}
                />
              ))}
            </svg>
            {domains.map((d, i) => (
              <div
                key={d.id}
                className="pgx-lbl"
                aria-hidden="true"
                data-side={plot.labels[i].side}
                data-emph={lit.has(d.id) ? '' : undefined}
                style={{ left: `${plot.labels[i].left}%`, top: `${plot.labels[i].top}%` }}
              >
                <span className="pgx-lbl-name">{d.short || d.name}</span>
                <span className="pgx-lv">{d.score}</span>
              </div>
            ))}
          </div>
          <figcaption className="pgx-cap">{graphCaption(domains, variant)}</figcaption>
        </figure>

        <div className="pgx-tw">
          <table className="pgx-tbl">
            <caption className="pgx-vh">Skill Graph scores out of 100</caption>
            <thead>
              <tr>
                <th scope="col">Area</th>
                <td aria-hidden="true" />
                <th scope="col" className="pgx-n">
                  Score
                </th>
              </tr>
            </thead>
            <tbody>
              {domains.map((d) => (
                <tr key={d.id} data-pf-domain={d.name} data-pf-score={d.score}>
                  <th scope="row">{d.name}</th>
                  <td className="pgx-ld" aria-hidden="true">
                    <span className="pgx-dots" />
                  </td>
                  <td className="pgx-n">
                    {d.id === high && (
                      <span className="pgx-flag">
                        <span aria-hidden="true">▲ </span>high
                      </span>
                    )}
                    {d.score}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
      <footer className="pgx-foot">
        <p>{footLine(graph, domains)}</p>
        <p>Scored by Tayyari from {plural(graph.sources.length, 'source', 'sources')}</p>
      </footer>
    </section>
  );
}

function SkillsWatch({ domains, graph }: PressIndexArgs) {
  const raw = useId();
  const titleId = `pgx-w${raw.replace(/[^a-zA-Z0-9_-]/g, '')}`;
  const from = sourcesLine(graph);

  return (
    <section className="pgx pgx-watch" aria-labelledby={titleId}>
      <header className="pgx-head">
        <h2 id={titleId} className="pgx-title">
          <svg className="pgx-tri" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" aria-hidden="true" focusable="false">
            <path d="M12 4 L21 19 H3 Z" />
          </svg>
          The Skills Watch
        </h2>
        <p className="pgx-sub">Areas of focus · {asOfLabel(graph)}</p>
      </header>
      <p className="pgx-new">New listing · first scores{from ? ` from ${from}` : ''}</p>
      <div className="pgx-wbody">
        <table className="pgx-tbl">
          <caption className="pgx-vh">Skill Graph scores out of 100</caption>
          <thead>
            <tr>
              <th scope="col">Area</th>
              <td aria-hidden="true" />
              <th scope="col" className="pgx-n">
                Score
              </th>
            </tr>
          </thead>
          <tbody>
            {domains.map((d) => (
              <tr key={d.id} className="pgx-r" style={cssVars({ '--pgx-pct': `${pct(d.score)}%` })} data-pf-domain={d.name} data-pf-score={d.score}>
                <th scope="row">{d.name}</th>
                <td className="pgx-ld" aria-hidden="true">
                  <span className="pgx-dots" />
                </td>
                <td className="pgx-n">{d.score}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

/** The newspaper's Skill Graph panel (unit H places it in the right rail). */
export function PressSkillsIndex(args: PressIndexArgs) {
  if (args.domains.length === 0) return null;
  return args.variant === 'focus' || args.domains.length < 3 ? <SkillsWatch {...args} /> : <SkillsIndex {...args} />;
}
