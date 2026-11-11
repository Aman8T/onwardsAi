/**
 * The slips beside the profile: the hand-touched corner of the dashboard, each one something of the person's own.
 *
 *   NotePile     their notes as a small pile, the newest on top: a note written on a tracked role, a note written
 *                to themselves here, a line pinned from under the greeting. "Write a note" turns the top slip into a field.
 *   ShareSlip    their skill shape as a taped picture; pressing it opens the share sheet. When the card is already
 *                shared it says so ("Your card is live") and, as two real buttons, opens sharing or copies the link
 *   ResumeSlip   what the resume leaves out (skills the other sources proved), and the way to /resume
 *
 * The words on a note are the person's, shown as written. The slips are turned a degree or two on a wide screen
 * (the kit in global.css) and sit straight on a phone; a slip being written on is never turned.
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import type { Profile } from '../../../shared/types';
import { ShareButton, useShareSheet } from '../../card/ShareButton';
import { useCardLink } from '../../card/useCardLink';
import { RadarGlyph } from '../../components/Radar';
import { STAGE_LABEL } from '../../discover/applications/tracker';
import type { TrackedJob } from '../../discover/applications/tracker';
import { sourceAction } from '../../lib/prompts';
import { actions } from '../../store';
import { trackedNotes } from './deskModel';
import { notOnResume } from './model';
import { NOTE_MAX_CHARS, useDeskNotes } from './notes';
import type { DeskNote } from './notes';

/* ───────────────────────── the pile of notes ───────────────────────── */

type Sheet = { key: string; at: number } & ({ kind: 'tracked'; job: TrackedJob } | { kind: 'desk'; note: DeskNote });

const stamp = (iso: string | undefined): number => {
  const t = iso ? Date.parse(iso) : NaN;
  return Number.isFinite(t) ? t : 0;
};

const shortDay = (t: number): string => new Date(t).toLocaleDateString('en-GB', { day: 'numeric', month: 'short' });

function NotePile({ profileId, tracked }: { profileId: string; tracked: readonly TrackedJob[] }) {
  const desk = useDeskNotes(profileId);
  const sheets = useMemo<Sheet[]>(() => {
    const fromRoles: Sheet[] = trackedNotes(tracked).map((job) => ({ kind: 'tracked', key: `t:${job.job.id}`, at: stamp(job.updatedAt) || stamp(job.savedAt), job }));
    const fromDesk: Sheet[] = desk.notes.map((note) => ({ kind: 'desk', key: `d:${note.id}`, at: note.at, note }));
    return [...fromRoles, ...fromDesk].sort((a, b) => b.at - a.at);
  }, [tracked, desk.notes]);

  const [at, setAt] = useState(0);
  // null: reading. '': writing a new note. an id: rewriting that note.
  const [writing, setWriting] = useState<string | null>(null);
  const [draft, setDraft] = useState('');
  const field = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (writing !== null) field.current?.focus();
  }, [writing]);

  const top = sheets.length ? sheets[at % sheets.length] : undefined;
  const start = (note?: DeskNote) => {
    setDraft(note?.text ?? '');
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

  if (writing !== null) {
    return (
      <form className="slip slip-pin db-pile-slip" onSubmit={submit} aria-label="Write a note to yourself">
        <label className="kicker" htmlFor="db-note-field">
          Note to self
        </label>
        <textarea
          id="db-note-field"
          ref={field}
          className="hand db-note-field"
          rows={3}
          maxLength={NOTE_MAX_CHARS}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Escape') setWriting(null);
          }}
          placeholder="Something to remember, in your own words…"
        />
        <span className="db-slip-foot">
          <button type="submit" className="btn btn-sm btn-primary">
            Pin it
          </button>
          <button type="button" className="btn btn-sm btn-quiet" onClick={() => setWriting(null)}>
            Cancel
          </button>
          <span className="db-fine db-slip-end">Stays in this browser</span>
        </span>
      </form>
    );
  }

  if (!top) {
    return (
      <button type="button" className="slip slip-pin tilt-l db-pile-slip" onClick={() => start()}>
        <span className="kicker">Your notes</span>
        <span className="hand">Nothing pinned yet.</span>
        <span className="db-fine">Write yourself a note. One you write on a saved role lands here too.</span>
        <span className="db-slip-foot">
          <span className="btn btn-sm">Write a note</span>
        </span>
      </button>
    );
  }

  return (
    <div className="db-pile">
      <div className={`stack tilt-l db-pile-stack${sheets.length > 1 ? '' : ' is-single'}`}>
        <div className="slip slip-pin db-pile-slip" role="note">
          {top.kind === 'tracked' ? (
            <>
              <span className="kicker">Your note · {top.job.job.employer}</span>
              <span className="hand">{top.job.note}</span>
              <span className="db-cite">
                {STAGE_LABEL[top.job.stage].toLowerCase()} · {top.job.job.title}
              </span>
              <span className="db-slip-foot">
                <button type="button" className="db-link" onClick={() => actions.openDiscover({ tab: 'applications' })}>
                  Open the application →
                </button>
              </span>
            </>
          ) : (
            <>
              <span className="kicker">{top.note.kind === 'pinned' ? `Pinned · ${top.note.label ?? 'a line'}` : `Note to self · ${shortDay(top.note.at)}`}</span>
              <span className="hand">{top.note.text}</span>
              {top.note.by && <span className="db-cite">{top.note.by}</span>}
              <span className="db-slip-foot">
                {top.note.kind === 'own' && (
                  <button type="button" className="db-link" onClick={() => start(top.note)}>
                    Edit
                  </button>
                )}
                <button type="button" className="db-link" onClick={() => drop(top.note.id)}>
                  {top.note.kind === 'pinned' ? 'Unpin' : 'Remove'}
                </button>
              </span>
            </>
          )}
        </div>
      </div>
      <div className="db-pile-foot">
        {sheets.length > 1 && (
          <button type="button" className="db-link" onClick={() => setAt((i) => (i + 1) % sheets.length)} aria-label={`Next note, ${(at % sheets.length) + 1} of ${sheets.length} showing`}>
            <span className="numeral">
              {(at % sheets.length) + 1} of {sheets.length}
            </span>{' '}
            · next →
          </button>
        )}
        <button type="button" className="db-link db-pile-add" onClick={() => start()}>
          + Write a note
        </button>
      </div>
    </div>
  );
}

