/**
 * Open to (opt-in): a small hairline note with one or two role titles, from `content.openTo`.
 */
import { BlockFrame, EmptyBlock, usePf } from './SectionShell';
import type { BlockProps } from './SectionShell';

export function OpenTo({ block, index }: BlockProps) {
  const { content } = usePf();
  const titles = content.openTo.filter((t) => t.trim() !== '').slice(0, 2);
  if (!titles.length) return <EmptyBlock block={block} index={index} label="Open to" />;
  return (
    <BlockFrame block={block} index={index} ariaLabel="Open to" className="pf-open">
      <p className="pf-open-note pf-glass">
        <span className="pf-open-k">Open to:</span>
        <span className="pf-open-v">{titles.join(' · ')}</span>
      </p>
    </BlockFrame>
  );
}
