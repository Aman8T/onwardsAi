/**
 * One section of the form in Review (canvas 4), and what code computes for it: the groups in the form's own order with
 * their counts, the words for each value's source, the summary of where the values came from, and the numbered
 * citations under a written answer.
 *
 *   - A choice field shows the chosen option among the others.
 *   - A locked value is read-only, with a lock and the reason.
 *   - A written answer (a text area) is shown in full, with its numbered facts and any sentence the verifier dropped.
 *   - A signature or declaration says it was typed by the person for this form only.
 *   - Every other value with an answer key can be changed: the change goes in as an answer and the form is filled again.
 */
import { useId, useState } from 'react';
import type { CitedFact, FieldSource, FilledField, NeedsInput, QuestionAnswer, ResumeChoice } from '../../shared/apply';
import { plural } from '../lib/ui';
import { NeedCard, initialSave } from './NeedCard';
import type { NeedDraft } from './NeedCard';

/**
 * A field as Review shows it. `dropped` is the sentences the fact check removed from a written answer; it is not in the
 * contract yet (shared/apply.ts), so it is read when the server sends it and absent otherwise.
 */
export type ReviewField = FilledField & { dropped?: string[] };

/** The words beside a value: where it came from. */
export function sourceText(field: Pick<FilledField, 'source' | 'attestation' | 'confidence'>, resume?: ResumeChoice): string {
  if (field.attestation) return 'Typed by you, for this form only';
  const words: Record<FieldSource, string> = {
    details: 'Your details',
    resume: resume === 'tailored' ? "This match's resume" : 'Your resume',
    fact: 'One of your facts',
    answers: 'Your earlier answer',
    model: 'Written from your facts',
    user: 'Typed by you',
    default: 'A default',
  };
  const base = field.source ? words[field.source] : 'Read from the form';
  return field.source === 'model' && field.confidence ? `${base} · ${field.confidence} confidence` : base;
}

/** The summary chip's words for a count of one source ("12 your details"). */
const SUMMARY_WORD: Record<FieldSource | 'form', [string, string]> = {
  details: ['your details', 'your details'],
  resume: ['from your resume', 'from your resume'],
  fact: ['from your facts', 'from your facts'],
  answers: ['earlier answer', 'earlier answers'],
  model: ['written from your facts', 'written from your facts'],
  user: ['typed by you', 'typed by you'],
  default: ['default', 'defaults'],
  form: ['read from the form', 'read from the form'],
};

const SUMMARY_ORDER: readonly (FieldSource | 'form')[] = ['details', 'resume', 'answers', 'fact', 'model', 'user', 'default', 'form'];

export interface SourceCount {
  source: FieldSource | 'form';
  n: number;
  text: string;
}

/** Where the values come from, counted in code, in a fixed order; a signature counts as typed by you. */
export function sourceSummary(fields: readonly FilledField[], resume?: ResumeChoice): SourceCount[] {
  const counts = new Map<FieldSource | 'form', number>();
  for (const f of fields) {
    const s: FieldSource | 'form' = f.attestation ? 'user' : (f.source ?? 'form');
    counts.set(s, (counts.get(s) ?? 0) + 1);
  }
  return SUMMARY_ORDER.filter((s) => (counts.get(s) ?? 0) > 0).map((s) => {
    const n = counts.get(s) ?? 0;
    const [one, many] = SUMMARY_WORD[s];
    const word = s === 'resume' && resume === 'tailored' ? "from this match's resume" : n === 1 ? one : many;
    return { source: s, n, text: `${n} ${word}` };
  });
}

export interface FieldGroup {
  title: string;
  fields: ReviewField[];
}

const FALLBACK_GROUP = 'The form';

