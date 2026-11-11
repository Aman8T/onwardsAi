/**
 * The pressed block: where its fact comes from (the kind of source, its lines when known, its words), and what may be
 * done to it in this resume. Edit, Move up, Move down, Leave out and Reset each send one operation; the server's
 * answer replaces the resume on screen. Nothing is checked while the person writes: the checks run at Finalise.
 */
import { useEffect, useId, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { markText, sourceOf } from './model';
import type { SheetLine, Source } from './model';
import { SourceLine } from './parts';
import { MAX_LINE_CHARS } from './types';
import type { Busy, Fact, LineWording, ResumeDetail, ResumeOp, ResumeState } from './types';

interface BlockPanelProps {
  /** The pressed line; undefined when nothing is pressed. */
  line: SheetLine | undefined;
  facts: ReadonlyMap<string, Fact>;
  state: ResumeState;
  busy: Busy | null;
  onOp: (op: ResumeOp) => Promise<ResumeDetail | null>;
  /** Under the pressed line (a phone), instead of above the resume. */
  inline?: boolean;
  /** What other resumes use for the lines of this one (the panel shows the pressed line's). */
  wordings?: readonly LineWording[];
}

/** One line of text, as a resume line is: line breaks become spaces. */
const oneLine = (text: string) => text.replace(/\s*[\r\n]+\s*/g, ' ');

export function BlockPanel({ line, facts, state, busy, onOp, inline = false, wordings = [] }: BlockPanelProps) {
  const bullet = line?.bullet;
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState('');
  const field = useRef<HTMLTextAreaElement>(null);
  const editButton = useRef<HTMLButtonElement>(null);
  const fieldId = useId();
  const bulletId = bullet?.id;

  // another block was pressed: an open edit belongs to the one before
  useEffect(() => {
    setEditing(false);
  }, [bulletId]);

  useEffect(() => {
    if (!editing) return;
    const el = field.current;
    if (!el) return;
    el.focus();
    el.setSelectionRange(el.value.length, el.value.length);
  }, [editing]);

  if (!line || !bullet) {
    return (
      <div className="panel rs-block is-empty" role="group" aria-label="The block you pressed">
        <span className="kicker">No block pressed</span>
        <p className="rs-small">Press a line of the resume to see where it comes from, and to move it, leave it out or put it in your own words.</p>
        <p className="rs-fine">Names, titles, employers and dates read the same on every resume, so they are not pressed.</p>
      </div>
    );
  }

  const sources: Source[] = [];
  for (const id of bullet.factIds) {
    const s = sourceOf(facts.get(id));
    if (s && !sources.some((x) => x.label === s.label && x.quote === s.quote)) sources.push(s);
  }
  const edited = line.marks.includes('edited') || bullet.status === 'needs_user';
  const saving = busy?.kind === 'op' && busy.target === bullet.id;
  const locked = busy !== null;
  const text = oneLine(draft).trim();
  const canSave = !locked && text.length > 0 && text.length <= MAX_LINE_CHARS && text !== bullet.text;

  const elsewhere = wordings.filter((w) => w.lineId === bullet.id && w.text !== bullet.text);
  const send = (op: ResumeOp) => void onOp(op);
  const startEdit = () => {
    setDraft(bullet.text);
    setEditing(true);
  };
  const stopEdit = () => {
    setEditing(false);
    requestAnimationFrame(() => editButton.current?.focus());
  };
  const save = async (e?: FormEvent) => {
    e?.preventDefault();
    if (!canSave) return;
    const done = await onOp({ op: 'edit', bulletId: bullet.id, text });
    if (done) stopEdit();
  };

  return (
    <div className={`panel rs-block${inline ? ' is-inline' : ''}`} role="group" aria-label="The block you pressed">
      <div className="rs-block-head">
        <span className="kicker">
          Block <span className="numeral">{line.no}</span> in this resume
        </span>
        {line.marks.length > 0 && <span className="rs-cite rs-accent">{markText(line.marks)}</span>}
      </div>

      {editing ? (
        <form className="rs-edit" onSubmit={(e) => void save(e)}>
          <label className="kicker" htmlFor={fieldId}>
            The line, in your words
          </label>
          <textarea
            ref={field}
            id={fieldId}
            className="field rs-edit-field"
            rows={3}
            maxLength={MAX_LINE_CHARS}
            value={draft}
            readOnly={saving}
            onChange={(e) => setDraft(oneLine(e.target.value))}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                void save();
              } else if (e.key === 'Escape') {
                e.preventDefault();
                stopEdit();
              }
            }}
          />
          <div className="rs-edit-foot">
            <span className="rs-fine">
              <span className="numeral">{draft.length}</span> of <span className="numeral">{MAX_LINE_CHARS}</span> characters
            </span>
            <span className="rs-actions">
              <button type="submit" className="btn btn-sm btn-primary rs-btn" disabled={!canSave}>
                {saving ? 'Saving…' : 'Save'}
              </button>
              <button type="button" className="btn btn-sm rs-btn" disabled={saving} onClick={stopEdit}>
                Cancel
              </button>
            </span>
          </div>
          <p className="rs-fine">Type anything. Nothing is checked while you write; the checks run when you finalise.</p>
        </form>
      ) : (
        <p className="rs-small rs-strong">{bullet.text}</p>
      )}

      {sources.length > 0 ? (
        <ul className="rs-sources" aria-label="Where it comes from">
          {sources.map((s, i) => (
            <li key={i}>
              <SourceLine source={s} />
            </li>
          ))}
        </ul>
      ) : (
        <p className="rs-fine">The source of this line did not come with the resume.</p>
      )}

      {!editing && elsewhere.length > 0 && (
        <section className="rs-elsewhere" aria-label="Worded differently elsewhere">
          <h3 className="kicker">Worded differently elsewhere</h3>
          <ul className="rs-rows rs-rows-in">
            {elsewhere.map((w) => {
              const working = saving && busy?.kind === 'op';
              return (
                <li key={`${w.fromResumeId}:${w.text}`} className="rs-row rs-row-top">
                  <span className="rs-row-main">
                    <span className="rs-small">{w.text}</span>
                    <span className="rs-cite">from {w.fromTitle}</span>
                  </span>
                  <button type="button" className="btn btn-sm rs-btn" disabled={locked} aria-busy={working} onClick={() => send({ op: 'edit', bulletId: bullet.id, text: w.text })}>
                    Use this wording
                  </button>
                </li>
              );
            })}
          </ul>
        </section>
      )}

      {!editing && (
        <div className="rs-actions" role="group" aria-label="What you can do with this block">
          <button ref={editButton} type="button" className="btn btn-sm rs-btn" disabled={locked} onClick={startEdit}>
            Edit
          </button>
          <button type="button" className="btn btn-sm rs-btn" disabled={locked || !line.canUp} onClick={() => send({ op: 'move', bulletId: bullet.id, dir: 'up' })}>
            Move up
          </button>
          <button type="button" className="btn btn-sm rs-btn" disabled={locked || !line.canDown} onClick={() => send({ op: 'move', bulletId: bullet.id, dir: 'down' })}>
            Move down
          </button>
          <button type="button" className="btn btn-sm btn-quiet rs-btn" disabled={locked} onClick={() => send({ op: 'leave_out', bulletId: bullet.id })}>
            Leave out
          </button>
          {edited && (
            <button type="button" className="btn btn-sm btn-quiet rs-btn" disabled={locked} onClick={() => send({ op: 'reset', bulletId: bullet.id })}>
              Reset
            </button>
          )}
        </div>
      )}

      <p className="rs-fine" role="status">
        {saving && !editing ? 'Saving…' : ''}
      </p>
      {edited ? (
        <p className="rs-fine">These are your words, not checked yet. The check runs when you finalise. Reset brings back the words of the source.</p>
      ) : state === 'final' ? (
        <p className="rs-fine">Changing a line starts a new draft. The finalised version stays as it is until you finalise again.</p>
      ) : null}
    </div>
  );
}
