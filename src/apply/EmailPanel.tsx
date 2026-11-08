/**
 * "Paste an email": an employer's reply, read once to move its application forward (spec 3.2, the MVP way in). The body is
 * classified and dropped on the server: never stored or logged. The person can say which application it is about; when
 * they don't, the server matches by employer, role and sent date and asks when more than one could match.
 *
 * Presentational: the caller passes `onSubmit` (the gallery passes a canned answer).
 */
import { useId, useState } from 'react';
import type { FormEvent } from 'react';
import type { ApplicationSummary, EmailClassification, EmailClassifyRequest } from '../../shared/apply';
import { APPLY_EMAIL_MAX_CHARS } from '../../shared/apply';
import { STAGE_LABEL, errorText } from './api';

interface EmailPanelProps {
  /** The sent applications it could be about (the picker is left out when there are none). */
  applications: readonly ApplicationSummary[];
  onSubmit: (body: EmailClassifyRequest) => Promise<EmailClassification>;
  /** After a classification moved a stage: the list should be read again. */
  onChanged?: () => void;
  /** The id of the heading that names this form (the sheet's title on a phone). */
  titleId?: string;
}

/** The sentence shown after the email was read. Code picks the words from the classification. */
export function resultText(got: EmailClassification): string {
  // "applied" is the email's word for "your application arrived": the row is then sent
  const label = got.stage === 'applied' ? STAGE_LABEL.sent : got.stage === 'none' ? '' : STAGE_LABEL[got.stage];
  if (got.applied && got.stage !== 'none') return `Moved to ${label}${got.quote ? `: “${got.quote}”` : '.'}`;
  if (got.note) return got.note;
  if (got.stage === 'none') return 'Nothing in it changes a stage.';
  return 'It reads like a reply, but no application matched it. Pick the application above and try again.';
}

export function EmailPanel({ applications, onSubmit, onChanged, titleId }: EmailPanelProps) {
  const ownTitle = useId();
  const [sender, setSender] = useState('');
  const [subject, setSubject] = useState('');
  const [body, setBody] = useState('');
  const [applicationId, setApplicationId] = useState('');
  const [sending, setSending] = useState(false);
  const [result, setResult] = useState<string | null>(null);
  const base = useId();
  const ids = { sender: `${base}-from`, subject: `${base}-subject`, body: `${base}-body`, app: `${base}-app` };

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!body.trim() || sending) return;
    setSending(true);
    setResult(null);
    try {
      const request: EmailClassifyRequest = { subject, sender, body };
      if (applicationId) request.applicationId = applicationId;
      const got = await onSubmit(request);
      setResult(resultText(got));
      setBody('');
      if (got.applied) onChanged?.();
    } catch (err) {
      setResult(errorText(err, 'Could not read that email. Try again.'));
    } finally {
      setSending(false);
    }
  };

  return (
    <form className="ap-email" onSubmit={submit} aria-labelledby={titleId ?? ownTitle}>
      {!titleId && (
        <h3 id={ownTitle} className="ap-h3">
          Paste an email
        </h3>
      )}
      <div className="ap-email-row">
        <label className="ap-label" htmlFor={ids.sender}>
          From
        </label>
        <input id={ids.sender} className="field" value={sender} onChange={(e) => setSender(e.target.value)} placeholder="no-reply@greenhouse.io" autoComplete="off" />
      </div>
      <div className="ap-email-row">
        <label className="ap-label" htmlFor={ids.subject}>
          Subject
        </label>
        <input id={ids.subject} className="field" value={subject} onChange={(e) => setSubject(e.target.value)} autoComplete="off" />
      </div>
      {applications.length > 0 && (
        <div className="ap-email-row">
          <label className="ap-label" htmlFor={ids.app}>
            Which application (optional)
          </label>
          <select id={ids.app} className="field" value={applicationId} onChange={(e) => setApplicationId(e.target.value)}>
            <option value="">Let Leafout match it</option>
            {applications.map((a) => (
              <option key={a.id} value={a.id}>
                {[a.company, a.title].filter(Boolean).join(' · ') || a.domain}
              </option>
            ))}
          </select>
        </div>
      )}
      <div className="ap-email-row">
        <label className="ap-label" htmlFor={ids.body}>
          The email's text (read once, never stored)
        </label>
        <textarea
          id={ids.body}
          className="field ap-textarea"
          rows={5}
          maxLength={APPLY_EMAIL_MAX_CHARS}
          value={body}
          onChange={(e) => setBody(e.target.value)}
          required
        />
      </div>
      <div className="ap-actions">
        <button type="submit" className="btn" disabled={sending || !body.trim()}>
          {sending ? 'Reading it' : 'Read it'}
        </button>
        <p className="ap-muted" role="status" aria-live="polite">
          {result}
        </p>
      </div>
    </form>
  );
}
