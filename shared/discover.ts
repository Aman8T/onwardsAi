/**
 * Discover: the small vocabulary it shares with Search with AI. Discover is the board store behind Search with AI
 * (`shared/jobsearch.ts`): it reads employers' own public job boards and knows the cities. What it puts on the wire is
 * the city list (`GET /api/discover/cities`) and the pieces a found job reuses: a place, a salary the employer states,
 * a work mode, the quick fit and the evidence a reason cites. `shared/jobsearch.ts` imports them from here.
 *
 * Mirrored by backend/tayyari/contract/discover.py; tests/unit/contract/test_discover_parity.py parses this file and
 * fails when the two drift. Keep to what that parser reads: interfaces, literal unions, `x?:` optionals, `T[]`, inline
 * objects. No `Record`, `null`, tuples or `typeof` in types.
 *
 * The rules this contract carries (project-soul.md): no percentages anywhere; the quick fit is a word and counts
 * computed in code from the skill names a posting uses, never a model's score; a salary appears only when the employer
 * states it; distances are straight lines and say so; the city a person looks in is chosen in the browser and is not
 * stored by the server.
 *
 * Employers' own boards (Greenhouse and Lever) are the only source. Sources that need a licence, a partnership or legal
 * review are listed in docs/discover-later.md.
 */

export type DiscoverWorkMode = 'onsite' | 'hybrid' | 'remote' | 'unknown';

/** How precisely a place is known. Distances exist only when both the area and the listing are `locality`. */
export type PlacePrecision = 'locality' | 'city' | 'country';

/**
 * The quick fit, in words. Computed in code from the skill names the posting uses against the person's profile.
 * `looks_close` and `worth_a_look` and `stretch` read "Looks close", "Worth a look", "A stretch"; `cant_tell` reads
 * "Can't tell yet" (the posting names too few skills, or the profile is thin).
 */
export type QuickFitWord = 'looks_close' | 'worth_a_look' | 'stretch' | 'cant_tell';

/** A fact a reason rests on: which source (`resume`, `github`, a link label) and the evidence text. */
export interface Cite {
  source: string;
  text: string;
}

export interface Reason {
  text: string;
  cite?: Cite;
}

export interface QuickFit {
  word: QuickFitWord;
  /** Skills the posting names (from our vocabulary). */
  named: number;
  /** Of those, how many your profile shows. */
  matched: number;
  /** Named by the posting and not found in your profile, shown as "Not found by name: …" (at most five). */
  notFound: string[];
  /** Years the posting asks for, when it says. */
  yearsAsked?: number;
  /** Years your profile shows, when known. */
  yearsYou?: number;
  /** One or two reasons, each citing your evidence. */
  reasons: Reason[];
}

export interface Place {
  /** "Koramangala, Bengaluru" · "Bengaluru" · "Remote, India". */
  label: string;
  city?: string;
  locality?: string;
  precision: PlacePrecision;
  /** Straight-line kilometres from your area, rounded; only when both ends are known to a locality. */
  km?: number;
}

export interface Salary {
  /** As the employer states it, e.g. "₹28–36 lakh a year". */
  text: string;
}

export interface DiscoverCity {
  name: string;
  localities: string[];
}

/** `GET /api/discover/cities`. */
export interface DiscoverCitiesResponse {
  cities: DiscoverCity[];
}
