/**
 * The Neon Coast skin's Skill Graph: a radar drawn as a lit sign (rings and spokes in a thin line, the person's
 * shape in pink, its edge thickened by two wider, fainter strokes instead of a blur) beside the table of areas
 * and scores. A focus graph, or fewer than three areas, has no radar: the table alone, each row with its bar.
 *
 * Same numbers as every other skin, passed through untouched; the rows carry `data-pf-domain` / `data-pf-score`
 * for the parity script, and the axes keep the snapshot's fixed order. Skills named under an area are the ones
 * a public page may list (45 or above), strongest first, as in the standard graph.
 */
import { useId, useMemo } from 'react';
import type { GraphDomain, GraphSnapshot, GraphVariant } from '../../../shared/portfolio';
import { cssVars } from '../../lib/ui';
import { DOT_R, PEAK_DOT_R, buildPlot, fmt, pct, topDomainId, visibleSkills } from '../graph/geometry';
import { asOfLabel, graphCaption, peaks, sourcesLine } from '../model';

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

interface Props {
  domains: GraphDomain[];
  variant: GraphVariant;
  graph: GraphSnapshot;
  /** Id of the panel's heading (the page's "Skills" title labels the section). */
  titleId: string;
}

export function CoastGraph({ domains, variant, graph, titleId }: Props) {
  const raw = useId().replace(/[^a-zA-Z0-9_-]/g, '');
  const plot = useMemo(() => buildPlot(domains.map((d) => d.score)), [domains]);
  const lit = useMemo(() => new Set(peaks(domains, 2).map((d) => d.id)), [domains]);
  if (domains.length === 0) return null;

  const radar = variant !== 'focus' && domains.length >= 3;
  const high = topDomainId(domains);
  const sources = sourcesLine(graph);
  const whole = domains.length === graph.domains.length && graph.skillCount > 0;

  return (
    <div className={radar ? 'nc-graph' : 'nc-graph nc-graph--list'}>
      <header className="nc-graph-head">
        <h2 id={titleId} className="nc-h">
          Skills
        </h2>
        <p className="nc-meta">
          Skill Graph, {asOfLabel(graph)}
          <br />
          {plural(domains.length, 'area', 'areas')}
          {whole ? `, ${plural(graph.skillCount, 'skill', 'skills')}` : ''}
        </p>
      </header>

      <div className="nc-graph-body">
        {radar && (
          <figure className="nc-radar-fig">
            <div className="nc-radar">
              <svg className="nc-radar-svg" viewBox={`0 0 ${plot.view.w} ${plot.view.h}`} aria-hidden="true" focusable="false">
                <path className="nc-ring" d={plot.rings} />
                <path className="nc-spoke" d={plot.spokes} />
                <path className="nc-shape-halo nc-shape-halo--wide" d={plot.poly} />
                <path className="nc-shape-halo" d={plot.poly} />
                <path className="nc-shape" d={plot.poly} />
                {domains.map((d, i) => (
                  <circle
                    key={d.id}
                    className={lit.has(d.id) ? 'nc-dot nc-dot--peak' : 'nc-dot'}
                    cx={fmt(plot.vertices[i].x)}
                    cy={fmt(plot.vertices[i].y)}
                    r={lit.has(d.id) ? PEAK_DOT_R + 2 : DOT_R + 2}
                  />
                ))}
              </svg>
              {domains.map((d, i) => (
                <div
                  key={d.id}
                  className="nc-axis"
                  aria-hidden="true"
                  data-side={plot.labels[i].side}
                  data-emph={lit.has(d.id) ? '' : undefined}
                  style={{ left: `${plot.labels[i].left}%`, top: `${plot.labels[i].top}%` }}
                >
                  <span className="nc-axis-name">{d.short || d.name}</span>
                  <span className="nc-axis-score">{d.score}</span>
                </div>
              ))}
            </div>
            <figcaption className="nc-graph-cap">{graphCaption(domains, variant)}</figcaption>
          </figure>
        )}

        <div className="nc-scores">
          <table className="nc-table" aria-describedby={`${raw}-cap`}>
            <caption id={`${raw}-cap`} className="nc-vh">
              Skill Graph scores out of 100
            </caption>
            <thead className="nc-vh">
              <tr>
                <th scope="col">Area</th>
                <th scope="col">Score</th>
              </tr>
            </thead>
            <tbody>
              {domains.map((d) => {
                const skills = visibleSkills(d, 3);
                return (
                  <tr key={d.id} className={d.id === high ? 'nc-row nc-row--high' : 'nc-row'} data-pf-domain={d.name} data-pf-score={d.score}>
                    <th scope="row">
                      <span className="nc-row-name">{d.name}</span>
                      {skills.length > 0 && <span className="nc-row-skills">{skills.map((s) => s.name).join(', ')}</span>}
                      <span className="nc-bar" aria-hidden="true" style={cssVars({ '--nc-pct': `${pct(d.score)}%` })} />
                    </th>
                    <td className="nc-row-score">{d.score}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {!radar && <p className="nc-graph-cap nc-graph-cap--list">{graphCaption(domains, variant)}</p>}
          {sources && <p className="nc-meta nc-graph-src">Scored by Leafout from {sources}</p>}
        </div>
      </div>
    </div>
  );
}
