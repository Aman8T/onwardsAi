/**
 * The application inbox (shared/inbox.ts): one tracking address per person that auto-apply puts on the forms it fills.
 * Employers' mail to it is read on the server for verification codes and replies; a reply moves its application's stage
 * through the same classifier as "Paste an email", and the person gets a copy in their own inbox. Pasting stays below
 * this panel as the fallback (mail that went to the person's own address).
 *
 * Three states, from `GET /api/inbox/status`:
 *   - the feature is off (or the status could not be read): nothing is rendered
 *   - no address yet: what it does, the consent checkbox (its exact text is `INBOX_CONSENT_TEXT`) and "Get my address"
 *   - an address: the address with a copy button, an on/off switch, a "send me a copy" switch, and the recent emails
 *     (sender domain, subject, the stage chip, a link to the application when one matched). A verification code is
 *     never shown: the server never sends it, and its row reads only "Verification code received".
 *
 * `InboxPanel` is presentational (the gallery and the tests render it from fixtures); `useInbox` is its container,
 * used by `ApplicationsScreen`.
 */
import { useCallback, useEffect, useId, useRef, useState } from 'react';
import type { ApplicationStage } from '../../shared/apply';
import type { EmailStage, InboxAddress, InboxEmail, InboxStatus, UpdateInboxAddressRequest } from '../../shared/inbox';
import { copyText } from '../discover/share/clipboard';
import { timeAgo } from '../lib/ui';
import { ApplyError, STAGE_LABEL, errorText, inboxApi, stageTone } from './api';
import type { StageTone } from './api';
import { linkTo } from './route';
import type { Navigate } from './route';

/* ───────────────────────── what code computes ───────────────────────── */

/** The consent the person ticks before an address is made (version `INBOX_CONSENT_VERSION` on the server). Exact text. */
export const INBOX_CONSENT_TEXT =
  "Leafout will receive the emails employers send to this address, read them to update your applications, and send you a copy. We keep the sender, the subject and the stage it set. We don't keep the email itself.";

/** The words of a verification email's row. The code itself never reaches the web app. */
export const VERIFICATION_TEXT = 'Verification code received';

/** The panel shows only while the feature is on. */
export function inboxShown(status: InboxStatus | null | undefined): status is InboxStatus {
  return status?.enabled === true;
}

/** An active address: auto-apply puts it on forms, so the paste panel below reads as the fallback. */
export function inboxActive(status: InboxStatus | null | undefined): boolean {
  return inboxShown(status) && status.address?.active === true;
}

/** The application stage an email's stage moves to ("applied" is the email's word for a sent application). */
const STAGE_OF: Record<Exclude<EmailStage, 'none'>, ApplicationStage> = {
  applied: 'sent',
  viewed: 'viewed',
  interviewing: 'interviewing',
  offer: 'offer',
  rejected: 'rejected',
};

export function applicationStageOf(stage: EmailStage | undefined): ApplicationStage | undefined {
  return stage && stage !== 'none' ? STAGE_OF[stage] : undefined;
}

/** The chip of one received email: the stage it set when it set one, else what happened to it. */
export function emailChip(e: InboxEmail): { text: string; tone: StageTone } {
  const moved = applicationStageOf(e.stage);
  switch (e.status) {
    case 'verification':
      return { text: VERIFICATION_TEXT, tone: 'plain' };
    case 'pending':
      return { text: 'Reading it', tone: 'plain' };
    case 'processed':
      return moved ? { text: STAGE_LABEL[moved], tone: stageTone(moved) } : { text: 'No change', tone: 'plain' };
    case 'unmatched':
      return { text: 'No application matched', tone: 'plain' };
    case 'ignored':
      return { text: 'Received while off', tone: 'plain' };
    case 'capped':
      return { text: 'Not read', tone: 'plain' };
    case 'failed':
      return { text: 'Could not read it', tone: 'bad' };
    case 'quarantined':
      return { text: 'Not delivered', tone: 'plain' };
  }
}

