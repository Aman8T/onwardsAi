/**
 * Small pieces every Skill Graph variant shares: the caption and provenance line, the visually hidden data
 * table (what the parity script reads), the value line, the section kicker and the phone ledger.
 */
import type { ReactNode } from 'react';
import type { GraphDomain, GraphSnapshot, GraphVariant, PortfolioContent, PortfolioSpec } from '../../../shared/portfolio';
import { asOfLabel, graphCaption, sourcesLine } from '../model';
import type { AxisPopoverApi } from './AxisPopover';
import { listedSkills, pct } from './geometry';

/** What a variant needs to render itself. */
export interface GraphCtx {
  spec: PortfolioSpec;
  graph: GraphSnapshot;
  /** The shown domains, in the snapshot's fixed order. */
  domains: GraphDomain[];
  content: PortfolioContent;
  placement: 'hero' | 'section';
  variant: GraphVariant;
  /** The stats strip's source facts may be drawn (false when the page's proof block already prints them). */
  showFacts: boolean;
  /** `spec.graph.stats`: the stats slot is switched on. */
  showStats: boolean;
  /** Id of the element holding the caption (the figure's label). */
  capId: string;
}

/** True when a domain has at least one skill that may be listed, so its label can open the popover. */
export function hasListedSkills(d: GraphDomain): boolean {
  return listedSkills(d, 1).length > 0;
}

/* ───────────────────────── text ───────────────────────── */

/** "Skill Graph · 6 areas · 21 skills · as of 8 Oct 2026 · scored from resume, GitHub and portfolio". */
export function metaLine(graph: GraphSnapshot, domains: GraphDomain[]): string {
  const areas = `${domains.length} ${domains.length === 1 ? 'area' : 'areas'}`;
  // The snapshot's skill count covers every domain; with some hidden it would overstate what is shown.
  const skills =
    domains.length === graph.domains.length && graph.skillCount > 0
      ? ` · ${graph.skillCount} ${graph.skillCount === 1 ? 'skill' : 'skills'}`
      : '';
  const from = sourcesLine(graph);
  return `Skill Graph · ${areas}${skills} · as of ${asOfLabel(graph)}${from ? ` · scored from ${from}` : ''}`;
}

export function Caption({ ctx, extra, left = false }: { ctx: GraphCtx; extra?: ReactNode; left?: boolean }) {
  const { domains, graph, variant, capId } = ctx;
  return (
    <figcaption className={left ? 'pg-figcap pg-figcap-left' : 'pg-figcap'}>
      <span id={capId} className="pg-cap">
        {graphCaption(domains, variant)}
      </span>
      {extra}
      <span className="pg-meta">{metaLine(graph, domains)}</span>
    </figcaption>
  );
}

export function Kicker({ children = 'Skill Graph', centred = false }: { children?: ReactNode; centred?: boolean }) {
  return <h2 className={centred ? 'pg-kicker pg-kicker-c' : 'pg-kicker'}>{children}</h2>;
}

/* ───────────────────────── data table ───────────────────────── */

/** Every shown domain and its score, in the fixed axis order; hidden from view, read by screen readers and the parity script. */
export function DataTable({ domains }: { domains: GraphDomain[] }) {
  return (
    <div className="pg-vh">
      <table>
        <caption>Skill Graph scores out of 100</caption>
        <thead>
          <tr>
            <th scope="col">Area</th>
            <th scope="col">Score</th>
          </tr>
        </thead>
        <tbody>
          {domains.map((d) => (
            <tr key={d.id} data-pf-domain={d.name} data-pf-score={d.score}>
              <th scope="row">{d.name}</th>
              <td>{d.score}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/* ───────────────────────── value line ───────────────────────── */

/** A 1px track with a 3px bar: the quiet bar chart of ledger rows. */
export function ValueLine({ score, className }: { score: number; className?: string }) {
  return (
    <span className={className ? `pg-vl ${className}` : 'pg-vl'} aria-hidden="true">
      <span className="pg-vl-track" />
      <span className="pg-vl-bar" style={{ width: `${pct(score)}%` }} />
    </span>
  );
}

/* ───────────────────────── phone ledger ───────────────────────── */

/**
 * Under 560px the radar shrinks and these rows appear under it (the approved phone board): name, value
 * line, number, 44px tall. A row opens the same popover as the axis label when the domain has skills.
 */
export function PhoneLedger({
  domains,
  emphasis,
  pop,
}: {
  domains: GraphDomain[];
  emphasis: ReadonlySet<string>;
  pop: AxisPopoverApi;
}) {
  return (
    <div className="pg-phone">
      <p className="pg-phone-k">Scores by area · out of 100</p>
      <ul className="pg-phone-list" aria-label="Skill Graph scores by area">
        {domains.map((d) => {
          const lit = emphasis.has(d.id);
          const body = (
            <>
              <span className="pg-ph-name" data-emph={lit ? '' : undefined}>
                {d.name}
              </span>
              <ValueLine score={d.score} />
              <span className="pg-ph-val" data-emph={lit ? '' : undefined}>
                {d.score}
              </span>
            </>
          );
          return (
            <li key={d.id}>
              {hasListedSkills(d) ? (
                <button
                  type="button"
                  className="pg-ph-row"
                  data-pg-trigger=""
                  aria-haspopup="dialog"
                  aria-expanded={pop.state?.id === d.id}
                  onClick={(e) => pop.toggle(d.id, e.currentTarget, e.detail === 0)}
                >
                  {body}
                </button>
              ) : (
                <div className="pg-ph-row">{body}</div>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