/* ───────────────────────── the share slip ───────────────────────── */

const COPIED_MS = 2000;

function ShareSlip({ profile }: { profile: Profile }) {
  const values = useMemo(() => profile.domains.map((d) => d.score), [profile.domains]);
  // the card's own state, read once; null (off, unknown, no API) leaves the slip as it always was
  const link = useCardLink(profile.id);
  const { show, open, sheet } = useShareSheet(profile.id);
  const [copied, setCopied] = useState(false);
  const timer = useRef<number | undefined>(undefined);
  useEffect(() => () => window.clearTimeout(timer.current), []);

  if (link === null) {
    return (
      <ShareButton profileId={profile.id} look="slip" className="slip slip-tape tilt-r db-share-slip">
        <RadarGlyph className="db-share-glyph" values={values} size={76} />
        <span className="db-share-text">
          <span className="kicker">Your skill shape, as a card</span>
          <span className="hand">Put it where recruiters look.</span>
          <span className="db-fine">For your LinkedIn and your GitHub README, drawn from this graph.</span>
          <span className="btn btn-sm btn-primary db-share-go">Share my skill shape</span>
        </span>
      </ShareButton>
    );
  }

  const copy = () => {
    window.clearTimeout(timer.current);
    const done = navigator.clipboard?.writeText(link);
    if (!done) return show();
    // the browser would not copy it: the sheet shows the link in a field to select
    void done.then(
      () => {
        setCopied(true);
        timer.current = window.setTimeout(() => setCopied(false), COPIED_MS);
      },
      () => show(),
    );
  };

  return (
    <div className="slip slip-tape tilt-r is-live db-share-slip" role="group" aria-labelledby="db-share-live-h">
      <RadarGlyph className="db-share-glyph" values={values} size={76} />
      <span className="db-share-text">
        <span className="kicker" id="db-share-live-h">
          Your card is live
        </span>
        <span className="hand">It is out there. Keep it current.</span>
        <span className="db-slip-foot">
          <button type="button" className="btn btn-sm btn-primary" aria-haspopup="dialog" aria-expanded={open} onClick={show}>
            Open sharing
          </button>
          <button type="button" className="btn btn-sm" onClick={copy}>
            {copied ? 'Copied' : 'Copy the link'}
          </button>
        </span>
        <span className="sr-only" role="status">
          {copied ? 'Link copied' : ''}
        </span>
      </span>
      {sheet}
    </div>
  );
}

/* ───────────────────────── the resume slip ───────────────────────── */

const QUOTE_MAX = 96;

function ResumeSlip({ profile }: { profile: Profile }) {
  const found = useMemo(() => notOnResume(profile), [profile]);
  const open = () => actions.openResume({ screen: 'resume' });
  if (!found) {
    return (
      <div className="slip slip-fold tilt-l db-resume-slip" role="note" aria-labelledby="db-slip-h">
        <span className="kicker" id="db-slip-h">
          Your resume
        </span>
        <span className="hand">One resume, a version for each next role.</span>
        <span className="db-slip-foot">
          <button type="button" className="btn btn-sm" onClick={open}>
            Open your resumes
          </button>
        </span>
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
    <div className="slip slip-fold tilt-l db-resume-slip" role="note" aria-labelledby="db-slip-h">
      <span className="kicker" id="db-slip-h">
        Your resume · not on it yet
      </span>
      <span className="hand">{names}.</span>
      <span className="db-cite">
        {first.source.label} · “{quote}”
      </span>
      {role && (
        <span className="db-fine">
          {items.length === 1 ? 'It counts' : 'Both count'} towards {role.title}.
        </span>
      )}
      <span className="db-slip-foot">
        <button type="button" className="btn btn-sm" onClick={add}>
          Add {items.length === 1 ? 'it' : 'both'}
        </button>
        <button type="button" className="db-link" onClick={open}>
          Your resumes →
        </button>
      </span>
    </div>
  );
}

/* ───────────────────────── the column ───────────────────────── */

export function Slips({ profile, tracked }: { profile: Profile; tracked: readonly TrackedJob[] }) {
  return (
    <aside className="db-slips" aria-label="Your notes, your card and your resume">
      <NotePile profileId={profile.id} tracked={tracked} />
      <ShareSlip profile={profile} />
      <ResumeSlip profile={profile} />
    </aside>
  );
}
