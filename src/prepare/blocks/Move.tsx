/**
 * The one move that closes most of it: title, one line, what the person already has (each cited), the gaps it works
 * on (names only, never a predicted number), the hours in all (computed), and the two actions: Plan it / Hide the
 * plan, and Show another move (up to three, best first, one at a time).
 */
import { useId, useState } from 'react';
import type { PrepareCited, PrepareEvidence, PrepareMove } from '../../../shared/prepare';
import { evidenceLabel, totalHours } from '../plan';

interface MoveProps {
  move: PrepareMove;
  index: number;
  count: number;
  evidence: Map<string, PrepareEvidence>;
  planOpen: boolean;
  onTogglePlan: () => void;
  onNext: () => void;
}

export function MoveBlock({ move, index, count, evidence, planOpen, onTogglePlan, onNext }: MoveProps) {
  return (
    <section id="pp-move" className="pp-block m-plain" aria-labelledby="pp-move-h">
      <div className="pp-head">
        <span className="pp-h">The one move that closes most of it</span>
        <span className="numeral pp-meta">
          {index + 1} of {count}
        </span>
      </div>
      <h2 id="pp-move-h" className="title pp-move-title">
        {move.title}
      </h2>
      {move.body && <p className="pp-move-body">{move.body}</p>}

      {move.have.length > 0 && (
        <div className="pp-stack">
          <span className="kicker">What you already have</span>
          <HaveChips items={move.have} evidence={evidence} />
        </div>
      )}

      <div className="pp-works">
        {move.works.length > 0 && (
          <>
            <span className="kicker">Works on</span>
            {move.works.map((w) => (
              <span key={w} className="chip pp-work">
                {w}
              </span>
            ))}
          </>
        )}
        <span className="numeral pp-hours">about {totalHours(move)} h</span>
      </div>

      <div className="pp-actions pp-move-actions">
        <button type="button" className="btn pp-btn" onClick={onTogglePlan} aria-expanded={planOpen} aria-controls="pp-plan">
          {planOpen ? 'Hide the plan' : 'Plan it'}
        </button>
        {count > 1 && (
          <button type="button" className="btn pp-btn" onClick={onNext}>
            Show another move
          </button>
        )}
      </div>
    </section>
  );
}

/** What the person already has: each item a chip; a tap shows the lines it rests on. */
function HaveChips({ items, evidence }: { items: PrepareCited[]; evidence: Map<string, PrepareEvidence> }) {
  const lineId = `${useId()}-have`;
  const [open, setOpen] = useState<number | null>(null);
  const shown = open !== null ? items[open] : undefined;
  const lines = shown ? shown.evidenceIds.map((id) => evidence.get(id)).filter((e): e is PrepareEvidence => e !== undefined) : [];
  return (
    <div className="pp-ev">
      <div className="pp-have">
        {items.map((h, i) => {
          const on = open === i;
          return (
            <button
              key={i}
              type="button"
              className={`pp-have-chip${on ? ' is-on' : ''}`}
              aria-expanded={on}
              aria-controls={lineId}
              onClick={() => setOpen(on ? null : i)}
            >
              {h.text}
            </button>
          );
        })}
      </div>
      <div id={lineId} hidden={!shown}>
        {lines.map((e) => (
          <p key={e.id} className="pp-ev-line">
            <span className="pp-ev-skill">{evidenceLabel(e)} · </span>
            <q>{e.text}</q>
          </p>
        ))}
        {shown && lines.length === 0 && <p className="pp-ev-line pp-muted">No line of your evidence is attached to this one.</p>}
      </div>
    </div>
  );
}
