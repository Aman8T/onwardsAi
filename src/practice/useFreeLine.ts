/**
 * "Free while we test it · 3 a day", or without the count for an account with no limits. For the screens that are not
 * handed the answer as a prop (the room's plan rail, the Discover prep page): they read the account themselves.
 */
import { useMe } from '../auth/useMe';
import { freeLineFor } from './words';

export function useFreeLine(): string {
  return freeLineFor(useMe().unlimited);
}
