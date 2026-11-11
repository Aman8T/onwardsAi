/**
 * The rail: every resume the person has. The one they uploaded, then one slot per next role of their graph (a resume
 * that exists opens; one that does not is made on request), then the line that holds the page together: every resume
 * is the same blocks in another arrangement.
 */
import { matchPath } from '../match/route';
import { statusOf } from './model';
import { ScreenLink, Stamp } from './parts';
import type { ResumeQuery } from './route';
import type { Busy, PathSlot, ResumeList, ResumeSummary } from './types';

interface RailProps {
  list: ResumeList;
  /** The resume in view. */
  viewId?: string;
  busy: Busy | null;
  onNavigate: (to: ResumeQuery) => void;
  onMake: (roleId: string) => void;
  /** Open the match a posting resume was saved from. */
  onOpenMatch: (matchId: string) => void;
}

/** A resume saved from a posting: it opens like the others, and says which match it came from. */
function PostingRow({ resume, viewId, onNavigate, onOpenMatch }: { resume: ResumeSummary; viewId?: string; onNavigate: (to: ResumeQuery) => void; onOpenMatch: (matchId: string) => void }) {
  const on = resume.id === viewId;
  const matchId = resume.matchId;
  return (
    <li className={`rs-row rs-row-top${on ? ' is-on' : ''}`}>
      <span className="rs-row-main">
        <ScreenLink to={{ screen: 'resume', id: resume.id }} onNavigate={onNavigate} className="rs-small rs-strong" current={on}>
          {resume.title}
        </ScreenLink>
        <span className="rs-fine">{statusOf(resume)}</span>
        {matchId && (
          <a
            href={matchPath({ screen: 'match', id: matchId })}
            className="rs-link rs-small"
            onClick={(e) => {
              if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
              e.preventDefault();
              onOpenMatch(matchId);
            }}
          >
            from a match
          </a>
        )}
      </span>
      <Stamp>saved</Stamp>
    </li>
  );
}

function ResumeRow({ resume, slot, viewId, onNavigate }: { resume: ResumeSummary; slot?: PathSlot; viewId?: string; onNavigate: (to: ResumeQuery) => void }) {
  const on = resume.id === viewId;
  return (
    <li>
      <ScreenLink to={{ screen: 'resume', id: resume.id }} onNavigate={onNavigate} className={`rs-row rs-row-link${on ? ' is-on' : ''}`} current={on}>
        <span className="rs-row-main">
          <span className="rs-small rs-strong">{resume.title}</span>
          <span className="rs-fine">{statusOf(resume)}</span>
        </span>
        {resume.isDefault ? (
          <Stamp>default</Stamp>
        ) : slot ? (
          <span className="numeral rs-small rs-muted">
            {Math.round(slot.alignment)}%<span className="sr-only"> aligned</span>
          </span>
        ) : null}
      </ScreenLink>
    </li>
  );
}

export function Rail({ list, viewId, busy, onNavigate, onMake, onOpenMatch }: RailProps) {
  const byId = new Map(list.resumes.map((r) => [r.id, r]));
  const uploads = list.resumes.filter((r) => r.kind === 'upload');
  const slotted = new Set(list.paths.map((p) => p.resumeId).filter(Boolean));
  // a resume made for a role that has since left the next roles is kept, and listed after them
  const kept = list.resumes.filter((r) => r.kind === 'path' && !slotted.has(r.id));
  const postings = list.resumes.filter((r) => r.kind === 'posting');
  const making = busy?.kind === 'make' ? list.paths.find((p) => p.roleId === busy.roleId) : undefined;

  return (
    <aside className="rs-rail" aria-label="Your resumes">
      <section className="rs-rail-group" aria-labelledby="rs-up">
        <h2 id="rs-up" className="kicker">
          Uploaded by you
        </h2>
        {uploads.length > 0 ? (
          <ul className="rs-rows">
            {uploads.map((r) => (
              <ResumeRow key={r.id} resume={r} viewId={viewId} onNavigate={onNavigate} />
            ))}
          </ul>
        ) : (
          <p className="rs-fine">No resume file was read for this profile, so there is none to show here.</p>
        )}
      </section>

      <section className="rs-rail-group" aria-labelledby="rs-paths">
        <h2 id="rs-paths" className="kicker">
          Built for your next roles
        </h2>
        {list.paths.length + kept.length > 0 ? (
          <ul className="rs-rows">
            {list.paths.map((slot) => {
              const resume = slot.resumeId ? byId.get(slot.resumeId) : undefined;
              if (resume) return <ResumeRow key={slot.roleId} resume={resume} slot={slot} viewId={viewId} onNavigate={onNavigate} />;
              if (slot.resumeId) {
                return (
                  <li key={slot.roleId}>
                    <ScreenLink to={{ screen: 'resume', id: slot.resumeId }} onNavigate={onNavigate} className="rs-row rs-row-link" current={slot.resumeId === viewId}>
                      <span className="rs-row-main">
                        <span className="rs-small rs-strong">{slot.title}</span>
                        <span className="rs-fine">made</span>
                      </span>
                    </ScreenLink>
                  </li>
                );
              }
              const busyHere = busy?.kind === 'make' && busy.roleId === slot.roleId;
              return (
                <li key={slot.roleId} className="rs-row">
                  <span className="rs-row-main">
                    <span className="rs-small">{slot.title}</span>
                    <span className="rs-fine">{busyHere ? 'being made now' : 'not made yet'}</span>
                  </span>
                  <button type="button" className="btn btn-sm rs-btn" disabled={busy !== null} aria-busy={busyHere} onClick={() => onMake(slot.roleId)}>
                    {busyHere ? 'Making…' : 'Make it'}
                    <span className="sr-only">: a resume for {slot.title}</span>
                  </button>
                </li>
              );
            })}
            {kept.map((r) => (
              <ResumeRow key={r.id} resume={r} viewId={viewId} onNavigate={onNavigate} />
            ))}
          </ul>
        ) : (
          <p className="rs-fine">Your graph has no next roles yet, so there is no role to build a resume for.</p>
        )}
        <p className="rs-fine" role="status">
          {making ? `Ranking your blocks against ${making.title} and choosing the ones that speak to it. This takes a few seconds.` : ''}
        </p>
        <p className="rs-fine">A resume for a role is made when you ask for it. It stays here even if the role later leaves your next roles.</p>
      </section>

      <section className="rs-rail-group" aria-labelledby="rs-posts">
        <h2 id="rs-posts" className="kicker">
          Saved from a posting
        </h2>
        {postings.length > 0 && (
          <ul className="rs-rows">
            {postings.map((r) => (
              <PostingRow key={r.id} resume={r} viewId={viewId} onNavigate={onNavigate} onOpenMatch={onOpenMatch} />
            ))}
          </ul>
        )}
        <p className="rs-fine">A resume tailored in Match stays with its match. It joins this list only when you save it.</p>
      </section>

      <div className="slip rs-note" role="note" aria-label="One set of blocks">
        <span className="kicker">One set of blocks</span>
        <p className="hand">Every resume here is the same blocks, arranged another way.</p>
        <ScreenLink to={{ screen: 'blocks', id: viewId }} onNavigate={onNavigate} className="rs-small">
          See all <span className="numeral">{list.blocks}</span> blocks →
        </ScreenLink>
      </div>
    </aside>
  );
}
