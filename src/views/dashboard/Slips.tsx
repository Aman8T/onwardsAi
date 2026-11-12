/**
 * "Your notes": a neutral patch of the desk with two real post-its on it that lap over the tiles around them.
 *
 *   NotePile     the yellow one: the person's notes as a pile, the newest on top (a note written on a tracked role, a note
 *                written to themselves here, a line pinned from under the greeting). "Next" turns the pile, "edit" and
 *                "remove" are the person's, writing straightens the note. With no notes it is never empty: "Worth a note:"
 *                and three pen prompts, each of which starts a note.
 *   ResumeSlip   the pink one: what the resume leaves out (skills the other sources proved), and the way to /resume
 *   a dashed ghost square, "+ a note"
 *
 * The words on a note are the person's, shown as written, in the pen face. Sharing is the primary button of the hero now,
 * so there is no share slip. Under 760 px nothing is turned (the kit in global.css).
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import type { CSSProperties, FormEvent } from 'react';
import type { Profile } from '../../../shared/types';
import { STAGE_LABEL } from '../../discover/applications/tracker';
import type { TrackedJob } from '../../discover/applications/tracker';
import { sourceAction } from '../../lib/prompts';
import { actions } from '../../store';
import { trackedNotes } from './deskModel';
import { notOnResume } from './model';
import { NOTE_MAX_CHARS, useDeskNotes } from './notes';
import type { DeskNote, DeskNotes } from './notes';

type Sheet = { key: string; at: number } & ({ kind: 'tracked'; job: TrackedJob } | { kind: 'desk'; note: DeskNote });

const stamp = (iso: string | undefined): number => {
  const t = iso ? Date.parse(iso) : NaN;
  return Number.isFinite(t) ? t : 0;
};

const shortDay = (t: number): string => new Date(t).toLocaleDateString('en-GB', { day: 'numeric', month: 'short' });

/** What the yellow note offers when there is nothing on it yet: each starts a note. */
export const NOTE_PROMPTS = ['who could refer you', 'your next interview date', 'a role you liked, and why'] as const;

/** The notes, newest first: those on tracked roles and those written here. */
function useSheets(tracked: readonly TrackedJob[], desk: DeskNotes): Sheet[] {
  return useMemo<Sheet[]>(() => {
    const fromRoles: Sheet[] = trackedNotes(tracked).map((job) => ({ kind: 'tracked', key: `t:${job.job.id}`, at: stamp(job.updatedAt) || stamp(job.savedAt), job }));
    const fromDesk: Sheet[] = desk.notes.map((note) => ({ kind: 'desk', key: `d:${note.id}`, at: note.at, note }));
    return [...fromRoles, ...fromDesk].sort((a, b) => b.at - a.at);
  }, [tracked, desk.notes]);
}

