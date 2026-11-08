/**
 * The consent step before the first application (spec 5, A1.7). It says what is sent to the employer, who processes the
 * form (a Browser Use cloud browser, Anthropic's models), and how long screenshots are kept; "I agree, continue" records
 * `consentAt` on the server.
 *
 * Draft copy: the wording is reviewed with the privacy page and terms before launch (spec 5). The public site and the
 * landing copy are not touched until the feature is live.
 */
import { useId, useState } from 'react';
import { errorText } from './api';

/** How long the filled form's screenshots are kept (spec 5; the server's `screenshot_days`). */
export const SCREENSHOT_DAYS = 30;

export interface ConsentProps {
  onAgree: () => Promise<void>;
  /** "Not now": back to the applications. */
  onCancel: () => void;
}

export function Consent({ onAgree, onCancel }: ConsentProps) {
  const [sending, setSending] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const titleId = useId();
  const agree = async () => {
    setSending(true);
    setErr(null);
    try {
      await onAgree();
    } catch (e) {
      setErr(errorText(e, 'Could not save that. Try again.'));
      setSending(false);
    }
  };
  return (
    <section className="ap-stack ap-narrow" aria-labelledby={titleId}>
      <div className="ap-head">
        <p className="kicker">Apply for me · before your first application</p>
        <h1 id={titleId} className="serif ap-title">
          What happens to your details when we apply for you.
        </h1>
      </div>

      <section className="panel ap-panel ap-stack-s" aria-label="What is sent to the employer">
        <h2 className="ap-h3">What the employer gets</h2>
        <p>
          Exactly the values you approve on the review: your name, your own email and phone, the answers the form asks for, and
          the resume you chose. Nothing reaches the employer before you approve that set, and the resume is attached only then.
        </p>
      </section>

      <section className="panel ap-panel ap-stack-s" aria-label="Who processes the form">
        <h2 className="ap-h3">Who works on the form</h2>
        <p>
          The employer's page is opened in a cloud browser run by Browser Use, behind our guard, which stops anything from being
          sent until you approve. Anthropic's Claude models read the form's questions, and the facts needed to answer a custom question, to fill
          it. Answers to custom questions cite your facts, and you see every one.
        </p>
      </section>

      <section className="panel ap-panel ap-stack-s" aria-label="What is kept">
        <h2 className="ap-h3">What we keep</h2>
        <p>
          A screenshot of the filled form, and of the employer's confirmation once it is sent, kept for{' '}
          <span className="numeral">{SCREENSHOT_DAYS}</span> days and then deleted. The record that you applied, and when, stays
          with your applications. ID numbers, signatures and declarations are never stored.
        </p>
      </section>

      {err && (
        <p className="ap-error" role="alert">
          {err}
        </p>
      )}
      <div className="ap-actions">
        <button type="button" className="btn btn-primary" disabled={sending} onClick={() => void agree()}>
          {sending ? 'Saving' : 'I agree, continue'}
        </button>
        <button type="button" className="btn btn-quiet" disabled={sending} onClick={onCancel}>
          Not now
        </button>
      </div>
    </section>
  );
}
