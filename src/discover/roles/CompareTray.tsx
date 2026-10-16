/**
 * The compare tray: sticky at the bottom of the results once a role is added. "Compare 2 roles · Headout, SonicWall ·
 * Clear · Compare". Up to three roles can be put side by side; a single role has nothing to be compared with, so the
 * button waits for a second.
 */
import type { AiJob } from '../../../shared/jobsearch';
import { COMPARE_MAX } from '../route';

export interface CompareTrayProps {
  jobs: readonly AiJob[];
  onClear: () => void;
  onCompare: () => void;
}

export function CompareTray({ jobs, onClear, onCompare }: CompareTrayProps) {
  if (jobs.length === 0) return null;
  const ready = jobs.length >= 2;
  return (
    <div className="panel dc-tray" role="region" aria-label="Compare roles">
      <span className="dc-strong dc-tray-line" role="status">
        Compare {jobs.length} {jobs.length === 1 ? 'role' : 'roles'} · {jobs.map((j) => j.employer).join(', ')}
      </span>
      <span className="dc-muted dc-small">{ready ? `Up to ${COMPARE_MAX} roles` : 'Add one more to compare'}</span>
      <div className="dc-grow" />
      <button type="button" className="btn btn-quiet btn-sm dc-btn-sm" onClick={onClear}>
        Clear
      </button>
      <button type="button" className="btn btn-primary btn-sm dc-btn-sm" aria-disabled={!ready || undefined} onClick={() => ready && onCompare()}>
        Compare
      </button>
    </div>
  );
}
