/**
 * Proof strip: 2-4 real counts from the connected sources ("389" · "GitHub stars"). Never invented:
 * the values and labels are the snapshot's `content.stats`.
 */
import { BlockFrame, EmptyBlock, usePf } from './SectionShell';
import type { BlockProps } from './SectionShell';

export function Proof({ block, index }: BlockProps) {
  const { content } = usePf();
  const stats = content.stats.slice(0, 4);
  if (!stats.length) return <EmptyBlock block={block} index={index} label="Public proof" />;
  return (
    <BlockFrame block={block} index={index} ariaLabel="Public proof" className="pf-proof">
      <ul className="pf-stats">
        {stats.map((s, i) => (
          <li key={`${s.sourceId}-${i}`} className="pf-stat">
            <span className="pf-stat-v">{s.value}</span>
            <span className="pf-stat-l">{s.label}</span>
          </li>
        ))}
      </ul>
    </BlockFrame>
  );
}
