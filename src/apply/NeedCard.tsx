/**
 * One thing a form asks that we will not answer on our own (canvas 12): a question with no saved answer, a per-offer
 * decision, a signature, or an ID number the person types on the employer's page.
 *
 *   - "Required by <employer>" and the reason, as the server wrote them.
 *   - "Also asked by N more forms waiting": one saved answer fills every waiting form that asks the same question.
 *   - The save box starts ticked, except for a per-offer question (a bond, relocation for this role) and a signature,
 *     which is never kept at all.
 *   - An `onSite` need (PAN, Aadhaar, caste, father's name) has no input: it is the person's to type on the employer's
 *     page, and it says so.
 *
 * The card is presentational. The caller keeps the drafts (`NeedDraft`) and sends them as one batch (`answerBatch`).
 */
import { useId } from 'react';
import type { FieldKind, NeedsInput, QuestionAnswer } from '../../shared/apply';
import { APPLY_ANSWER_MAX_CHARS } from '../../shared/apply';
import { plural } from '../lib/ui';

/**
 * Signatures, declarations and consent ticks: typed or ticked by the person in Review, for this form only, never kept.
 * The server marks such a need (`NeedsInput.attestation`); this mirror of `apply.playbook.ATTESTATION_KEYS` covers a need
 * stored before the flag.
 */
export const ATTESTATION_KEYS: ReadonlySet<string> = new Set(['signature', 'declaration', 'consent']);

/** The answer bank key a need is answered under. Every need carries one now; the field key is the fallback. */
export const keyOf = (need: Pick<NeedsInput, 'key' | 'questionKey'>): string => need.questionKey || need.key;

export function isAttestation(need: Pick<NeedsInput, 'key' | 'questionKey' | 'attestation'>): boolean {
  return need.attestation === true || ATTESTATION_KEYS.has(keyOf(need));
}

/**
 * Self-ID (gender, race, veteran, disability, pronouns): declined by default and kept only on an explicit tick, so the
 * save box starts off. Mirrors `apply.playbook.SELF_ID_KEYS`.
 */
export const SELF_ID_KEYS: ReadonlySet<string> = new Set(['eeoGender', 'eeoRace', 'eeoVeteran', 'eeoDisability', 'eeoOther', 'pronouns']);

export function isSelfId(need: Pick<NeedsInput, 'key' | 'questionKey'>): boolean {
  return SELF_ID_KEYS.has(keyOf(need));
}

/** A file the form needs (the resume, a document): fixed in Your details or on the match, never typed here. */
export function isFileNeed(need: Pick<NeedsInput, 'kind'>): boolean {
  return need.kind === 'file';
}

/** A need the person can answer here (not one only they can type on the employer's page, nor a file). */
export function answerable(need: NeedsInput): boolean {
  return !need.onSite && !isFileNeed(need);
}

/** Needs the person settles somewhere else: typed on the employer's page, or a file added in Your details. */
export function elsewhere(need: NeedsInput): boolean {
  return Boolean(need.onSite) || isFileNeed(need);
}

/** Questions for the Questions screen: everything but a signature (Review) and what is typed on the employer's page. */
export function isQuestion(need: NeedsInput): boolean {
  return answerable(need) && !isAttestation(need);
}

export interface NeedDraft {
  value: string;
  save: boolean;
}

/** The save box's first state: on, so no form asks again; off for a per-offer question and self-ID; never for a signature. */
export function initialSave(need: NeedsInput): boolean {
  return !need.perOffer && !isAttestation(need) && !isSelfId(need);
}

export function initialDrafts(needs: readonly NeedsInput[]): Record<string, NeedDraft> {
  const out: Record<string, NeedDraft> = {};
  for (const need of needs) if (answerable(need)) out[keyOf(need)] = { value: '', save: initialSave(need) };
  return out;
}

/** The batch to send: every answerable need with a value, a signature never saved. */
export function answerBatch(needs: readonly NeedsInput[], drafts: Readonly<Record<string, NeedDraft>>): QuestionAnswer[] {
  const out: QuestionAnswer[] = [];
  const seen = new Set<string>();
  for (const need of needs) {
    const key = keyOf(need);
    const draft = drafts[key];
    if (!answerable(need) || !draft || seen.has(key)) continue;
    const value = draft.value.trim();
    if (!value) continue;
    seen.add(key);
    out.push({ questionKey: key, value, save: isAttestation(need) ? false : draft.save });
  }
  return out;
}

