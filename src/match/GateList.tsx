/**
 * "Hard requirements, checked first": the conditions a posting states as non-negotiable, each with a verdict.
 *
 * A "Can't tell" is never a fail: it means the person's sources do not say, and adding the fact settles it. Only a
 * `Fail` (decided by code from the gate's severity) can block, and even then the rest of the match stays visible.
 */
import type { Gate, GateAnswer, Posting } from '../../shared/match';
import { Cites, GateChip } from './parts';
import type { MatchFact } from './types';

interface Props {
  posting: Posting;
  gates: readonly GateAnswer[];
  facts: readonly MatchFact[];
  blocked: boolean;
}

const UNSAID = "Nothing in your sources settles this. If it's true, say so below and it counts.";

export function GateList({ posting, gates, facts, blocked }: Props) {
  const answers = new Map(gates.map((g) => [g.gateId, g]));
  const rows = posting.gates
    .map((gate) => ({ gate, answer: answers.get(gate.id) }))
    .filter((r): r is { gate: Gate; answer: GateAnswer } => r.answer !== undefined);

  return (
    <div className="mt-gates">
      <p className="mt-lede">A "can't tell" is never a fail. It means your sources don't say. Add the fact if it's true.</p>

      {blocked && (
        <p className="mt-callout" role="status">
          One of these isn't met, so this role may not be open to you. The rest of the match is still here to read.
        </p>
      )}

      {rows.length === 0 ? (
        <p className="mt-lede">This posting states no hard conditions.</p>
      ) : (
        <ul className="mt-gate-list">
          {rows.map(({ gate, answer }) => (
            <li key={gate.id} className="mt-gate" data-gate={gate.id} data-verdict={answer.verdict}>
              <GateChip verdict={answer.verdict} />
              <div className="mt-gate-body">
                <p className="mt-gate-q">{gate.question}</p>
                {gate.quote && (
                  <p className="mt-posting-quote">
                    <q>{gate.quote}</q>
                  </p>
                )}
                <Cites factIds={answer.factIds} facts={facts} />
                {answer.note ? <p className="mt-note">{answer.note}</p> : answer.verdict === 'uncertain' ? <p className="mt-note">{UNSAID}</p> : null}
              </div>
            </li>
          ))}
        </ul>
      )}

      <div className="mt-how">
        <span className="kicker">How this is judged</span>
        <p className="mt-lede">
          The posting was read first, on its own, to list what it asks for and how strongly ("must", "required", "preferred"). Then each item was
          checked against your sources. A status only exists when it cites a line, a link or something you told us.
        </p>
      </div>
    </div>
  );
}