/** The two post-its, the ghost square and the tile's counts, in one patch. */
export function NotesTile({ profile, tracked }: { profile: Profile; tracked: readonly TrackedJob[] }) {
  const desk = useDeskNotes(profile.id);
  const sheets = useSheets(tracked, desk);
  const found = useMemo(() => notOnResume(profile), [profile]);

  const [at, setAt] = useState(0);
  // null: reading. '': writing a new note. an id: rewriting that note.
  const [writing, setWriting] = useState<string | null>(null);
  const [draft, setDraft] = useState('');
  const field = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (writing !== null) field.current?.focus();
  }, [writing]);

  const top = sheets.length ? sheets[at % sheets.length] : undefined;
  const start = (note?: DeskNote, prefill = '') => {
    setDraft(note?.text ?? prefill);
    setWriting(note?.id ?? '');
  };
  const submit = (e: FormEvent) => {
    e.preventDefault();
    desk.save(draft, writing || undefined);
    setWriting(null);
    setAt(0);
  };
  const drop = (id: string) => {
    desk.remove(id);
    setAt(0);
  };
  const nudges = found ? 1 : 0;

  return (
    <section className="t t-notes" aria-label="Your notes and your resume">
      <p className="notes-count">
        <span className="kicker t-ink">Stuck here by you</span>
        <span className="fine t-ink">
          <span className="numeral">{sheets.length}</span> {sheets.length === 1 ? 'note' : 'notes'} · <span className="numeral">{nudges}</span> {nudges === 1 ? 'nudge' : 'nudges'}
        </span>
      </p>

      {writing !== null ? (
        <div className="stick stick-a">
          <form className="postit" style={{ '--pp': 'var(--paper-yellow)' } as CSSProperties} onSubmit={submit} aria-label="Write a note to yourself">
            <label className="pcite" htmlFor="db-note-field">
              Note to self
            </label>
            <textarea
              id="db-note-field"
              ref={field}
              className="pen pnote-field"
              rows={4}
              maxLength={NOTE_MAX_CHARS}
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Escape') setWriting(null);
              }}
              placeholder="Something to remember, in your own words…"
            />
            <span className="pfoot">
              <button type="submit" className="pbtn">
                Pin it
              </button>
              <button type="button" className="plink" onClick={() => setWriting(null)}>
                Cancel
              </button>
            </span>
          </form>
        </div>
      ) : top ? (
        <div className={`stick stick-a tilt-l${sheets.length > 1 ? ' pile' : ''}`}>
          <div className="postit" role="note" style={{ '--pp': 'var(--paper-yellow)' } as CSSProperties}>
            {top.kind === 'tracked' ? (
              <>
                <p className="pen">{top.job.note}</p>
                <span className="pcite">
                  your note · {top.job.job.employer} · {STAGE_LABEL[top.job.stage].toLowerCase()}
                </span>
                <span className="pfoot pfoot-start">
                  {sheets.length > 1 && (
                    <button type="button" className="plink" onClick={() => setAt((i) => (i + 1) % sheets.length)} aria-label={`Next note, ${(at % sheets.length) + 1} of ${sheets.length} showing`}>
                      <span className="numeral">
                        {(at % sheets.length) + 1} of {sheets.length}
                      </span>{' '}
                      · next →
                    </button>
                  )}
                  <button type="button" className="plink" onClick={() => actions.openDiscover({ tab: 'applications' })}>
                    open →
                  </button>
                </span>
              </>
            ) : (
              <>
                <p className="pen">{top.note.text}</p>
                <span className="pcite">{top.note.kind === 'pinned' ? `pinned · ${top.note.label ?? 'a line'}${top.note.by ? ` · ${top.note.by}` : ''}` : `note to self · ${shortDay(top.note.at)}`}</span>
                <span className="pfoot pfoot-start">
                  {sheets.length > 1 && (
                    <button type="button" className="plink" onClick={() => setAt((i) => (i + 1) % sheets.length)} aria-label={`Next note, ${(at % sheets.length) + 1} of ${sheets.length} showing`}>
                      <span className="numeral">
                        {(at % sheets.length) + 1} of {sheets.length}
                      </span>{' '}
                      · next →
                    </button>
                  )}
                  {top.note.kind === 'own' && (
                    <button type="button" className="plink" onClick={() => start(top.note)}>
                      edit
                    </button>
                  )}
                  <button type="button" className="plink" onClick={() => drop(top.note.id)}>
                    {top.note.kind === 'pinned' ? 'unpin' : 'remove'}
                  </button>
                </span>
              </>
            )}
          </div>
        </div>
      ) : (
        <div className="stick stick-a tilt-l">
          <div className="postit" role="note" style={{ '--pp': 'var(--paper-yellow)' } as CSSProperties}>
            <p className="pen pen-under">Worth a note:</p>
            {NOTE_PROMPTS.map((p) => (
              <button key={p} type="button" className="pen ptodo" onClick={() => start(undefined, `${p.charAt(0).toUpperCase()}${p.slice(1)}: `)}>
                {p}
              </button>
            ))}
            <span className="pcite pcite-end">press one to start writing</span>
          </div>
        </div>
      )}

      <ResumeSlip found={found} />

      <button type="button" className="ghost pen" onClick={() => start()}>
        + a note
      </button>
    </section>
  );
}

/* ───────────────────────── the resume slip ───────────────────────── */

const QUOTE_MAX = 80;

function ResumeSlip({ found }: { found: ReturnType<typeof notOnResume> }) {
  const open = () => actions.openResume({ screen: 'resume' });
  if (!found) {
    return (
      <div className="stick stick-b tilt-r">
        <div className="postit" role="note" style={{ '--pp': 'var(--paper-pink)' } as CSSProperties}>
          <p className="pen">One resume, a version for each next role.</p>
          <span className="pcite">your resumes</span>
          <span className="pfoot">
            <button type="button" className="pbtn" onClick={open}>
              Open them
            </button>
          </span>
        </div>
      </div>
    );
  }
  const { resume, items, role } = found;
  const labels = items.map((it) => it.source.label).filter((v, i, arr) => arr.indexOf(v) === i);
  const names = items.map((it) => it.skill.name).join(' and ');
  const first = items[0];
  const quote = first.evidence.text.length > QUOTE_MAX ? `${first.evidence.text.slice(0, QUOTE_MAX - 1).trimEnd()}…` : first.evidence.text;
  const add = () => {
    const a = sourceAction(resume);
    actions.openChat(
      a.origin,
      `My resume doesn't mention ${items.map((it) => it.skill.name).join(' or ')}, which ${labels.join(' and ')} prove${labels.length === 1 ? 's' : ''}. Help me add ${items.length === 1 ? 'it' : 'them'} as resume bullets in my own voice, using only the cited facts.`,
    );
  };
  return (
    <div className="stick stick-b tilt-r">
      <div className="postit" role="note" aria-labelledby="db-slip-h" style={{ '--pp': 'var(--paper-pink)' } as CSSProperties}>
        <p className="pen" id="db-slip-h">
          {names}: the resume never says so.
        </p>
        <span className="pcite">
          {first.source.label} · “{quote}”{role ? ` · counts towards ${role.title}` : ''}
        </span>
        <span className="pfoot">
          <button type="button" className="pbtn" onClick={add}>
            Add {items.length === 1 ? 'it' : 'both'}
          </button>
          <button type="button" className="plink" onClick={open}>
            Your resumes →
          </button>
        </span>
      </div>
    </div>
  );
}
