/**
 * Graph helpers shared by the Now and Next views: score tiers, role/requirement ordering and
 * popover placement. Pure functions only — components decide how to render.
 */
import type { Profile, Role, RoleRequirement } from '../../shared/types';

/** Default popover bounds when a caller does not pass its own. */
const STAGE_W = 960;
const STAGE_H = 700;

export type Tier = 'expert' | 'pro' | 'dev' | 'gap';

export function tier(score: number): Tier {
  if (score >= 80) return 'expert';
  if (score >= 60) return 'pro';
  if (score >= 40) return 'dev';
  return 'gap';
}

export const TIER_LABEL: Record<Tier, string> = {
  expert: 'Expert',
  pro: 'Proficient',
  dev: 'Developing',
  gap: 'Gap',
};

/** Roles ranked by alignment, best first; at most `max` (default 6). */
export function rankRoles(profile: Profile, max = 6): Role[] {
  return [...profile.roles].sort((a, b) => b.alignment - a.alignment).slice(0, max);
}

/** Where a role stands among the person's roles by alignment (1 = closest), and how many there are. No percentage. */
export function roleStanding(profile: Profile, roleId: string): { rank: number; count: number } {
  const sorted = [...profile.roles].sort((a, b) => b.alignment - a.alignment);
  const i = sorted.findIndex((r) => r.id === roleId);
  return { rank: i < 0 ? sorted.length : i + 1, count: sorted.length };
}

const ordinal = (n: number) => {
  const r10 = n % 10;
  const r100 = n % 100;
  if (r10 === 1 && r100 !== 11) return `${n}st`;
  if (r10 === 2 && r100 !== 12) return `${n}nd`;
  if (r10 === 3 && r100 !== 13) return `${n}rd`;
  return `${n}th`;
};

/** "closest of your 5 roles", "2nd closest of your 5 roles", "your only role". */
export function rankLabel(rank: number, count: number): string {
  if (count <= 1) return 'your only role';
  return rank <= 1 ? `closest of your ${count} roles` : `${ordinal(rank)} closest of your ${count} roles`;
}

/** A role's requirements as radar axes: the ones you have first, then the gaps, original order within each. */
export function orderRequirements(role: Role, max = 7): RoleRequirement[] {
  return [...role.requirements.filter((r) => r.have), ...role.requirements.filter((r) => !r.have)].slice(0, max);
}

/** Stable key for a requirement, given its index in the `orderRequirements` result. */
export function reqKey(req: RoleRequirement, index: number): string {
  return `${index}:${req.name}`;
}

/**
 * Place a popover of size pw×ph beside a point (x,y) with radius r, kept inside `bounds`.
 * It opens on the side with more room; `pad` is the minimum gap to the edges (2px looser vertically, as before).
 */
export function placePopover(
  x: number,
  y: number,
  r: number,
  pw = 304,
  ph = 340,
  bounds: { w: number; h: number } = { w: STAGE_W, h: STAGE_H },
  pad = 8,
) {
  const padY = pad + 2;
  let left = x <= bounds.w / 2 ? x + r + 22 : x - r - 22 - pw;
  left = Math.max(pad, Math.min(bounds.w - pad - pw, left));
  const top = Math.max(padY, Math.min(bounds.h - ph - padY, y - 96));
  return { left: Math.round(left), top: Math.round(top) };
}
