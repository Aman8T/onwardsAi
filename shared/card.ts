/**
 * The radar card (R1, canvas https://claude.ai/artifact/LG8rk27K2RfG68giTXcoX5): a person's skill shape shared as an image
 * for a GitHub README, and as a large picture when the link is posted on LinkedIn. The contract between `/api/card` and the
 * web app's share sheet.
 *
 *   GET  /api/card?profileId=        CardView (with `blocked` for the sample or an anonymous profile)
 *   PUT  /api/card                   CardUpdate -> CardView (turning sharing on makes a slug; off clears it)
 *   POST /api/card/new-link          CardNewLink -> CardView (a new slug; the old one stops at once)
 *
 * Public, no account: `/r/{slug}.svg?theme=light|dark|transparent&size=md|sm&compare={roleId}`, `/og/{slug}-{version}.png`
 * (1200 × 627) and `/u/{slug}` (the page the card links to).
 *
 * On the card: the graph's areas in their own order with their 0–100 scores, up to three skills the person picks, and one
 * role of the graph as a lighter outline. Never evidence text, a name, an email, a username, a rank, a percentile or a match
 * percentage. Everything on it is worked out in code.
 *
 * Mirrored by backend/tayyari/contract/card.py; tests/unit/contract/test_card_parity.py reads this file. Same subset as the
 * other contracts: interfaces, literal unions, `x?:` optionals, `T[]`.
 */

export const CARD_SKILLS_MAX = 3;
export const CARD_SKILLS_OFFERED = 8;
export const CARD_AREAS_MAX = 8;
/** Fewer areas than this make no shape: the card draws bars and no leaf. */
export const CARD_AREAS_MIN_RADAR = 3;
export const SLUG_LEN = 12;

/** `match` is a setting only: the README snippet becomes a `<picture>` (the dark card for dark visitors). */
export type CardTheme = 'light' | 'dark' | 'transparent' | 'match';

export type CardSize = 'md' | 'sm';

/** Why sharing is not offered: the shared sample, or a graph no account owns (it could never be turned off). */
export type CardBlocked = 'sample' | 'anonymous';

/** One area of the graph, in the graph's own order. */
export interface CardArea {
  id: string;
  name: string;
  /** The rim label (`Domain.short`, at most 14 characters). */
  short: string;
  score: number;
  /** What the chosen role expects here, worked out in code; only when a role is chosen. */
  roleTarget?: number;
}

export interface CardSkill {
  id: string;
  name: string;
  score: number;
}

export interface CardRole {
  id: string;
  title: string;
}

export interface CardSettings {
  shared: boolean;
  /** Skill ids, at most 3. */
  skills: string[];
  roleId?: string;
  theme: CardTheme;
}

/** The owner's view of their card. The link fields are present only while sharing is on. */
export interface CardView {
  settings: CardSettings;
  areas: CardArea[];
  /** The top skills by score, the ones that can be named. */
  skillsOffered: CardSkill[];
  rolesOffered: CardRole[];
  blocked?: CardBlocked;
  slug?: string;
  pageUrl?: string;
  svgUrl?: string;
  ogUrl?: string;
  /** The README snippet: markdown, or a `<picture>` for the `match` theme. */
  markdown?: string;
  updatedAt?: string;
}

export interface CardUpdate {
  profileId: string;
  shared: boolean;
  skills: string[];
  roleId?: string;
  theme: CardTheme;
}

export interface CardNewLink {
  profileId: string;
}