/** The form's sections in the form's order (by each section's first field), and the fields inside each in order. */
export function groupFields(fields: readonly ReviewField[]): FieldGroup[] {
  const pos = (f: FilledField, i: number) => f.order ?? 10_000 + i;
  const indexed = fields.map((f, i) => ({ f, at: pos(f, i) })).sort((a, b) => a.at - b.at);
  const groups = new Map<string, FieldGroup>();
  for (const { f } of indexed) {
    const title = f.group?.trim() || FALLBACK_GROUP;
    let g = groups.get(title);
    if (!g) {
      g = { title, fields: [] };
      groups.set(title, g);
    }
    g.fields.push(f);
  }
  return [...groups.values()];
}

export interface Citation {
  n: number;
  id: string;
  text: string;
}

/** The numbered facts under a written answer, in the order the answer cites them; an unknown id keeps its place. */
export function citations(field: Pick<FilledField, 'factIds'>, facts: readonly CitedFact[] = []): Citation[] {
  const byId = new Map(facts.map((f) => [f.id, f.text]));
  return (field.factIds ?? []).map((id, i) => ({ n: i + 1, id, text: byId.get(id) ?? 'A fact that is no longer in your fact book' }));
}

/** "Written from 2 of your facts · 1 sentence dropped". */
export function citeLine(field: ReviewField): string {
  const n = field.factIds?.length ?? 0;
  const parts = [n === 0 ? 'Written from your facts' : n === 1 ? 'Written from one of your facts' : `Written from ${n} of your facts`];
  const dropped = field.dropped?.length ?? 0;
  if (dropped > 0) parts.push(`${plural(dropped, 'sentence')} dropped`);
  return parts.join(' · ');
}

const CHOICE_KINDS = new Set(['select', 'radio', 'combobox', 'checkboxGroup']);
/** Long option lists (countries, years) show the chosen one and how many others there were. */
const MAX_SHOWN_OPTIONS = 6;

export function LockIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.3" aria-hidden="true">
      <rect x="2.5" y="5.5" width="7" height="5" rx="1" />
      <path d="M4 5.5V4a2 2 0 014 0v1.5" />
    </svg>
  );
}

function Choices({ field }: { field: ReviewField }) {
  const options = field.options ?? [];
  const chosen = field.value ?? '';
  if (options.length > MAX_SHOWN_OPTIONS) {
    return (
      <span>
        {chosen || <span className="ap-muted">Left empty</span>}
        <span className="ap-muted ap-small"> · one of {options.length} choices</span>
      </span>
    );
  }
  return (
    <ul className="ar-opts" aria-label={`Choices for ${field.label}`}>
      {options.map((opt) => {
        const on = opt === chosen;
        return (
          <li key={opt} className={on ? 'ar-opt ar-opt-chosen' : 'ar-opt'}>
            <span className="ar-opt-mark" aria-hidden="true">
              {on ? '✓' : ''}
            </span>
            {opt}
            {on && <span className="sr-only"> (chosen)</span>}
          </li>
        );
      })}
    </ul>
  );
}

/** A field turned into the question its change is sent as. */
export function changeNeed(field: FilledField): NeedsInput {
  return {
    key: field.key,
    label: field.label,
    kind: field.kind,
    required: field.required,
    reason: 'Your answer replaces this value, and the form is filled again before you approve.',
    options: field.options,
    // a review row without its own key is answered by its field key (the planner reads typed values by field key first)
    questionKey: field.questionKey ?? field.key,
  };
}

interface RowProps {
  field: ReviewField;
  facts: readonly CitedFact[];
  resume?: ResumeChoice;
  onChange?: (answer: QuestionAnswer) => Promise<void>;
  busy?: boolean;
}

