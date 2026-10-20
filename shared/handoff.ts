/**
 * The hand-off from the marketing site (https://leafout.in) to the app: the contract between `/api/handoff` and the web app.
 *
 * The site collects a resume, links and a goal and sends the visitor's browser to the app with a top-level HTML form POST
 * (multipart/form-data, never fetch):
 *
 *   POST /api/handoff         fields: resume (file, optional), links (URLs separated by newlines or spaces, at most
 *                             `HANDOFF_LINKS_MAX`), goal (<= `HANDOFF_GOAL_MAX_CHARS`), notes (<= `HANDOFF_NOTES_MAX_CHARS`);
 *                             a resume or a link is required. Only accepted from the origins the server lists
 *                             (`TAYYARI_HANDOFF__ORIGINS`). Answers 303 See Other, never JSON:
 *                               accepted  -> `/?handoff=<id>`
 *                               refused   -> `/?handoff_error=<code>`, a `HandoffErrorCode`
 *   GET  /api/handoff/{id}    same-origin fetch from the app; returns the `HandoffPayload` ONCE and deletes it (404 when
 *                             missing, taken already or expired after `ttl_seconds`, 15 minutes by default)
 *
 * Mirrored by backend/tayyari/contract/handoff.py; tests/unit/contract/test_handoff_parity.py reads this file. Same subset
 * as the other contracts: interfaces, literal unions, `x?:` optionals (no `null`), `T[]`.
 */

/** At most this many links are kept. */
export const HANDOFF_LINKS_MAX = 10;
/** A link longer than this is dropped. */
export const HANDOFF_LINK_MAX_CHARS = 2000;
export const HANDOFF_GOAL_MAX_CHARS = 300;
export const HANDOFF_NOTES_MAX_CHARS = 4000;

/**
 * Why a hand-off was refused (the `handoff_error` query value). `origin`: it did not come from the site; `too_large`: the
 * resume or the form is over the size limit; `type`: the resume is not a PDF, DOCX, TXT or Markdown file; `empty`: no resume
 * and no usable link; `rate`: too many hand-offs from this address.
 */
export type HandoffErrorCode = 'origin' | 'too_large' | 'type' | 'empty' | 'rate';

/** The resume file as the site sent it. `type` is its media type (application/pdf, text/plain ...). */
export interface HandoffResume {
  name: string;
  type: string;
  /** The file's bytes, standard base64. */
  dataBase64: string;
}

/** What the visitor handed over. Links are checked http(s) addresses, in the order given. */
export interface HandoffPayload {
  links: string[];
  goal?: string;
  notes?: string;
  resume?: HandoffResume;
}
