/**
 * "Your applications": the roles a person saved and what became of them. Kept in this browser, and it says so; the
 * only thing that ever leaves is a check of whether each role is still on the employer's board. A role that is gone
 * stays on the list with its snapshot, marked "No longer on the employer's board", and the match and resume made for
 * it are kept.
 */
import { useEffect, useRef, useState } from 'react';
import { BAND_LABEL } from '../match/model';
import { FIT_LABEL, MODE_LABEL, fmtKm, safeHref } from './model';
import { ApplyLink } from './ListingRow';
import { STATUSES, STATUS_LABEL } from './tracker';
import type { TrackStatus, TrackedRole, Tracker } from './tracker';

export type ListingCheck = 'open' | 'gone' | 'unknown';

/** Roles already checked this session, so reopening the tab does not ask again. */
const checkedThisSession = new Set<string>();
const MAX_CHECKS = 20;
const CHECK_WORKERS = 3;

const fmtDate = (iso: string) => new Date(iso).toLocaleDateString('en-GB', { day: 'numeric', month: 'short' });

/** The same four tones the quick-fit chips use, by how the full match came out. */
const BAND_TONE: Record<string, string> = { strong: 'looks_close', good: 'looks_close', stretch: 'worth_a_look', weak: 'stretch' };

function FitCell({ role }: { role: TrackedRole }) {
  if (role.matchId && role.band && BAND_LABEL[role.band as keyof typeof BAND_LABEL]) {
    return <span className={`dc-fit dc-fit-${BAND_TONE[role.band] ?? 'cant_tell'}`}>{BAND_LABEL[role.band as keyof typeof BAND_LABEL]}</span>;
  }
  return (
    <span className={`dc-fit dc-fit-${role.word}`}>
      {FIT_LABEL[role.word]} · not checked yet
    </span>
  );
}

export interface ApplicationsProps {
  tracker: Tracker;
  /** Asks the server whether a role is still listed; absent in the gallery, which marks nothing. */
  check?: (id: string) => Promise<ListingCheck>;
  onOpenMatch: (matchId: string) => void;
  onBack: () => void;
}

export function Applications({ tracker, check, onOpenMatch, onBack }: ApplicationsProps) {
  const [filter, setFilter] = useState<TrackStatus | 'all'>('all');
  const latest = useRef(tracker);
  latest.current = tracker;
  const count = (s: TrackStatus) => tracker.items.filter((r) => r.status === s).length;
  const shown = [...tracker.items].filter((r) => filter === 'all' || r.status === filter).sort((a, b) => b.updatedAt.localeCompare(a.updatedAt));

  // is each open role still on the employer's board? a few at a time, once per session
  const ids = tracker.items.map((r) => r.id).join(',');
  useEffect(() => {
    if (!check) return;
    let cancelled = false;
    const queue = latest.current.items.filter((r) => r.status !== 'closed' && !checkedThisSession.has(r.id)).slice(0, MAX_CHECKS);
    const worker = async () => {
      for (let r = queue.shift(); r; r = queue.shift()) {
        const result = await check(r.id);
        if (cancelled) return;
        checkedThisSession.add(r.id);
        if (result !== 'unknown') latest.current.markGone(r.id, result === 'gone');
      }
    };
    void Promise.all(Array.from({ length: CHECK_WORKERS }, worker));
    return () => {
      cancelled = true;
    };
  }, [ids, check]);

  const n = tracker.items.length;
  return (
    <>
      <section className="dc-intro">
        <p className="kicker">Your applications</p>
        <h1 className="serif dc-title" data-dc-h1 tabIndex={-1}>
          {n === 0 ? 'Nothing saved yet' : n === 1 ? "1 role you're following" : `${n} roles you're following`}
        </h1>
        <p className="dc-lede">Saved, applied and closed, in one place. Kept in this browser: clearing your browser data removes it.</p>
        <div className="dc-actions">
          <button type="button" className="btn btn-sm dc-btn-sm" onClick={onBack}>
            Back to roles
          </button>
        </div>
      </section>

      {n === 0 ? (
        <section className="panel dc-state" aria-labelledby="dc-track-empty">
          <h2 id="dc-track-empty" className="dc-state-title">
            Save a role and it shows up here.
          </h2>
          <p className="dc-muted">Use the bookmark on any row or card. Then move it along as you apply: Saved, Applied, Interviewing, Offer, Closed.</p>
        </section>
      ) : (
        <section className="dc-track" aria-label="Roles you are following">
          <div role="group" aria-label="Filter by status" className="dc-filters">
            <button type="button" className="btn btn-sm dc-btn-sm dc-filter" aria-pressed={filter === 'all'} onClick={() => setFilter('all')}>
              All · <span className="numeral">{n}</span>
            </button>
            {STATUSES.map((s) => (
              <button key={s} type="button" className="btn btn-sm dc-btn-sm dc-filter" aria-pressed={filter === s} onClick={() => setFilter(s)}>
                {STATUS_LABEL[s]} · <span className="numeral">{count(s)}</span>
              </button>
            ))}
          </div>
          <div className="dc-track-head kicker" aria-hidden="true">
            <span>Role</span>
            <span>Status</span>
            <span>Fit</span>
            <span>Dates</span>
            <span>Actions</span>
          </div>
          {shown.length === 0 ? (
            <p className="dc-muted dc-track-none">Nothing is marked {STATUS_LABEL[filter as TrackStatus].toLowerCase()} yet.</p>
          ) : (
            <ul className="dc-track-list">
              {shown.map((r) => (
                <TrackRow key={r.id} role={r} tracker={tracker} onOpenMatch={onOpenMatch} />
              ))}
            </ul>
          )}
          <p className="dc-faint dc-small">We check each open role against the employer's board when you open this page. Nothing else here leaves your browser.</p>
        </section>
      )}
    </>
  );
}

