/**
 * Footer provenance: the snapshot date and the sources the Skill Graph was scored from (always shown).
 */
import { asOfLabel, sourcesLine } from '../model';
import { usePf } from './SectionShell';

export function Footer() {
  const { graph } = usePf();
  const sources = sourcesLine(graph);
  return (
    <footer className="pf-footer">
      <span>
        Skill Graph snapshot · {asOfLabel(graph)} · scored by Tayyari{sources ? ` from ${sources}` : ''}
      </span>
      <span>Made with Tayyari</span>
    </footer>
  );
}
