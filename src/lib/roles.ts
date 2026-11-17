/**
 * How much of a role the person's evidence already covers, as a count shown beside the alignment percentage (the founder's
 * decision of 2026-11-17: keep the percentage, which draws people in, and give it a count anyone can check). Counted in
 * code from the role's own requirements; nothing new is scored.
 */
import type { Role } from '../../shared/types';

export interface Asks {
  have: number;
  total: number;
}

/** The role's asks the evidence covers (`have`), out of all it lists. */
export function asksOf(role: Pick<Role, 'requirements'>): Asks {
  const total = role.requirements.length;
  const have = role.requirements.filter((r) => r.have).length;
  return { have, total };
}

/** "covers 6 of 7 asks"; empty for a role that lists no asks. */
export function coversText(role: Pick<Role, 'requirements'>): string {
  const { have, total } = asksOf(role);
  return total > 0 ? `covers ${have} of ${total} ${total === 1 ? 'ask' : 'asks'}` : '';
}

/** "6 of 7 asks", the short form for tight rows; empty for a role that lists no asks. */
export function asksShort(role: Pick<Role, 'requirements'>): string {
  const { have, total } = asksOf(role);
  return total > 0 ? `${have} of ${total} ${total === 1 ? 'ask' : 'asks'}` : '';
}
