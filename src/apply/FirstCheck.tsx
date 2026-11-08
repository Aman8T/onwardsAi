/**
 * The first check, about 30 seconds (canvas 11, spec 2.2 layer 2). Shown once, before the first Apply for me: the facts
 * read from the resume, each with Edit, and the two things almost every Indian form asks (the notice period, and whether
 * the person can work in India without sponsorship). The safe defaults are shown, not asked. "Skip, ask me on the form"
 * is always there; nothing here is required.
 *
 * Work authorisation is stored for India only (country `IN`); another country is asked by the form that needs it and
 * never inferred from this answer.
 */
import { useId, useState } from 'react';
import type { FormEvent } from 'react';
import type { FirstCheckRequest, QuestionAnswer, SavedAnswer, WorkAuthorization, YourDetails } from '../../shared/apply';
import { APPLY_DETAIL_MAX_CHARS } from '../../shared/apply';
import { errorText } from './api';

/** The notice period choices; the label is the value forms are filled with. */
export const NOTICE_OPTIONS = ['Immediate', '15 days', '30 days', '60 days', '90 days', 'Serving notice'] as const;

export type WorkAuthChoice = 'citizen' | 'other_basis' | 'no';

export const WORK_AUTH_OPTIONS: readonly (readonly [WorkAuthChoice, string, WorkAuthorization])[] = [
  ['citizen', "Yes, I'm a citizen", { country: 'IN', authorised: 'Yes, citizen', needsSponsorship: 'No' }],
  ['other_basis', 'Yes, on another basis', { country: 'IN', authorised: 'Yes, on another basis', needsSponsorship: 'No' }],
  ['no', 'No', { country: 'IN', authorised: 'No', needsSponsorship: 'Yes' }],
];

/** What the resume gave, in the order the canvas shows it. */
const RESUME_GROUPS = new Set(['you', 'work', 'education']);

export function resumeRows(details: Pick<YourDetails, 'answers'>): SavedAnswer[] {
  return details.answers.filter((a) => a.source === 'resume' && (!a.group || RESUME_GROUPS.has(a.group)) && !a.private);
}

export function currentNotice(details: Pick<YourDetails, 'answers'>): string | undefined {
  const v = details.answers.find((a) => a.questionKey === 'noticePeriod')?.value;
  return NOTICE_OPTIONS.find((o) => o === v);
}

export function currentWorkAuth(details: Pick<YourDetails, 'workAuthorization'>): WorkAuthChoice | undefined {
  const india = details.workAuthorization.find((w) => w.country.toUpperCase() === 'IN');
  return india ? WORK_AUTH_OPTIONS.find(([, , w]) => w.authorised === india.authorised)?.[0] : undefined;
}

/** The request "Looks right, continue" sends: only what the person set or changed. */
export function firstCheckBody(notice: string | undefined, auth: WorkAuthChoice | undefined, edits: Record<string, string>): FirstCheckRequest {
  const body: FirstCheckRequest = { skipped: false };
  if (notice) body.noticePeriod = notice;
  const w = WORK_AUTH_OPTIONS.find(([id]) => id === auth)?.[2];
  if (w) body.workAuthorization = { ...w };
  const changed: QuestionAnswer[] = Object.entries(edits)
    .map(([questionKey, value]) => ({ questionKey, value: value.trim(), save: true }))
    .filter((e) => e.value);
  if (changed.length > 0) body.edits = changed;
  return body;
}

export interface FirstCheckProps {
  details: YourDetails;
  onSubmit: (body: FirstCheckRequest) => Promise<void>;
}

