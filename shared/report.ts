/**
 * "Where can I go next", the shareable report (N1, beta; canvas https://claude.ai/artifact/WLF5fKswLqtgrWzRRW9Sq5). The
 * contract between `/api/report` and the web app's owner sheet.
 *
 *   GET  /api/report?profileId=      ReportView (with `blocked` for the sample or an anonymous profile)
 *   PUT  /api/report                 ReportUpdate -> ReportView (turning sharing on makes a slug; off clears it)
 *   POST /api/report/new-link        ReportNewLink -> ReportView (a new slug; the old one stops at once)
 *
 * Public, no account: `/next/{slug}` (a server-rendered page, no scripts, noindex) and `/og/next/{slug}-{version}.png`.
 *
 * On it: up to three roles the person can credibly move to, each with the resume lines behind it (quoted verbatim, then
 * redacted by the owner's choices) and the asks not shown yet; their skill shape; the near misses when the owner makes them
 * public. All worked out in code from the newest owned graph. Never a percentage, rank, percentile or match score, and
 * nothing that says why the person is looking.
 *
 * Mirrored by backend/tayyari/contract/report.py; tests/unit/contract/test_report_parity.py reads this file. Same subset as
 * the other contracts: interfaces, literal unions, `x?:` optionals, `T[]`.
 */

export const REPORT_ROLES_MAX = 3;
export const REPORT_QUOTES_PER_ROLE = 2;
export const REPORT_MISSING_MAX = 3;
export const REPORT_NEAR_MISSES_MAX = 2;
export const REPORT_AREAS_MAX = 8;
/** A quoted resume line is clipped at a word boundary past this many characters. */
export const REPORT_QUOTE_MAX = 240;
export const REPORT_HIDDEN_MAX = 12;
export const SLUG_LEN = 12;

/** Why sharing is not offered: the shared sample, or a graph no account owns (it could never be turned off). */
export type ReportBlocked = 'sample' | 'anonymous';

/** One resume line behind a role, verbatim after redaction. `source` is the source's label ("Resume", "GitHub"). */
export interface ReportQuote {
  /** Stable while the line stays in the graph, so hiding it survives a refresh. */
  id: string;
  text: string;
  source: string;
  /** Hidden by the owner: present in `offered` (so it can be shown again), never in `public`. */
  hidden: boolean;
}

/** A role the person can credibly move to: at least half its asks shown, and at least one cited line. */
export interface ReportRole {
  id: string;
  title: string;
  /** Asks shown with evidence, out of `total`: drawn as dots, never a percentage. */
  have: number;
  total: number;
  /** "Ready now": at most one ask not shown yet. */
  ready: boolean;
  /** `Role.eta` as the graph wrote it. */
  eta: string;
  quotes: ReportQuote[];
  /** Names of the asks not shown yet, at most REPORT_MISSING_MAX. */
  missing: string[];
}

/** A role that fell short of the bar, with what is missing. */
export interface ReportNearMiss {
  id: string;
  title: string;
  missing: string[];
}

export interface ReportArea {
  name: string;
  /** The rim label, at most 14 characters. */
  short: string;
  score: number;
}

export interface ReportPerson {
  /** "Ravi K.": present only while the owner shows their name. */
  name?: string;
  /** Current role · years · place; no employer, ever. */
  line: string;
}

/** What a report draws. `public` is exactly what the shared page shows. */
export interface ReportContent {
  person: ReportPerson;
  areas: ReportArea[];
  roles: ReportRole[];
  nearMisses: ReportNearMiss[];
  openToWork: boolean;
}

export interface ReportChoices {
  showName: boolean;
  /** Off (the default): every employer name the person's own resume blocks hold is replaced in the quotes. */
  showOrgs: boolean;
  showQuotes: boolean;
  openToWork: boolean;
  showNearMisses: boolean;
  /** Quote ids the owner hid, at most REPORT_HIDDEN_MAX. */
  hiddenQuotes: string[];
}

/** The owner's view. The link fields are present only while sharing is on. */
export interface ReportView {
  choices: ReportChoices;
  /** Everything the owner can choose from: every role, quote (hidden ones flagged) and near miss, redacted by the choices. */
  offered: ReportContent;
  /** Exactly what a visitor sees. */
  public: ReportContent;
  /** How many employer names were found to hide. 0 while `showOrgs` is off: none could be hidden, so check each quote. */
  orgsFound: number;
  blocked?: ReportBlocked;
  /** The paid step's link, when the app is configured with one (`TAYYARI_REPORT__OFFER_URL`). */
  offerUrl?: string;
  slug?: string;
  pageUrl?: string;
  ogUrl?: string;
  updatedAt?: string;
}

export interface ReportUpdate {
  profileId: string;
  shared: boolean;
  choices: ReportChoices;
}

export interface ReportNewLink {
  profileId: string;
}