/** The line under the subject: the sentence the stage rests on, or why nothing moved. Never a code. */
export function emailNote(e: InboxEmail): string | undefined {
  switch (e.status) {
    case 'verification':
      return 'Auto-apply uses it on the form. We never show it.';
    case 'processed':
      return e.quote ? `“${e.quote}”` : undefined;
    case 'unmatched': {
      const moved = applicationStageOf(e.stage);
      const reads = moved ? `It reads as ${STAGE_LABEL[moved]}, but we` : 'We';
      return `${reads} could not tell which application it is about. Paste it below and pick the application.`;
    }
    case 'ignored':
      return 'Your address was off, so we did not read it.';
    case 'capped':
      return 'This address got too many emails today, so we did not read this one.';
    case 'failed':
      return 'Paste it below if it matters.';
    default:
      return undefined;
  }
}

/** The copy to the person's own inbox, when there is something to say about it. */
export function copyNote(e: InboxEmail): string | undefined {
  if (e.copyStatus === 'sent') return 'Copy sent';
  if (e.copyStatus === 'failed') return 'Your copy did not send';
  return undefined;
}

/** Where the emails list stands. `failed`: the last read did not answer, which is not the same as no mail. */
export function emailsLine(emails: readonly InboxEmail[] | null, failed = false): string | undefined {
  if (failed && (emails === null || emails.length === 0)) return 'Could not load your emails. They show up again on the next check.';
  if (emails === null) return 'Loading your emails';
  if (emails.length === 0) return 'Nothing yet. Employers’ emails show up here as they arrive.';
  return undefined;
}

/** Statuses whose subject was stored before the email was read (or never read): the server hides codes in them, and so
 * does the page, in case an older row kept one. */
const UNREAD: ReadonlySet<InboxEmail['status']> = new Set(['pending', 'ignored', 'capped', 'failed', 'quarantined']);
const CODE_TALK = /code|verif|otp|passcode|one[\s-]?time|security/i;
const CODE_SHAPED = /(?<![A-Za-z0-9])(?=[A-Za-z]*\d)[A-Za-z0-9]{4,8}(?![A-Za-z0-9])/g;

/** The subject as the page shows it: never a code. On a row that was not read, a subject that talks about codes has
 * every code-shaped token (4 to 8 letters and digits with a digit) hidden. */
export function shownSubject(e: InboxEmail): string {
  const subject = e.subject || '(no subject)';
  if (!UNREAD.has(e.status) || !CODE_TALK.test(subject)) return subject;
  return subject.replace(CODE_SHAPED, '[code]');
}

/** Did a read of the list bring a newly matched email (one that is now processed with an application and was not
 * before, including one first seen as pending)? Then that application's stage moved on the server. The first read
 * (`before` null) never counts. */
export function applicationsMoved(before: ReadonlyMap<string, InboxEmail['status']> | null, list: readonly InboxEmail[]): boolean {
  if (!before) return false;
  return list.some((e) => e.status === 'processed' && !!e.applicationId && before.get(e.id) !== 'processed');
}

/* ───────────────────────── the view ───────────────────────── */

export interface InboxPanelProps {
  /** Null while loading, or when the status could not be read: nothing is shown, and pasting still works. */
  status: InboxStatus | null;
  /** Newest first; null while loading. */
  emails: readonly InboxEmail[] | null;
  /** The last read of the list failed (a network error or a server error). */
  emailsFailed?: boolean;
  /** The time "received" is measured against (fixed in the gallery). */
  now: number;
  navigate: Navigate;
  /** "Get my address", after the person ticked the consent. */
  onCreate: () => Promise<void>;
  onUpdate: (body: UpdateInboxAddressRequest) => Promise<void>;
  /** Puts the address on the clipboard; true when it got there. */
  onCopy?: (text: string) => Promise<boolean>;
}

export function InboxPanel(props: InboxPanelProps) {
  const titleId = useId();
  const { status } = props;
  if (!inboxShown(status)) return null;
  return (
    <section className="panel ap-panel ap-stack-s" aria-labelledby={titleId}>
      {status.address ? <HasAddress {...props} address={status.address} titleId={titleId} /> : <NoAddress onCreate={props.onCreate} titleId={titleId} />}
    </section>
  );
}

