/**
 * Which applicant tracking system a host (or a whole web address) belongs to, and whether auto-apply fills its form.
 * A code mirror of the host rules in `backend/tayyari/apply/intake.py` (`fingerprint_url`) and of the ATS sets in
 * `apply/checks.py`, so a Discover card can say "Apply for me" or why not before anything is asked of the server.
 *
 * It is a hint only: the server's `POST /api/apply/check` and the start run the real checks (liveness, the match's
 * gates, an earlier application), and nothing here is ever fetched. Pure functions, no DOM.
 */
import { APPLY_URL_MAX_CHARS } from '../../shared/apply';
import type { AtsKind } from '../../shared/apply';

/** The ATSs whose forms auto-apply fills (`checks.FILLABLE_ATS`). */
export const FILLABLE_ATS: ReadonlySet<AtsKind> = new Set<AtsKind>(['greenhouse', 'lever', 'ashby']);

/** ATSs that need the person's own account on the employer's site: never filled for them (`checks.OWN_ACCOUNT_ATS`). */
export const OWN_ACCOUNT_ATS: ReadonlySet<AtsKind> = new Set<AtsKind>(['workday']);

export type FillableAts = 'greenhouse' | 'lever' | 'ashby';

const GREENHOUSE_HOSTS = new Set(['boards.greenhouse.io', 'job-boards.greenhouse.io', 'boards.eu.greenhouse.io', 'job-boards.eu.greenhouse.io']);
const LEVER_HOSTS = new Set(['jobs.lever.co', 'jobs.eu.lever.co']);
const ASHBY_HOST = 'jobs.ashbyhq.com';

/**
 * The lower-cased host of a host or an address, without a leading `www.`; '' for something that is neither.
 * `boards.greenhouse.io`, `https://boards.greenhouse.io/acme/jobs/1` and `BOARDS.greenhouse.io:443` all give
 * `boards.greenhouse.io`.
 */
export function hostOf(hostOrUrl: string): string {
  const raw = hostOrUrl.trim();
  if (!raw) return '';
  let host: string;
  try {
    host = new URL(/^[a-z][a-z0-9+.-]*:\/\//i.test(raw) ? raw : `https://${raw}`).hostname;
  } catch {
    return '';
  }
  host = host.toLowerCase().replace(/\.$/, '');
  return host.startsWith('www.') ? host.slice(4) : host;
}

/** The host as the server reads it (`apply/intake.fingerprint_url`): lower case, no "www." taken off. */
function rawHost(hostOrUrl: string): string {
  const raw = hostOrUrl.trim();
  if (!raw) return '';
  try {
    return new URL(/^[a-z][a-z0-9+.-]*:\/\//i.test(raw) ? raw : `https://${raw}`).hostname.toLowerCase().replace(/\.$/, '');
  } catch {
    return '';
  }
}

/** The ATS a host or address belongs to, by its host alone (the intake's first pass, before any page is read). */
export function atsOf(hostOrUrl: string): AtsKind {
  const host = rawHost(hostOrUrl);
  if (!host) return 'unknown';
  if (GREENHOUSE_HOSTS.has(host)) return 'greenhouse';
  if (LEVER_HOSTS.has(host)) return 'lever';
  if (host === ASHBY_HOST) return 'ashby';
  if (host.endsWith('.myworkdayjobs.com') || host.endsWith('.myworkdaysite.com') || host === 'myworkdaysite.com') return 'workday';
  return 'unknown';
}

/** The fillable ATS behind a host or address, or null when auto-apply does not fill that form. */
export function fillableAts(hostOrUrl: string): FillableAts | null {
  const ats = atsOf(hostOrUrl);
  return ats === 'greenhouse' || ats === 'lever' || ats === 'ashby' ? ats : null;
}

/** Does auto-apply fill the form on this host? */
export function isFillable(hostOrUrl: string): boolean {
  return fillableAts(hostOrUrl) !== null;
}

/** Does this host's ATS need the person's own account there (Workday and the like)? */
export function needsOwnAccount(hostOrUrl: string): boolean {
  return OWN_ACCOUNT_ATS.has(atsOf(hostOrUrl));
}

/**
 * A pasted application link, cleaned: an http(s) address with a host and no spaces, without its #fragment; else null.
 * The same shape as the intake's `normalize_url`; the server checks it is the same posting before filling anything.
 */
export function cleanApplyUrl(raw: string): string | null {
  const text = raw.trim();
  if (!text || /\s/.test(text) || text.length > APPLY_URL_MAX_CHARS) return null;
  let url: URL;
  try {
    url = new URL(text);
  } catch {
    return null;
  }
  if ((url.protocol !== 'https:' && url.protocol !== 'http:') || !url.hostname || !url.hostname.includes('.')) return null;
  url.hash = '';
  return url.toString();
}
