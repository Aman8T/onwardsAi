/**
 * The entitlement chip ("Pass until 12 Nov", "1 free this month", "2 postings left", "Unlimited") is the payments wave's
 * (`src/billing/EntitlementChip.tsx`, words in `src/billing/format.ts`). The match header and `scripts/match-parity.tsx`
 * keep importing it from here, so the screens did not have to change when it landed.
 *
 * The match header's chip is hidden for a signed-out visitor on a server that matches without an account: that
 * visitor has no allowance, and the zero-entitlement body `/api/me` gives them would otherwise read as a free posting.
 */
import { useMe } from '../auth/useMe';
import { EntitlementChip as BillingChip } from '../billing/EntitlementChip';

export function EntitlementChip({ className }: { className?: string }) {
  const { matchAccess } = useMe();
  if (matchAccess === 'anonymous') return null;
  return <BillingChip className={className} />;
}

export { entitlementText } from '../billing/format';
