/**
 * Application inbox: one tracking address per person (e.g. `jordan.k7m4q@apply.leafout.in`) that employers and ATSs
 * write to. Mail to it is read for verification codes, classified with the auto-apply email classifier (which moves the
 * application's stage) and copied to the person's real inbox. The contract between `/api/inbox/*` and the web app.
 *
 *   GET    /api/inbox/status     `InboxStatus` (works with the feature off: `enabled: false`, no address)
 *   POST   /api/inbox/address    `CreateInboxAddressRequest` -> `InboxAddress` (explicit opt-in, `consent: true`;
 *                                idempotent: a person who already has an address gets that one back, 200)
 *   PATCH  /api/inbox/address    `UpdateInboxAddressRequest` -> `InboxAddress` (turn it off or on, copies off or on)
 *   GET    /api/inbox/emails     `InboxEmailList`, newest first, `?limit=` (default `INBOX_EMAILS_DEFAULT_LIMIT`, at most
 *                                `INBOX_EMAILS_MAX_LIMIT`)
 *
 * Every route but `status` answers 404 while the inbox is off (`INBOUND_EMAIL_ENABLED` unset or a Resend secret missing),
 * and 401 without a sign-in. The provider's webhook (`POST /api/inbound/resend`) is not part of this contract.
 *
 * We keep the sender, the subject (cut to `INBOX_SUBJECT_MAX_CHARS`) and the stage an email set; never the body or the
 * attachments. A verification code is never sent to the web app: such an email is listed with status `verification`.
 *
 * Mirrored by backend/tayyari/contract/inbox.py; tests/unit/contract/test_inbox_parity.py parses this file (together with
 * shared/apply.ts, which owns `EmailStage`) and fails when the two drift. Same subset as the other contracts: interfaces,
 * literal unions, `x?:` optionals (no `null`: an absent address is a missing key), `T[]`.
 */
import type { EmailStage } from './apply';

export type { EmailStage };

/** The version of the consent text the person agreed to when they asked for an address. */
export const INBOX_CONSENT_VERSION = 'inbox-v1';

/** Longest subject we keep (and list). */
export const INBOX_SUBJECT_MAX_CHARS = 200;
/** Emails listed when `?limit=` is absent. */
export const INBOX_EMAILS_DEFAULT_LIMIT = 50;
/** Most emails one list returns. */
export const INBOX_EMAILS_MAX_LIMIT = 200;

/**
 * What happened to one received email. `pending`: received, not read yet. `processed`: classified and matched to an
 * application. `unmatched`: classified, but no application fits. `verification`: an ATS verification code or link (kept
 * for auto-apply for 15 minutes, never shown). `quarantined`: sent to an address nobody has. `ignored`: the address was
 * off. `capped`: over the daily cap for the address. `failed`: could not be read.
 */
export type InboxEmailStatus =
  | 'pending'
  | 'processed'
  | 'unmatched'
  | 'verification'
  | 'quarantined'
  | 'ignored'
  | 'capped'
  | 'failed';

/** The copy to the person's real inbox: `skipped` when copies are off or the email is not copied (verification codes). */
export type CopyStatus = 'sent' | 'skipped' | 'failed';

/** The person's tracking address. One per person, never reused or given to anyone else. */
export interface InboxAddress {
  /** The full address, e.g. `jordan.k7m4q@apply.leafout.in`. */
  email: string;
  /** Off: mail to it is noted (status `ignored`) but not read. */
  active: boolean;
  /** Send the person a copy of each employer email. */
  copies: boolean;
  /** ISO 8601. */
  createdAt: string;
}

export interface InboxStatus {
  enabled: boolean;
  /** Absent until the person asked for one (and always while the feature is off). */
  address?: InboxAddress;
}

/** "Get my address". `consent` must be true: the request is refused otherwise. */
export interface CreateInboxAddressRequest {
  consent: boolean;
}

/** At least one of the two is required. */
export interface UpdateInboxAddressRequest {
  active?: boolean;
  copies?: boolean;
}

/** One received email, as listed. Never the body, never a verification code. */
export interface InboxEmail {
  id: string;
  /** ISO 8601. */
  receivedAt: string;
  /** The sender's address. */
  sender: string;
  senderDomain: string;
  subject: string;
  status: InboxEmailStatus;
  /** The stage the classifier read (absent until classified, and for verification codes). */
  stage?: EmailStage;
  /** The application the email was matched to. */
  applicationId?: string;
  /** The sentence of the email the classification rests on. */
  quote?: string;
  copyStatus?: CopyStatus;
}

export interface InboxEmailList {
  emails: InboxEmail[];
}