function NoAddress({ onCreate, titleId }: { onCreate: () => Promise<void>; titleId: string }) {
  const [agreed, setAgreed] = useState(false);
  const [sending, setSending] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const create = async () => {
    if (!agreed || sending) return;
    setSending(true);
    setErr(null);
    try {
      await onCreate();
    } catch (e) {
      setErr(errorText(e, 'Could not make your address. Try again.'));
      setSending(false);
    }
  };
  return (
    <>
      <div className="ap-measure ap-stack-s">
        <h2 id={titleId} className="ap-h3">
          Application inbox
        </h2>
        <p className="ap-muted">
          Get one email address for your applications. Auto-apply puts it on the forms it fills, so employers' replies and
          verification codes come to us: we move each application forward for you and send you a copy.
        </p>
      </div>
      <label className="ap-opt ap-measure">
        <input type="checkbox" checked={agreed} disabled={sending} onChange={(e) => setAgreed(e.target.checked)} />
        <span>{INBOX_CONSENT_TEXT}</span>
      </label>
      {err && (
        <p className="ap-error" role="alert">
          {err}
        </p>
      )}
      <div className="ap-actions">
        <button type="button" className="btn btn-primary" disabled={!agreed || sending} onClick={() => void create()}>
          {sending ? 'Making your address' : 'Get my address'}
        </button>
      </div>
    </>
  );
}

function HasAddress({ address, titleId, emails, emailsFailed = false, now, navigate, onUpdate, onCopy = copyText }: InboxPanelProps & { address: InboxAddress; titleId: string }) {
  const [saving, setSaving] = useState(false);
  const [said, setSaid] = useState('');
  const [err, setErr] = useState<string | null>(null);
  const listTitle = useId();

  const change = async (body: UpdateInboxAddressRequest) => {
    setSaving(true);
    setErr(null);
    setSaid('Saving');
    try {
      await onUpdate(body);
      setSaid('Saved');
    } catch (e) {
      setSaid('');
      setErr(errorText(e, 'Could not save that. Try again.'));
    } finally {
      setSaving(false);
    }
  };
  const copy = async () => {
    setSaid((await onCopy(address.email)) ? 'Address copied' : 'Could not copy. Select the address and copy it yourself.');
  };
  const listLine = emailsLine(emails, emailsFailed);

  return (
    <>
      <div className="ap-measure ap-stack-s">
        <h2 id={titleId} className="ap-h3">
          Application inbox
        </h2>
        <p className="ap-muted">
          {address.active
            ? 'Auto-apply puts this address on the forms it fills. Employers’ replies to it move your applications forward here.'
            : 'Off: new forms get your own email, and mail to this address is noted but not read.'}
        </p>
      </div>

      <div className="ap-well-box">
        <span className="ap-label">Your address</span>
        <div className="ap-actions">
          <span className="ap-strong ap-v">{address.email}</span>
          <button type="button" className="btn btn-sm ap-btn-sm" aria-label={`Copy ${address.email}`} onClick={() => void copy()}>
            Copy
          </button>
        </div>
      </div>

      <div className="ap-actions">
        <label className="ap-check">
          <input type="checkbox" role="switch" checked={address.active} disabled={saving} onChange={(e) => void change({ active: e.target.checked })} />
          <span>Receiving mail</span>
        </label>
        <label className="ap-check">
          <input type="checkbox" role="switch" checked={address.copies} disabled={saving} onChange={(e) => void change({ copies: e.target.checked })} />
          <span>Send me a copy</span>
        </label>
        <span className="ap-muted ap-small" role="status" aria-live="polite">
          {said}
        </span>
      </div>
      {err && (
        <p className="ap-error" role="alert">
          {err}
        </p>
      )}

      <h3 id={listTitle} className="kicker">
        Recent emails
      </h3>
      {listLine ? (
        <p className="ap-muted" role="status">
          {listLine}
        </p>
      ) : (
        <ul className="ap-checks ap-rows" aria-labelledby={listTitle}>
          {(emails ?? []).map((e) => (
            <EmailRow key={e.id} email={e} now={now} navigate={navigate} />
          ))}
        </ul>
      )}
    </>
  );
}

