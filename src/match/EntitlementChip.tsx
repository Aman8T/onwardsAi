/**
 * The entitlement chip ("Pass until 12 Nov", "1 free this month", "2 postings left") is the payments wave's
 * (`src/billing/EntitlementChip.tsx`, words in `src/billing/format.ts`). The match header and `scripts/match-parity.tsx`
 * keep importing it from here, so the screens did not have to change when it landed.
 */
export { EntitlementChip } from '../billing/EntitlementChip';
export { entitlementText } from '../billing/format';
