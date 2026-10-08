/**
 * SkillGraph: the portfolio's signature block. One entry point (`GraphRenderArgs` in, markup out) that draws
 * the graph block's variant: peaks, halo, ledger, stellar, atlas, spotlight or focus.
 *
 * Rules the variants share: scores are shown exactly as the snapshot carries them; axes keep the snapshot's
 * fixed order; captions are templates over the values (`graphCaption`); a radar needs at least three domains,
 * so fewer fall back to the focus rendering; every variant keeps a visually hidden data table whose rows
 * carry `data-pf-domain` and `data-pf-score`.
 */
import { useId } from 'react';
import type { GraphRenderArgs } from '../types';
import '../../styles/portfolio-graph.css';
import { Atlas, Focus, Halo, Ledger, Peaks, Spotlight, Stellar } from './variants';
import type { GraphCtx } from './parts';

export function SkillGraph(args: GraphRenderArgs) {
  const { spec, graph, domains, content, placement } = args;
  const capId = useId();
  if (domains.length === 0) return null;

  // A radar of fewer than three axes is a line: show the focus rendering instead.
  const variant = args.variant !== 'focus' && domains.length < 3 ? 'focus' : args.variant;

  // The proof block prints the same source facts; never show them twice on one page.
  const proofShown = spec.blocks.some((b) => b.kind === 'proof' && b.visible && b.public);

  const ctx: GraphCtx = {
    spec,
    graph,
    domains,
    content,
    placement,
    variant,
    showFacts: !proofShown,
    showStats: spec.graph.stats,
    capId,
  };

  return (
    <div className="pg" data-variant={variant} data-placement={placement} data-type={spec.style.type}>
      {variant === 'peaks' && <Peaks ctx={ctx} />}
      {variant === 'halo' && <Halo ctx={ctx} />}
      {variant === 'ledger' && <Ledger ctx={ctx} />}
      {variant === 'stellar' && <Stellar ctx={ctx} />}
      {variant === 'atlas' && <Atlas ctx={ctx} />}
      {variant === 'spotlight' && <Spotlight ctx={ctx} />}
      {variant === 'focus' && <Focus ctx={ctx} />}
    </div>
  );
}
