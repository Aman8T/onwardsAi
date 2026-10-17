/**
 * Accounts and sign-in: the contract between the auth routes (`/api/auth/*`, `/api/me`) and the web app.
 *
 * Mirrored by backend/tayyari/contract/auth.py; tests/unit/contract/test_auth_parity.py parses this file and
 * fails when the two drift. Keep to what that parser reads: interfaces, literal unions, `x?:` optionals,
 * `T[]`, inline objects. No `Record`, `null`, tuples or `typeof` in types.
 *
 * An anonymous visitor's `/api/me` has no `user`: absent, not null. `entitlements` is part of the shape from
 * the start so the screens can render it; until payments exist every value is the empty one.
 * `anonymousMatch` is true when a visitor with no sign-in may match a posting on this server (always for everyone,
 * signed in or not): the setting `TAYYARI_MATCH__ALLOW_ANONYMOUS`, which by default is on exactly when the server
 * offers no way to sign in.
 */

export type Currency = 'INR' | 'USD';

/** Longest display name a person may set (`PATCH /api/me`). */
export const NAME_MAX_CHARS = 80;

export interface MeUser {
  id: string;
  email: string;
  name?: string;
  pictureUrl?: string;
  currency?: Currency;
}

/** One profile the account owns (the "Your profiles" list). `createdAt` is an ISO timestamp. */
export interface MeProfile {
  id: string;
  createdAt: string;
  goal?: string;
  personName?: string;
}

export interface MeEntitlements {
  credits: number;
  /** ISO timestamp until which every posting is included, when a pass is active. */
  passUntil?: string;
  freeMatchAvailable: boolean;
  /** True for an account on the server's unlimited list (`TAYYARI_GUARD__UNLIMITED_EMAILS`): no limits, nothing charged. */
  unlimited?: boolean;
}

/** Which ways of signing in this server offers; the sign-in sheet hides the others. */
export interface MeProviders {
  google: boolean;
  magic: boolean;
}

/** `GET /api/me`, for anonymous visitors and signed-in people alike. */
export interface MeResponse {
  user?: MeUser;
  profiles: MeProfile[];
  entitlements: MeEntitlements;
  providers: MeProviders;
  /** May a visitor with no sign-in use `/api/match*`? True by default only when `providers` are both false. */
  anonymousMatch: boolean;
}

/** `POST /api/auth/magic`. */
export interface MagicLinkRequest {
  email: string;
}

/** The 202 body of `POST /api/auth/magic`. `devLink` exists only on a local server with no email key. */
export interface MagicLinkResponse {
  ok: boolean;
  devLink?: string;
}

/** `POST /api/me/claim`: attach an unowned profile (and its portfolio) to the signed-in account. */
export interface ClaimRequest {
  profileId: string;
}

/** `PATCH /api/me`. */
export interface MePatch {
  currency?: Currency;
  name?: string;
}