export function FirstCheck({ details, onSubmit }: FirstCheckProps) {
  const rows = resumeRows(details);
  const [notice, setNotice] = useState<string | undefined>(() => currentNotice(details));
  const [auth, setAuth] = useState<WorkAuthChoice | undefined>(() => currentWorkAuth(details));
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [editing, setEditing] = useState<string | null>(null);
  const [sending, setSending] = useState<'continue' | 'skip' | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const base = useId();

  const send = async (kind: 'continue' | 'skip', body: FirstCheckRequest) => {
    setSending(kind);
    setErr(null);
    try {
      await onSubmit(body);
    } catch (e) {
      setErr(errorText(e, 'Could not save that. Try again.'));
      setSending(null);
    }
  };

  const submit = (e: FormEvent) => {
    e.preventDefault();
    void send('continue', firstCheckBody(notice, auth, edits));
  };

  return (
    <form className="ap-stack ap-narrow" onSubmit={submit} aria-labelledby={`${base}-title`}>
      <div className="ap-head">
        <p className="kicker">Apply for me · the first time · about 30 seconds</p>
        <h1 id={`${base}-title`} className="serif ap-title">
          Check what we read, answer two things.
        </h1>
        <p className="ap-muted">Everything else, we ask only when a form needs it, and only once.</p>
      </div>

      <section className="panel ap-panel" aria-labelledby={`${base}-read`}>
        <div className="ap-ghead">
          <h2 id={`${base}-read`} className="ap-h3">
            From your resume
          </h2>
          <span className="ap-muted ap-small">Tap Edit only if something is wrong</span>
        </div>
        {rows.length === 0 ? (
          <p className="ap-muted">We could not read these from your resume yet. A form that needs them will ask.</p>
        ) : (
          <div className="ap-rows">
            {rows.map((a) => {
              const id = `${base}-${a.questionKey}`;
              const open = editing === a.questionKey;
              return (
                <div key={a.questionKey} className="ap-frow">
                  <span className="ap-q">{a.question}</span>
                  {open ? (
                    <span className="ap-v">
                      <label className="sr-only" htmlFor={id}>
                        {a.question}
                      </label>
                      <input
                        id={id}
                        className="field"
                        maxLength={APPLY_DETAIL_MAX_CHARS}
                        value={edits[a.questionKey] ?? a.value ?? ''}
                        onChange={(e) => setEdits({ ...edits, [a.questionKey]: e.target.value })}
                        autoFocus
                      />
                    </span>
                  ) : (
                    <span className="ap-v">{edits[a.questionKey] ?? a.value ?? ''}</span>
                  )}
                  <button
                    type="button"
                    className="btn btn-quiet ap-row-btn"
                    aria-label={open ? `Done editing ${a.question.toLowerCase()}` : `Edit ${a.question.toLowerCase()}`}
                    aria-expanded={open}
                    onClick={() => setEditing(open ? null : a.questionKey)}
                  >
                    {open ? 'Done' : 'Edit'}
                  </button>
                </div>
              );
            })}
          </div>
        )}
      </section>

      <section className="panel ap-panel ap-stack" aria-labelledby={`${base}-two`}>
        <h2 id={`${base}-two`} className="ap-h3">
          Two things nearly every Indian form asks
        </h2>
        <div className="ap-q-block" role="radiogroup" aria-labelledby={`${base}-np`}>
          <p id={`${base}-np`} className="ap-strong">
            Your notice period
          </p>
          <div className="ap-pills">
            {NOTICE_OPTIONS.map((o) => (
              <label key={o} className="ap-pill">
                <input type="radio" name={`${base}-np`} checked={notice === o} onChange={() => setNotice(o)} />
                {o}
              </label>
            ))}
          </div>
        </div>
        <div className="ap-q-block" role="radiogroup" aria-labelledby={`${base}-wa`}>
          <p id={`${base}-wa`} className="ap-strong">
            Can you work in India without sponsorship?
          </p>
          <div className="ap-pills">
            {WORK_AUTH_OPTIONS.map(([id, label]) => (
              <label key={id} className="ap-pill">
                <input type="radio" name={`${base}-wa`} checked={auth === id} onChange={() => setAuth(id)} />
                {label}
              </label>
            ))}
          </div>
        </div>
        <div className="ap-well-box">
          <p className="ap-strong">Already set for you, change any time</p>
          <p className="ap-muted">
            Expected CTC: "open to discussion". Gender and other self-ID: "prefer not to say". Relocation and bonds: asked per
            offer.
          </p>
        </div>
      </section>

      {err && (
        <p className="ap-error" role="alert">
          {err}
        </p>
      )}
      <div className="ap-actions">
        <button type="submit" className="btn btn-primary" disabled={sending !== null}>
          {sending === 'continue' ? 'Saving' : 'Looks right, continue'}
        </button>
        <button type="button" className="btn btn-quiet" disabled={sending !== null} onClick={() => void send('skip', { skipped: true })}>
          Skip, ask me on the form
        </button>
      </div>
    </form>
  );
}
