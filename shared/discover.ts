/**
 * Discover: personal, location-aware job discovery. The contract between the discover service's routes
 * (`/api/discover*`) and the web app.
 *
 * Mirrored by backend/tayyari/contract/discover.py; tests/unit/contract/test_discover_parity.py parses this file and
 * fails when the two drift. Keep to what that parser reads: interfaces, literal unions, `x?:` optionals, `T[]`, inline
 * objects. No `Record`, `null`, tuples or `typeof` in types.
 *
 * The rules this contract carries (project-soul.md): no percentages anywhere; the quick fit is a word and counts
 * computed in code from the skill names a posting uses, never a model's score; a salary appears only when the employer
 * states it; every listing carries an apply link to the employer's own posting; distances are straight lines and say
 * so; the person's area is chosen in the browser and is not stored by the server.
 *
 * Version 1 reads only employers' own public job boards (Greenhouse and Lever). Sources that need a licence,
 * a partnership or legal review are listed in docs/discover-later.md.
 */

/** Where a listing was read from. Both are the employer's own applicant tracking system. */
export type DiscoverSource = 'greenhouse' | 'lever';

export type DiscoverWorkMode = 'onsite' | 'hybrid' | 'remote' | 'unknown';

/** How precisely a place is known. Distances exist only when both the area and the listing are `locality`. */
export type PlacePrecision = 'locality' | 'city' | 'country';

/**
 * The quick fit, in words. Computed in code from the skill names the posting uses against the person's profile.
 * `looks_close` and `worth_a_look` and `stretch` read "Looks close", "Worth a look", "A stretch"; `cant_tell` reads
 * "Can't tell yet" (the posting names too few skills, or the profile is thin).
 */
export type QuickFitWord = 'looks_close' | 'worth_a_look' | 'stretch' | 'cant_tell';

/** `near`: near your area. `remote`: remote roles that say India. `city`: another city you'd move to. */
export type DiscoverLane = 'near' | 'remote' | 'city';

/** How far, as straight-line kilometres from your area, or the whole city. */
export type DiscoverReach = '3' | '5' | '10' | '25' | 'city';

/** How wide: roles like yours, plus your next steps, plus stretch roles. */
export type DiscoverBreadth = 'role' | 'next' | 'stretch';

/** `ranked`: our order (see `signals`). `posted`: newest first, no ranking. */
export type DiscoverOrder = 'ranked' | 'posted';

/** Why a group exists, in the order a listing is placed: the first kind that fits is its home. */
export type GroupKind = 'next_role' | 'current' | 'skill_bridge' | 'first_steps' | 'other';

/** Version 1 reads only employers' own boards. */
export type SourceConfidence = 'employer';

/** Where the city came from: the resume, the person, or nowhere yet. */
export type CityOrigin = 'resume' | 'you' | 'none';

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

export interface ListingCard {
  id: string;
  title: string;
  employer: string;
  employerId: string;
  source: DiscoverSource;
  place: Place;
  workMode: DiscoverWorkMode;
  /** ISO time the employer gives, when it gives one. */
  postedAt?: string;
  /** ISO time we first saw it: freshness counts from here. */
  firstSeenAt: string;
  /** ISO time the employer's board last listed it. */
  checkedAt: string;
  /** "Posted 3 days ago" · "Open 4 weeks" · "Worth checking it's still hiring". */
  freshness: string;
  applyUrl: string;
  /** The host the apply link goes to, e.g. "jobs.lever.co". */
  applyHost: string;
  confidence: SourceConfidence;
  /** "Employer's own listing, checked today". */
  sourceLabel: string;
  salary?: Salary;
  fit: QuickFit;
  /** The group this listing lives in. */
  groupId: string;
  /** Other groups it also fits; never counted twice. */
  alsoFits: string[];
}

export interface Group {
  /** Stable facet key, e.g. "next_role:senior-frontend-engineer". */
  id: string;
  kind: GroupKind;
  /** "Senior frontend engineer". */
  title: string;
  count: number;
  /** One sentence on why this group matters to you, citing a fact. */
  reason: Reason;
  /** Every listing in the group, in order (the page shows the first three). */
  listingIds: string[];
}

/** One stop on a lens, with the count you would see there. `value` is a `DiscoverReach` or a `DiscoverBreadth`. */
export interface LensStop {
  value: string;
  label: string;
  count: number;
}

export interface DiscoverArea {
  city?: string;
  cityFrom: CityOrigin;
  /** The resume's own location text, shown with "from your resume". */
  resumeText?: string;
  locality?: string;
}

export interface CompanyLead {
  employerId: string;
  name: string;
  city?: string;
  /** "Hiring 3 frontend roles in Pune, nothing near you now." */
  reason: string;
  careersUrl: string;
}

export interface DiscoverCoverage {
  /** Employers whose boards we read. */
  employers: number;
  /** Open listings across them. */
  listings: number;
  /** An honest line when coverage for this person is thin. */
  note?: string;
}

/** `GET /api/discover?profileId=…&city=…&locality=…&reach=…&breadth=…&lane=…&order=…&otherCity=…`. */
export interface DiscoverResponse {
  area: DiscoverArea;
  lane: DiscoverLane;
  reach: DiscoverReach;
  breadth: DiscoverBreadth;
  order: DiscoverOrder;
  /** Listings shown in this lane at this reach and breadth. */
  total: number;
  reachStops: LensStop[];
  breadthStops: LensStop[];
  laneCounts: { near: number; remote: number; city: number };
  /** One data-grounded hint, e.g. "Widening to 25 km adds 19 roles, 8 in Senior frontend engineer." */
  suggestion?: string;
  groups: Group[];
  listings: ListingCard[];
  companies: CompanyLead[];
  coverage: DiscoverCoverage;
  /** "Why this order": the signals used, in plain words. */
  signals: string[];
  /** ISO time the employer boards were last read. */
  refreshedAt?: string;
}

export interface DiscoverCity {
  name: string;
  localities: string[];
}

/** `GET /api/discover/cities`. */
export interface DiscoverCitiesResponse {
  cities: DiscoverCity[];
}

/** `GET /api/discover/listings/{id}`. `postingText` is what the web app sends to `POST /api/match` as `text`. */
export interface ListingDetail {
  card: ListingCard;
  postingText: string;
}
