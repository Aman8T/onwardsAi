/**
 * Every requirement, with the sentence it came from, a status and the evidence behind it.
 *
 * Wide screens read it as a table (Requirement · The posting says · Status · Your evidence); under 760 px each row is
 * a card with the same four cells and their labels. It is one set of elements either way, with explicit table roles
 * so the semantics survive the layout switch. Rows are sorted by how strongly the posting asks, then unmet first
 * (`sortRows`). While a match is still streaming, rows whose coverage has not arrived say so instead of guessing.
 */
import { useState } from 'react';
import type { Coverage, Posting } from '../../shared/match';
import { isHole, sortRows } from './model';
import type { Row } from './model';
import { Cites, StatusChip, TierChip } from './parts';
import type { MatchFact } from './types';

interface Props {
  posting: Posting;
  coverage: readonly Coverage[];
  facts: readonly MatchFact[];
  /** Show only this many rows with a "Show the other N" button (the phone). Omit to show all. */
  limit?: number;
  /** The result is final: a row with no coverage is missing from it, not still on its way. */
  settled?: boolean;
  /** Opens where the person can add proof (the questions). A requirement with nothing cited offers it. */
  onAddProof?: () => void;
}

export function RequirementTable({ posting, coverage, facts, limit, settled = false, onAddProof }: Props) {
  const [all, setAll] = useState(false);
  const rows = sortRows(posting, coverage);
  const shown = limit !== undefined && !all ? rows.slice(0, limit) : rows;
  const hidden = rows.length - shown.length;

  return (
    <div className="mt-table" role="table" aria-label="Every requirement, with your evidence" aria-rowcount={rows.length + 1}>
      <div className="mt-thead" role="rowgroup">
        <div className="mt-tr mt-tr-head" role="row">
          <span className="kicker" role="columnheader">Requirement</span>
          <span className="kicker" role="columnheader">The posting says</span>
          <span className="kicker" role="columnheader">Status</span>
          <span className="kicker" role="columnheader">Your evidence</span>
        </div>
      </div>
      <div className="mt-tbody" role="rowgroup">
        {shown.map((row) => (
          <TableRow key={row.requirement.id} row={row} facts={facts} settled={settled} onAddProof={onAddProof} />
        ))}
      </div>
      {hidden > 0 && (
        <button type="button" className="mt-more" onClick={() => setAll(true)}>
          Show the other {hidden}
        </button>
      )}
    </div>
  );
}

function TableRow({ row, facts, settled, onAddProof }: { row: Row; facts: readonly MatchFact[]; settled: boolean; onAddProof?: () => void }) {
  const { requirement: req, coverage: cov } = row;
  // nothing cited: a dashed hole, not a tile of evidence
  const hole = (cov !== undefined || settled) && isHole(cov);
  return (
    <div className={`mt-tr ${hole ? 'm-gap' : cov ? 'm-graph' : 'm-quiet'}`} role="row" data-tier={req.tier} data-req={req.id} data-status={cov?.status ?? (settled ? 'missing' : 'pending')}>
      <div className="mt-td mt-td-req" role="cell">
        <TierChip tier={req.tier} />
        <p className="mt-req-text">{req.text}</p>
        {req.provenance === 'inferred' && <p className="mt-inferred">Read between the lines, not stated</p>}
      </div>
      <div className="mt-td" role="cell">
        <span className="mt-cell-label kicker" aria-hidden="true">The posting says</span>
        <p className="mt-posting-quote">
          <q>{req.quote}</q>
        </p>
      </div>
      <div className="mt-td mt-td-status" role="cell">
        <span className="mt-cell-label kicker" aria-hidden="true">Status</span>
        {cov ? <StatusChip status={cov.status} /> : <span className="mt-pending">{settled ? 'No status recorded' : 'Checking…'}</span>}
      </div>
      <div className="mt-td" role="cell">
        <span className="mt-cell-label kicker" aria-hidden="true">Your evidence</span>
        {cov ? <Evidence cov={cov} facts={facts} /> : <p className="mt-note mt-note-faint">{settled ? 'The stored result has no row for this requirement.' : 'Looking for evidence.'}</p>}
        {hole && onAddProof && (
          <button type="button" className="mt-addproof" onClick={onAddProof}>
            Add proof
            <span className="sr-only"> for {req.text}</span>
            <span aria-hidden="true"> →</span>
          </button>
        )}
      </div>
    </div>
  );
}

function Evidence({ cov, facts }: { cov: Coverage; facts: readonly MatchFact[] }) {
  return (
    <>
      {cov.note && <p className="mt-note">{cov.note}</p>}
      <Cites factIds={cov.factIds} facts={facts} />
      {(cov.interviewRisk || cov.mitigation) && (
        <p className="mt-risk">
          {cov.interviewRisk && <>Interview risk: {cov.interviewRisk} </>}
          {cov.mitigation && <>Mitigation: {cov.mitigation}</>}
        </p>
      )}
    </>
  );
}