/** The required answerable needs still without a value. */
export function missingRequired(needs: readonly NeedsInput[], drafts: Readonly<Record<string, NeedDraft>>): NeedsInput[] {
  return needs.filter((n) => answerable(n) && n.required && !(drafts[keyOf(n)]?.value.trim() ?? ''));
}

/** "Save it, and use it for Tamarind and Corvid Labs too". */
export function saveLabel(need: NeedsInput): string {
  if (need.perOffer) return 'Remember my answer for every form';
  const others = (need.alsoWaiting ?? []).map((a) => a.company).filter(Boolean);
  if (others.length === 0) return 'Save it, so no form asks again';
  const names = others.length <= 2 ? others.join(' and ') : `${others.slice(0, 2).join(', ')} and ${plural(others.length - 2, 'more form')}`;
  return `Save it, and use it for ${names} too`;
}

/** A checkbox group's value: the picked options, in the form's order, joined with "; " (as `apply.verify` reads it). */
export function joinPicks(picked: readonly string[], options: readonly string[]): string {
  return options.filter((o) => picked.includes(o)).join('; ');
}

export function splitPicks(value: string): string[] {
  return value
    .split(';')
    .map((p) => p.trim())
    .filter(Boolean);
}

const CHOICE_KINDS: ReadonlySet<FieldKind> = new Set(['select', 'radio', 'combobox', 'checkboxGroup']);
const INPUT_TYPE: Partial<Record<FieldKind, string>> = { email: 'email', tel: 'tel', url: 'url', date: 'date' };

export interface NeedCardProps {
  need: NeedsInput;
  draft?: NeedDraft;
  onChange?: (next: NeedDraft) => void;
  disabled?: boolean;
  /** The employer whose page an `onSite` field is typed on, when the need does not name it. */
  company?: string;
}