function TrackRow({ role, tracker, onOpenMatch }: { role: TrackedRole; tracker: Tracker; onOpenMatch: (matchId: string) => void }) {
  const dim = role.gone || role.status === 'closed';
  const mode = MODE_LABEL[role.workMode];
  const where = [role.placeLabel, mode && !role.placeLabel.toLowerCase().startsWith('remote') ? mode : '', role.km !== undefined ? `${fmtKm(role.km)} km straight line` : ''].filter(Boolean).join(' · ');
  const selectId = `dc-st-${role.id}`;
  return (
    <li className={`dc-track-row${dim ? ' is-closed' : ''}`}>
      <div className="dc-track-role">
        <p className={dim ? 'dc-faint dc-strong' : 'dc-strong'}>{role.title}</p>
        <p className={`${dim ? 'dc-faint' : 'dc-muted'} dc-small`}>
          {role.employer} · {where}
        </p>
      </div>
      <div className="dc-track-status">
        <label htmlFor={selectId} className="sr-only">
          Status for {role.title} at {role.employer}
        </label>
        <select id={selectId} className="dc-select dc-select-sm" value={role.status} onChange={(e) => tracker.setStatus(role.id, e.target.value as TrackStatus)}>
          {STATUSES.map((s) => (
            <option key={s} value={s}>
              {STATUS_LABEL[s]}
            </option>
          ))}
        </select>
      </div>
      <div className="dc-track-fit">
        <FitCell role={role} />
      </div>
      <div className="dc-track-dates dc-muted dc-small">
        <span>Saved {fmtDate(role.savedAt)}</span>
        {role.appliedAt && <span>Applied {fmtDate(role.appliedAt)}</span>}
        {role.closedAt && <span>Closed {fmtDate(role.closedAt)}</span>}
      </div>
      <div className="dc-track-actions">
        {role.matchId && (
          <button type="button" className="btn btn-sm dc-btn-sm" onClick={() => onOpenMatch(role.matchId!)}>
            Open match
          </button>
        )}
        {!role.gone && safeHref(role.applyUrl) && <ApplyLink card={role} className="btn btn-sm dc-btn-sm" />}
        <button type="button" className="btn btn-sm btn-quiet dc-btn-sm" aria-label={`Remove ${role.title} at ${role.employer} from Your applications`} onClick={() => tracker.remove(role.id)}>
          Remove
        </button>
      </div>
      {role.gone && (
        <p className="dc-track-note">
          No longer on the employer's board.{role.matchId ? ' Your match for it is kept.' : ''}
        </p>
      )}
    </li>
  );
}
