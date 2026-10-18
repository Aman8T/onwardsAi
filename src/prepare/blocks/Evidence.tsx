/**
 * Evidence chips: "From your resume", one per line a sentence cites. A tap shows the line itself (the graph's own
 * evidence, word for word) and the skill it evidences. `children` sits at the end of the row (a related action).
 */
import { useId, useState } from 'react';
import type { ReactNode } from 'react';
import type { PrepareEvidence } from '../../../shared/prepare';
import { evidenceLabel } from '../plan';

interface Props {
  ids: readonly string[];
  evidence: Map<string, PrepareEvidence>;
  children?: ReactNode;
}

export function EvidenceChips({ ids, evidence, children }: Props) {
  const lineId = `${useId()}-line`;
  const [open, setOpen] = useState<string | null>(null);
  const items = ids.map((id) => evidence.get(id)).filter((e): e is PrepareEvidence => e !== undefined);
  if (!items.length && !children) return null;
  const shown = open ? evidence.get(open) : undefined;
  return (
    <div className="pp-ev">
      <div className="pp-ev-row">
        {items.map((e) => {
          const on = open === e.id;
          return (
            <button
              key={e.id}
              type="button"
              className={`chip pp-ev-chip${on ? ' is-on' : ''}`}
              aria-expanded={on}
              aria-controls={lineId}
              onClick={() => setOpen(on ? null : e.id)}
            >
              {evidenceLabel(e)}
            </button>
          );
        })}
        {children}
      </div>
      <p id={lineId} className="pp-ev-line" hidden={!shown}>
        {shown && (
          <>
            <q>{shown.text}</q> <span className="pp-ev-skill">· {shown.skill}</span>
          </>
        )}
      </p>
    </div>
  );
}