function EmailRow({ email: e, now, navigate }: { email: InboxEmail; now: number; navigate: Navigate }) {
  const chip = emailChip(e);
  const note = emailNote(e);
  const copied = copyNote(e);
  const at = Date.parse(e.receivedAt);
  const subject = shownSubject(e);
  return (
    <li className="ap-drow">
      <span className="ap-q" title={e.sender}>
        {e.senderDomain}
      </span>
      <span className="ap-v">
        {subject}
        {note && <span className="ap-block ap-muted ap-small">{note}</span>}
      </span>
      <span className="ap-src">
        <span className={`chip ap-chip ap-tone-${chip.tone}`}>{chip.text}</span>
        {Number.isFinite(at) && (
          <time className="numeral" dateTime={e.receivedAt}>
            {timeAgo(at, now)}
          </time>
        )}
        {copied && <span>{copied}</span>}
      </span>
      {e.applicationId ? (
        <a className="btn btn-sm ap-btn-sm" {...linkTo({ screen: 'one', id: e.applicationId }, navigate)} aria-label={`Open the application: ${subject}`}>
          Open
        </a>
      ) : (
        <span />
      )}
    </li>
  );
}

/* ───────────────────────── the container ───────────────────────── */

/** How often the list is read again while the address is on and the page is in view. */
const POLL_MS = 60_000;

export interface InboxState {
  status: InboxStatus | null;
  emails: InboxEmail[] | null;
  emailsFailed: boolean;
  onCreate: () => Promise<void>;
  onUpdate: (body: UpdateInboxAddressRequest) => Promise<void>;
}

/** A 404 from an inbox route: the feature was turned off on the server. */
const isOff = (err: unknown) => err instanceof ApplyError && err.status === 404;

/**
 * Reads the status, then the emails while there is an address (again every minute while it is on and the page is in
 * view). `onApplicationsChanged` runs when an email newly turned processed with an application (`applicationsMoved`):
 * its stage moved on the server. A read that fails keeps the list it had and says so (`emailsFailed`).
 */
export function useInbox(onApplicationsChanged?: () => void): InboxState {
  const [status, setStatus] = useState<InboxStatus | null>(null);
  const [emails, setEmails] = useState<InboxEmail[] | null>(null);
  const [emailsFailed, setEmailsFailed] = useState(false);
  const seen = useRef<Map<string, InboxEmail['status']> | null>(null);
  const changed = useRef(onApplicationsChanged);
  useEffect(() => {
    changed.current = onApplicationsChanged;
  }, [onApplicationsChanged]);

  const loadEmails = useCallback(async () => {
    try {
      const list = (await inboxApi.emails()).emails;
      const before = seen.current;
      seen.current = new Map(list.map((e) => [e.id, e.status]));
      if (applicationsMoved(before, list)) changed.current?.();
      setEmails(list);
      setEmailsFailed(false);
    } catch (err) {
      if (isOff(err)) setStatus({ enabled: false });
      else setEmailsFailed(true);
    }
  }, []);

  useEffect(() => {
    let live = true;
    inboxApi
      .status()
      .then((s) => {
        if (live) setStatus(s);
      })
      .catch(() => {
        if (live) setStatus(null);
      });
    return () => {
      live = false;
    };
  }, []);

  const hasAddress = inboxShown(status) && status.address !== undefined;
  const active = inboxActive(status);
  useEffect(() => {
    if (hasAddress) void loadEmails();
  }, [hasAddress, loadEmails]);
  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(() => {
      if (document.visibilityState === 'visible') void loadEmails();
    }, POLL_MS);
    return () => window.clearInterval(timer);
  }, [active, loadEmails]);

  const onCreate = useCallback(async () => {
    try {
      const address = await inboxApi.create();
      setStatus({ enabled: true, address });
    } catch (err) {
      if (isOff(err)) setStatus({ enabled: false });
      throw err;
    }
  }, []);

  const onUpdate = useCallback(async (body: UpdateInboxAddressRequest) => {
    try {
      const address = await inboxApi.update(body);
      setStatus({ enabled: true, address });
    } catch (err) {
      if (isOff(err)) setStatus({ enabled: false });
      throw err;
    }
  }, []);

  return { status, emails, emailsFailed, onCreate, onUpdate };
}