function Row({ field, facts, resume, onChange, busy }: RowProps) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<NeedDraft>(() => ({ value: field.value ?? '', save: initialSave(changeNeed(field)) }));
  const labelId = useId();
  const canChange = Boolean(onChange && field.questionKey && !field.locked && !field.attestation);
  const long = field.kind === 'textarea';
  const src = (
    <span className="ap-src">
      {field.locked && (
        <span className="ar-lock">
          <LockIcon />
          Locked
        </span>
      )}
      {sourceText(field, resume)}
    </span>
  );

  if (editing && onChange && field.questionKey) {
    const key = field.questionKey;
    return (
      <div className="ar-row ar-row-full">
        <NeedCard need={changeNeed(field)} draft={draft} onChange={setDraft} disabled={busy} />
        <div className="ap-actions">
          <button
            type="button"
            className="btn"
            aria-disabled={busy || !draft.value.trim() ? 'true' : undefined}
            onClick={() => {
              if (busy || !draft.value.trim()) return;
              void onChange({ questionKey: key, value: draft.value.trim(), save: draft.save }).then(() => setEditing(false));
            }}
          >
            Use this and fill again
          </button>
          <button type="button" className="btn btn-quiet" onClick={() => setEditing(false)}>
            Keep it as it is
          </button>
        </div>
      </div>
    );
  }

  if (long) {
    const cites = citations(field, facts);
    return (
      <div className="ar-row ar-row-full">
        <div className="ar-row-head">
          <span id={labelId} className="ap-q">
            {field.label}
          </span>
          <span className="ap-src">{field.source === 'model' ? citeLine(field) : sourceText(field, resume)}</span>
        </div>
        <p className="ar-long" aria-labelledby={labelId}>
          {field.value || <span className="ap-muted">Left empty</span>}
          {cites.map((c) => (
            <sup key={c.id} className="ar-sup">
              {c.n}
            </sup>
          ))}
        </p>
        {(cites.length > 0 || (field.dropped?.length ?? 0) > 0) && (
          <ol className="ar-cites" aria-label={`What “${field.label}” rests on`}>
            {cites.map((c) => (
              <li key={c.id} className="ar-cite">
                <sup className="ar-sup">{c.n}</sup>
                <span>{c.text}</span>
              </li>
            ))}
            {(field.dropped ?? []).map((line) => (
              <li key={line} className="ar-cite ar-cite-dropped">
                <span aria-hidden="true">–</span>
                <span>Dropped: “{line}” No fact of yours supports it.</span>
              </li>
            ))}
          </ol>
        )}
        {canChange && (
          <div>
            <button type="button" className="btn btn-quiet ap-row-btn" onClick={() => setEditing(true)}>
              Change<span className="sr-only"> {field.label}</span>
            </button>
          </div>
        )}
      </div>
    );
  }

  const choice = CHOICE_KINDS.has(field.kind) && (field.options?.length ?? 0) > 0;
  return (
    <div className={canChange ? 'ar-row ar-row-edit' : 'ar-row'}>
      <span className="ap-q">{field.label}</span>
      <span className="ap-v">{choice ? <Choices field={field} /> : field.value || <span className="ap-muted">Left empty</span>}</span>
      {src}
      {canChange && (
        <button type="button" className="btn btn-quiet ap-row-btn" onClick={() => setEditing(true)}>
          Change<span className="sr-only"> {field.label}</span>
        </button>
      )}
    </div>
  );
}

export interface ReviewGroupProps {
  title: string;
  fields: readonly ReviewField[];
  facts?: readonly CitedFact[];
  resume?: ResumeChoice;
  /** Absent: the rows are read-only (a sent application). */
  onChange?: (answer: QuestionAnswer) => Promise<void>;
  busy?: boolean;
}

export function ReviewGroup({ title, fields, facts = [], resume, onChange, busy }: ReviewGroupProps) {
  const id = useId();
  return (
    <section className="panel ap-panel ar-group" aria-labelledby={id}>
      <div className="ap-ghead">
        <h2 id={id} className="ap-h3">
          {title}
        </h2>
        <span className="numeral ap-muted ap-small" aria-label={plural(fields.length, 'value')}>
          {fields.length}
        </span>
      </div>
      {fields.map((f) => (
        <Row key={f.key} field={f} facts={facts} resume={resume} onChange={onChange} busy={busy} />
      ))}
    </section>
  );
}
