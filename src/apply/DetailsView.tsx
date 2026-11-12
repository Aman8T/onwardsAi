/**
 * `/apply/details`: Your details, what forms will be told (canvas 2, spec 2 and 2.2 "Keeping answers true").
 *
 * Every kept answer with its source and date, the questions no form has asked yet (answerable early, never required),
 * one "Still true?" check for the first answer that went stale, the self-ID switch (off by default), the never-stored
 * list and the default resume with Replace. Filters are `aria-pressed` chips whose counts are computed here. A private
 * answer (current CTC) is shown as "Saved, private" and never read back. Under 760 px each row stacks and an edit opens
 * in a bottom sheet.
 *
 * `DetailsView` is presentational; `DetailsScreen` reads and writes `/api/apply/details`.
 */
import { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import type { AnswerGroup, BankQuestion, FieldKind, SaveAnswerRequest, SavedAnswer, WorkAuthorization, YourDetails } from '../../shared/apply';
import { APPLY_DETAIL_MAX_CHARS, APPLY_RESUME_MAX_BYTES } from '../../shared/apply';
import { Sheet } from '../auth/Sheet';
import { shortDate } from '../billing/format';
import { NARROW_QUERY, timeAgo, useMediaQuery } from '../lib/ui';
import { applyApi, errorText } from './api';
import { LIST_ROUTE, linkTo } from './route';
import type { Navigate } from './route';

/* ───────────────────────── what code computes ───────────────────────── */

export type DetailFilter = 'all' | 'resume' | 'you' | 'default' | 'check' | 'not_asked';

export const DETAIL_FILTERS: readonly (readonly [DetailFilter, string])[] = [
  ['all', 'All'],
  ['resume', 'From your resume'],
  ['you', 'From you'],
  ['default', 'Defaults'],
  ['check', 'To check'],
  ['not_asked', 'Not asked yet'],
];

const FROM_YOU = new Set(['first_check', 'form', 'you']);

export function answerMatches(a: SavedAnswer, f: DetailFilter): boolean {
  switch (f) {
    case 'all':
      return true;
    case 'resume':
      return a.source === 'resume';
    case 'you':
      return FROM_YOU.has(a.source);
    case 'default':
      return a.source === 'default';
    case 'check':
      return a.stale === true;
    case 'not_asked':
      return false;
  }
}

export function filterCounts(d: Pick<YourDetails, 'answers' | 'notAskedYet'>): Record<DetailFilter, number> {
  const counts: Record<DetailFilter, number> = { all: 0, resume: 0, you: 0, default: 0, check: 0, not_asked: d.notAskedYet.length };
  for (const a of d.answers) {
    for (const [f] of DETAIL_FILTERS) if (f !== 'all' && answerMatches(a, f)) counts[f] += 1;
  }
  counts.all = d.answers.length + d.notAskedYet.length;
  return counts;
}

/** Self-ID answers sit in their own panel, beside the switch. */
const GROUPS: readonly (readonly [AnswerGroup | 'other', string, string?])[] = [
  ['you', 'You', 'Name, contact, place'],
  ['work', 'Work right now'],
  ['education', 'Education and marks', 'Scores as given: CGPA is never turned into a percentage'],
  ['registrations', 'Registrations and licences'],
  ['where_how', 'Where and how you work'],
  ['other', 'Other answers from forms'],
];

const groupOf = (g: AnswerGroup | undefined): AnswerGroup | 'other' => g ?? 'other';

/** The first answer to ask "Still true?" about: the oldest stale one. */
export function stillTrueCandidate(answers: readonly SavedAnswer[]): SavedAnswer | undefined {
  const stale = answers.filter((a) => a.stale);
  const at = (a: SavedAnswer) => Date.parse(a.confirmedAt ?? a.answeredAt) || 0;
  return stale.sort((a, b) => at(a) - at(b))[0];
}

/** What the value cell says. A private answer is never read back. */
export function valueText(a: SavedAnswer): string {
  if (a.private) return 'Saved, private';
  if (a.askEachTime && !a.value) return 'Ask me each time';
  return a.value ?? 'Not answered';
}

/** Where an answer came from, with its date. */
export function sourceText(a: SavedAnswer): string {
  const on = a.answeredAt ? shortDate(a.answeredAt) : '';
  const dated = (s: string) => (on ? `${s} · ${on}` : s);
  let text: string;
  switch (a.source) {
    case 'resume':
      text = 'Resume';
      break;
    case 'first_check':
      text = dated('You, first check');
      break;
    case 'form':
      text = dated(a.answeredOn ? `You, on the ${a.answeredOn} form` : 'You, on a form');
      break;
    case 'you':
      text = dated('You');
      break;
    case 'default':
      text = 'Default · change any time';
      break;
  }
  return a.private ? `${text} · sent only where a form requires it` : text;
}

function countryName(code: string): string {
  try {
    return new Intl.DisplayNames(['en'], { type: 'region' }).of(code.toUpperCase()) ?? code;
  } catch {
    return code;
  }
}

export function workAuthText(w: WorkAuthorization): string {
  const sponsor = w.needsSponsorship ? (/^no\b/i.test(w.needsSponsorship) ? ', no sponsorship needed' : ', needs sponsorship') : '';
  return `${w.authorised}${sponsor}`;
}

/* ───────────────────────── the view ───────────────────────── */

interface Editing {
  questionKey: string;
  question: string;
  kind: FieldKind;
  options?: string[];
  value: string;
  askEachTime: boolean;
  isPrivate: boolean;
}

export interface DetailsViewProps {
  details: YourDetails;
  navigate: Navigate;
  onSave: (body: SaveAnswerRequest) => Promise<void>;
  onStillTrue: (questionKey: string) => Promise<void>;
  onSelfId: (on: boolean) => Promise<void>;
  onUpload: (file: File) => Promise<void>;
  onRemoveResume: () => Promise<void>;
  /** "Since" for the Still true line (fixed in the gallery). */
  now?: number;
  initialFilter?: DetailFilter;
}

export function DetailsView(props: DetailsViewProps) {
  const { details, navigate } = props;
  const narrow = useMediaQuery(NARROW_QUERY);
  const [filter, setFilter] = useState<DetailFilter>(props.initialFilter ?? 'all');
  const [editing, setEditing] = useState<Editing | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const counts = useMemo(() => filterCounts(details), [details]);
  const check = stillTrueCandidate(details.answers);
  const sheetTitle = useId();

  const editAnswer = (a: SavedAnswer) =>
    setEditing({
      questionKey: a.questionKey,
      question: a.question,
      kind: 'text',
      value: a.private ? '' : (a.value ?? ''),
      askEachTime: a.askEachTime,
      isPrivate: a.private === true,
    });
  const editQuestion = (q: BankQuestion) =>
    setEditing({ questionKey: q.questionKey, question: q.question, kind: q.kind, options: q.options, value: '', askEachTime: false, isPrivate: false });

  const save = async (body: SaveAnswerRequest) => {
    await props.onSave(body);
    setEditing(null);
    setNotice('Saved. Forms from now on use it; applications already sent are not changed.');
  };

  const shownAnswers = details.answers.filter((a) => a.group !== 'self_id' && answerMatches(a, filter));
  const shownQuestions = filter === 'all' || filter === 'not_asked' ? details.notAskedYet.filter((q) => q.group !== 'self_id') : [];
  const countries = new Set(details.answers.map((a) => a.country?.toUpperCase()).filter(Boolean));
  const workAuth = filter === 'all' || filter === 'you' ? details.workAuthorization.filter((w) => !countries.has(w.country.toUpperCase())) : [];
  const selfIdAnswers = details.answers.filter((a) => a.group === 'self_id');
  // "all" and "not asked yet" always show the other-country row, so only the other filters can come up empty
  const nothingMatches = shownAnswers.length + shownQuestions.length + workAuth.length === 0 && filter !== 'all' && filter !== 'not_asked';

  const editor = editing && (
    <AnswerEditor key={editing.questionKey} editing={editing} titleId={narrow ? sheetTitle : undefined} onSave={save} onCancel={() => setEditing(null)} />
  );

  return (
    <div className="ap-stack ap-details">
      <div className="ap-head">
        <p className="kicker">
          <a {...linkTo(LIST_ROUTE, navigate)}>Applications</a> · Your details
        </p>
        <h1 className="serif ap-title">What forms will be told about you.</h1>
        <p className="ap-muted ap-measure">
          Each answer says where it came from. Nothing here is guessed, and nothing you leave empty blocks you: a form that needs
          it will ask.
        </p>
      </div>

      <div className="ap-headrow">
        <div className="ap-filters" role="group" aria-label="Show answers">
          {DETAIL_FILTERS.map(([id, label]) => (
            <button key={id} type="button" className="ap-filter" aria-pressed={filter === id} onClick={() => setFilter(id)}>
              {label} <span className="numeral">{counts[id]}</span>
            </button>
          ))}
        </div>
        <p className="ap-muted ap-small">Changing an answer never changes an application already sent.</p>
      </div>

      <p className="ap-muted" role="status" aria-live="polite">
        {notice}
      </p>

      {check && <StillTrue answer={check} now={props.now} onStillTrue={props.onStillTrue} onChange={() => editAnswer(check)} />}

      {nothingMatches && <p className="ap-muted">Nothing matches this filter.</p>}

      {GROUPS.map(([group, label, note]) => {
        const answers = shownAnswers.filter((a) => groupOf(a.group) === group);
        const questions = shownQuestions.filter((q) => q.group === group);
        const auths = group === 'where_how' ? workAuth : [];
        const otherCountry = group === 'where_how' && (filter === 'all' || filter === 'not_asked');
        if (answers.length + questions.length + auths.length === 0 && !otherCountry) return null;
        return (
          <section key={group} className="panel ap-panel" aria-label={label}>
            <div className="ap-ghead">
              <h2 className="ap-h3">{label}</h2>
              {note && <span className="ap-muted ap-small">{note}</span>}
            </div>
            <div className="ap-rows">
              {answers.map((a) => (
                <div key={a.questionKey}>
                  <div className="ap-drow">
                    <span className="ap-q">{a.question}</span>
                    <span className="ap-v">{valueText(a)}</span>
                    <span className="ap-src">
                      {sourceText(a)}
                      {a.stale && <span className="chip ap-chip ap-tone-you">To check</span>}
                    </span>
                    <button type="button" className="btn btn-quiet ap-row-btn" aria-label={`Edit ${a.question.toLowerCase()}`} onClick={() => editAnswer(a)}>
                      Edit
                    </button>
                  </div>
                  {!narrow && editing?.questionKey === a.questionKey && editor}
                </div>
              ))}
              {auths.map((w) => (
                <div key={w.country} className="ap-drow">
                  <span className="ap-q">Work in {countryName(w.country)}</span>
                  <span className="ap-v">{workAuthText(w)}</span>
                  <span className="ap-src">You · asked for this country only</span>
                  <span />
                </div>
              ))}
              {questions.map((q) => (
                <div key={q.questionKey}>
                  <div className="ap-drow ap-drow-na">
                    <span className="ap-q">{q.question}</span>
                    <span className="ap-v">Not asked yet</span>
                    <span className="ap-src">Asked only when a form needs it</span>
                    <button type="button" className="btn btn-quiet ap-row-btn" aria-label={`Answer ${q.question.toLowerCase()} now`} onClick={() => editQuestion(q)}>
                      Answer
                    </button>
                  </div>
                  {!narrow && editing?.questionKey === q.questionKey && editor}
                </div>
              ))}
              {otherCountry && (
                <div className="ap-drow ap-drow-na">
                  <span className="ap-q">Work in any other country</span>
                  <span className="ap-v">Not asked yet</span>
                  <span className="ap-src">Asked per form; never carried across countries</span>
                  <span />
                </div>
              )}
            </div>
          </section>
        );
      })}

      <div className="ap-two">
        <SelfId details={details} answers={selfIdAnswers} onSelfId={props.onSelfId} />
        <section className="panel ap-panel" aria-label="Never stored">
          <div className="ap-ghead">
            <h2 className="ap-h3">Never stored</h2>
            <span className="chip ap-chip ap-tone-you">You type these</span>
          </div>
          <p>{details.neverStored.join(' · ')}</p>
          <p className="ap-muted ap-small">A form that requires one waits for you, and you type it on the employer's page.</p>
        </section>
      </div>

      <ResumePanel details={details} onUpload={props.onUpload} onRemove={props.onRemoveResume} />

      {narrow && (
        <Sheet open={editing !== null} onClose={() => setEditing(null)} labelledBy={sheetTitle} className="ap-sheet">
          {editor}
        </Sheet>
      )}
    </div>
  );
}

function StillTrue({ answer, now, onStillTrue, onChange }: { answer: SavedAnswer; now?: number; onStillTrue: (key: string) => Promise<void>; onChange: () => void }) {
  const [busyNow, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const titleId = useId();
  const set = Date.parse(answer.confirmedAt ?? answer.answeredAt);
  const keep = answer.private || !answer.value ? 'Still the same' : `Still ${answer.value}`;
  const confirm = async () => {
    setBusy(true);
    setErr(null);
    try {
      await onStillTrue(answer.questionKey);
    } catch (e) {
      setErr(errorText(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <section className="panel ap-panel ap-well ap-headrow" aria-labelledby={titleId}>
      <div>
        <p id={titleId} className="ap-strong">
          Still true? Your {answer.question.toLowerCase()} was set {Number.isFinite(set) ? timeAgo(set, now) : 'a while ago'}.
        </p>
        <p className="ap-muted ap-small">Notice period, CTC and city get a one-tap check every 90 days, never the whole form.</p>
        {err && (
          <p className="ap-error ap-small" role="alert">
            {err}
          </p>
        )}
      </div>
      <div className="ap-actions">
        <button type="button" className="btn" disabled={busyNow} onClick={() => void confirm()}>
          {keep}
        </button>
        <button type="button" className="btn btn-quiet" onClick={onChange}>
          Change
        </button>
      </div>
    </section>
  );
}

function AnswerEditor({ editing, titleId, onSave, onCancel }: { editing: Editing; titleId?: string; onSave: (b: SaveAnswerRequest) => Promise<void>; onCancel: () => void }) {
  const [value, setValue] = useState(editing.value);
  const [askEachTime, setAskEachTime] = useState(editing.askEachTime);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const base = useId();
  const inputId = `${base}-value`;
  const choice = editing.options && editing.options.length > 0;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (saving) return;
    const v = value.trim();
    if (!askEachTime && !v) {
      setErr('Type an answer, or choose "Ask me each time".');
      return;
    }
    setSaving(true);
    setErr(null);
    try {
      const body: SaveAnswerRequest = { questionKey: editing.questionKey, askEachTime };
      if (v) body.value = v;
      await onSave(body);
    } catch (error) {
      setErr(errorText(error, 'Could not save that. Try again.'));
      setSaving(false);
    }
  };

  return (
    <form className="ap-editor" onSubmit={submit} aria-labelledby={titleId}>
      {titleId && (
        <h2 id={titleId} className="ap-h3">
          {editing.question}
        </h2>
      )}
      {choice ? (
        <fieldset className="ap-fieldset">
          <legend className={titleId ? 'sr-only' : 'ap-label'}>{editing.question}</legend>
          <div className="ap-pills">
            {editing.options!.map((o) => (
              <label key={o} className="ap-pill">
                <input type="radio" name={`${base}-opt`} checked={value === o} onChange={() => setValue(o)} />
                {o}
              </label>
            ))}
          </div>
        </fieldset>
      ) : (
        <div className="ap-email-row">
          <label className={titleId ? 'sr-only' : 'ap-label'} htmlFor={inputId}>
            {editing.question}
          </label>
          {editing.kind === 'textarea' ? (
            <textarea id={inputId} className="field ap-textarea" rows={4} maxLength={APPLY_DETAIL_MAX_CHARS} value={value} onChange={(e) => setValue(e.target.value)} />
          ) : (
            <input
              id={inputId}
              className="field"
              type={editing.kind === 'email' || editing.kind === 'tel' || editing.kind === 'url' || editing.kind === 'date' ? editing.kind : 'text'}
              maxLength={APPLY_DETAIL_MAX_CHARS}
              value={value}
              placeholder={editing.isPrivate ? 'Type the new value; it stays private' : undefined}
              onChange={(e) => setValue(e.target.value)}
            />
          )}
        </div>
      )}
      <label className="ap-check">
        <input type="checkbox" checked={askEachTime} onChange={(e) => setAskEachTime(e.target.checked)} />
        Ask me each time
      </label>
      {err && (
        <p className="ap-error ap-small" role="alert">
          {err}
        </p>
      )}
      <div className="ap-actions">
        <button type="submit" className="btn btn-primary" disabled={saving}>
          {saving ? 'Saving' : 'Save'}
        </button>
        <button type="button" className="btn btn-quiet" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </form>
  );
}

function SelfId({ details, answers, onSelfId }: { details: YourDetails; answers: readonly SavedAnswer[]; onSelfId: (on: boolean) => Promise<void> }) {
  const [busyNow, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const toggle = async (on: boolean) => {
    setBusy(true);
    setErr(null);
    try {
      await onSelfId(on);
    } catch (e) {
      setErr(errorText(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <section className="panel ap-panel" aria-label="Self-identification">
      <div className="ap-ghead">
        <h2 className="ap-h3">Self-identification</h2>
      </div>
      <label className="ap-check">
        <input type="checkbox" checked={details.selfIdOnForms} disabled={busyNow} onChange={(e) => void toggle(e.target.checked)} />
        Use my answers on forms
      </label>
      <p className="ap-muted ap-small">
        {details.selfIdOnForms
          ? 'On: forms get the answers below where they ask. They are never sent to a model.'
          : 'Off: forms get "Prefer not to say" for gender, disability and similar questions where they allow it. A form that requires one asks you.'}
      </p>
      {answers.length > 0 && (
        <ul className="ap-plain-list">
          {answers.map((a) => (
            <li key={a.questionKey} className="ap-small">
              {a.question}: {valueText(a)}
            </li>
          ))}
        </ul>
      )}
      {err && (
        <p className="ap-error ap-small" role="alert">
          {err}
        </p>
      )}
    </section>
  );
}

const RESUME_TYPES = '.pdf,.docx,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document';

function ResumePanel({ details, onUpload, onRemove }: { details: YourDetails; onUpload: (f: File) => Promise<void>; onRemove: () => Promise<void> }) {
  const input = useRef<HTMLInputElement>(null);
  const [state, setState] = useState<string | null>(null);
  const [busyNow, setBusy] = useState(false);
  const titleId = useId();
  const run = async (task: () => Promise<void>, done: string) => {
    setBusy(true);
    setState(null);
    try {
      await task();
      setState(done);
    } catch (e) {
      setState(errorText(e));
    } finally {
      setBusy(false);
    }
  };
  const pick = (file: File | undefined) => {
    if (input.current) input.current.value = '';
    if (!file) return;
    if (file.size > APPLY_RESUME_MAX_BYTES) {
      setState('That file is over 5 MB. Save a smaller PDF or DOCX and try again.');
      return;
    }
    void run(() => onUpload(file), 'Resume saved.');
  };
  return (
    <section className="panel ap-panel ap-headrow" aria-labelledby={titleId}>
      <div>
        <h2 id={titleId} className="ap-h3">
          Resume used by default
        </h2>
        <p className="ap-muted ap-small">
          {details.hasResume
            ? `${details.resumeName ?? 'Your resume'}. Each application can use a resume fitted to its role instead.`
            : 'No resume saved yet. Applications with your saved resume need one; PDF or DOCX, up to 5 MB.'}
        </p>
        <p className="ap-muted ap-small" role="status" aria-live="polite">
          {state}
        </p>
      </div>
      <div className="ap-actions">
        <input ref={input} type="file" accept={RESUME_TYPES} className="sr-only" tabIndex={-1} aria-hidden="true" onChange={(e) => pick(e.target.files?.[0])} />
        <button type="button" className="btn" disabled={busyNow} onClick={() => input.current?.click()}>
          {details.hasResume ? 'Replace' : 'Upload a resume'}
        </button>
        {details.hasResume && (
          <button type="button" className="btn btn-quiet" disabled={busyNow} onClick={() => void run(onRemove, 'Resume removed.')}>
            Remove
          </button>
        )}
      </div>
    </section>
  );
}

/* ───────────────────────── the container ───────────────────────── */

export function DetailsScreen({ navigate }: { navigate: Navigate }) {
  const [details, setDetails] = useState<YourDetails | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setDetails(await applyApi.details());
      setError(null);
    } catch (err) {
      setError(errorText(err, 'Could not load your details. Try again.'));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  if (!details) {
    return error ? (
      <p className="ap-error" role="alert">
        {error}
      </p>
    ) : (
      <p className="ap-muted" role="status">
        Loading your details
      </p>
    );
  }
  return (
    <DetailsView
      details={details}
      navigate={navigate}
      onSave={async (body) => setDetails(await applyApi.saveAnswer(body))}
      onStillTrue={async (key) => setDetails(await applyApi.stillTrue(key))}
      onSelfId={async (on) => setDetails(await applyApi.setSelfId(on))}
      onUpload={async (file) => {
        await applyApi.uploadResume(file);
        await load();
      }}
      onRemoveResume={async () => {
        await applyApi.removeResume();
        await load();
      }}
    />
  );
}
