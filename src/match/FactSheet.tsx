/**
 * The fact behind a superscript id on the resume: what it says, and the words of every source it rests on. A dialog
 * on wide screens and a bottom sheet under 760 px (the shared `Sheet` from the account screens: focus lands inside,
 * Tab stays inside, Escape closes and focus returns to the id that opened it).
 */
import { useId } from 'react';
import { Sheet } from '../auth/Sheet';
import { FactRefs, FromYouChip } from './parts';
import type { MatchFact } from './types';

const KIND_LABEL: Record<MatchFact['kind'], string> = {
  experience: 'A role you held',
  accomplishment: 'Something you achieved',
  skill: 'A skill',
  education: 'Education',
  project: 'A project',
  cert: 'A certification',
  other: 'From your documents',
};

const TIER_LABEL = {
  artifact: 'Backed by a public artifact',
  built: 'Built in a project with an outcome',
  used: 'Used in your work',
  listed: 'Only listed',
} as const;

export function FactSheet({ id, fact, onClose }: { id: string | null; fact?: MatchFact; onClose: () => void }) {
  const titleId = useId();
  return (
    <Sheet open={id !== null} onClose={onClose} labelledBy={titleId} className="mt-fact-sheet">
      {id !== null && (
        <div className="mt-fact">
          <button type="button" className="auth-close" aria-label="Close" onClick={onClose}>
            <svg width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.25" strokeLinecap="round" aria-hidden="true">
              <path d="M2 2l8 8M10 2l-8 8" />
            </svg>
          </button>
          <p className="kicker">
            Source <span className="numeral">{id}</span>
          </p>
          <h2 id={titleId} className="mt-fact-title">
            {fact ? fact.text : 'This source is not in the evidence for this match.'}
          </h2>
          {fact && (
            <>
              <div className="mt-chips">
                <span className="chip">{KIND_LABEL[fact.kind]}</span>
                {fact.evidenceTier && <span className="chip">{TIER_LABEL[fact.evidenceTier]}</span>}
                {fact.refs.some((r) => r.source === 'user') && <FromYouChip />}
              </div>
              <p className="kicker">Where it comes from</p>
              <FactRefs fact={fact} />
            </>
          )}
        </div>
      )}
    </Sheet>
  );
}