export function NeedCard({ need, draft, onChange, disabled, company }: NeedCardProps) {
  const base = useId();
  const inputId = `${base}-in`;
  const titleId = `${base}-t`;
  const whyId = `${base}-why`;
  const others = need.alsoWaiting?.length ?? 0;
  const employer = need.requiredBy ?? company;
  const attestation = isAttestation(need);

  if (isFileNeed(need) && !need.onSite) {
    return (
      <section className="panel ar-need ar-need-onsite" aria-labelledby={titleId}>
        <div className="ar-need-head">
          <p id={titleId} className="ar-need-q">
            {need.label}
            {need.required && <span className="sr-only"> (required)</span>}
          </p>
          <span className="chip ap-chip ap-tone-you">A file, not an answer</span>
        </div>
        <p className="ap-small">
          {need.required && employer ? `Required by ${employer}. ` : ''}
          {need.reason}
        </p>
        <p className="ap-muted ap-small">Add it in Your details (or finish the tailored resume on the match), then press Fill it again.</p>
      </section>
    );
  }

  if (need.onSite) {
    return (
      <section className="panel ar-need ar-need-onsite" aria-labelledby={titleId}>
        <div className="ar-need-head">
          <p id={titleId} className="ar-need-q">
            {need.label}
          </p>
          <span className="chip ap-chip ap-tone-you">You type this one</span>
        </div>
        <p className="ap-small">{need.reason}</p>
        <p className="ap-muted ap-small">
          When you open the form, this field is left empty for you to fill on {employer ? `${employer}'s` : "the employer's"} page.
        </p>
      </section>
    );
  }

  const value = draft?.value ?? '';
  const save = draft?.save ?? initialSave(need);
  const set = (patch: Partial<NeedDraft>) => onChange?.({ value, save, ...patch });
  const options = need.options ?? [];
  const choice = CHOICE_KINDS.has(need.kind) && options.length > 0;
  const yesNo = need.kind === 'checkbox' && !attestation;
  const multi = need.kind === 'checkboxGroup' && options.length > 0;
  const pills = yesNo ? ['Yes', 'No'] : choice && !multi && options.length <= 6 ? options : null;
  const picked = multi ? splitPicks(value) : [];

  let input;
  if (multi) {
    // several answers allowed: one checkbox per option, sent joined the way the verifier reads a group ("A; B")
    input = (
      <div className="ap-pills" role="group" aria-labelledby={titleId} aria-describedby={whyId}>
        {options.map((opt) => (
          <label key={opt} className="ap-pill">
            <input
              type="checkbox"
              value={opt}
              checked={picked.includes(opt)}
              disabled={disabled}
              onChange={(e) => set({ value: joinPicks(e.target.checked ? [...picked, opt] : picked.filter((p) => p !== opt), options) })}
            />
            {opt}
          </label>
        ))}
      </div>
    );
  } else if (attestation && need.kind === 'checkbox') {
    input = (
      <label className="ap-check">
        <input type="checkbox" checked={value === 'yes'} disabled={disabled} onChange={(e) => set({ value: e.target.checked ? 'yes' : '' })} aria-describedby={whyId} />
        I agree
      </label>
    );
  } else if (pills) {
    input = (
      <div className="ap-pills" role="radiogroup" aria-labelledby={titleId} aria-describedby={whyId}>
        {pills.map((opt) => (
          <label key={opt} className="ap-pill">
            <input type="radio" name={inputId} value={opt} checked={value === opt} disabled={disabled} onChange={() => set({ value: opt })} />
            {opt}
          </label>
        ))}
      </div>
    );
  } else if (choice) {
    input = (
      <select id={inputId} className="field ar-select" value={value} disabled={disabled} onChange={(e) => set({ value: e.target.value })} aria-describedby={whyId}>
        <option value="">Choose one</option>
        {options.map((opt) => (
          <option key={opt} value={opt}>
            {opt}
          </option>
        ))}
      </select>
    );
  } else if (need.kind === 'textarea') {
    input = (
      <textarea
        id={inputId}
        className="field ar-textarea"
        value={value}
        maxLength={APPLY_ANSWER_MAX_CHARS}
        disabled={disabled}
        onChange={(e) => set({ value: e.target.value })}
        aria-describedby={whyId}
      />
    );
  } else {
    const numeric = need.kind === 'number' || Boolean(need.unit);
    input = (
      <div className="ar-unit-row">
        <input
          id={inputId}
          className={`field ar-input${numeric ? ' numeral ar-input-short' : ''}`}
          type={INPUT_TYPE[need.kind] ?? 'text'}
          inputMode={numeric ? 'decimal' : undefined}
          value={value}
          maxLength={APPLY_ANSWER_MAX_CHARS}
          disabled={disabled}
          placeholder={attestation ? 'Your full name' : undefined}
          autoComplete="off"
          onChange={(e) => set({ value: e.target.value })}
          aria-describedby={whyId}
        />
        {need.unit && <span className="numeral ap-muted">{need.unit}</span>}
      </div>
    );
  }

  const labelled = !pills && !multi && !(attestation && need.kind === 'checkbox');
  return (
    <section className="panel ar-need" aria-labelledby={titleId}>
      <div className="ar-need-head">
        {labelled ? (
          <label id={titleId} htmlFor={inputId} className="ar-need-q">
            {need.label}
            {need.required && <span className="sr-only"> (required)</span>}
          </label>
        ) : (
          <p id={titleId} className="ar-need-q">
            {need.label}
            {need.required && <span className="sr-only"> (required)</span>}
          </p>
        )}
        {others > 0 && <span className="chip ap-chip">Also asked by {plural(others, 'more form')} waiting</span>}
      </div>
      <p id={whyId} className="ap-src ar-need-why">
        {need.required && employer ? `Required by ${employer}. ` : ''}
        {need.reason}
      </p>
      {need.suggestion && !value && (
        <p className="ap-small">
          From your facts: <span className="ap-strong">{need.suggestion}</span>{' '}
          <button type="button" className="btn btn-quiet ap-row-btn" disabled={disabled} onClick={() => set({ value: need.suggestion ?? '' })}>
            Use it
          </button>
        </p>
      )}
      {input}
      {attestation ? (
        <p className="ap-muted ap-small">For this form only. We never keep it.</p>
      ) : (
        <label className="ap-check">
          <input type="checkbox" checked={save} disabled={disabled} onChange={(e) => set({ save: e.target.checked })} />
          {saveLabel(need)}
        </label>
      )}
    </section>
  );
}
