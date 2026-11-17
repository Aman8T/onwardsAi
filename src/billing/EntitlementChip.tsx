/**
 * The plan, as a small chip: "Day One", "Unlimited" for an account with no limits, else "Just Looking · 2 matches
 * left" ("Just Looking" when none are left). Reads the entitlements `/api/me` returns, so it needs no request of its
 * own, and shows only to a signed-in person: a visitor has no account to spend anything on.
 */
import { useMe } from '../auth/useMe';
import { entitlementText } from './format';
import '../styles/billing.css';

export function EntitlementChip({ className }: { className?: string }) {
  const { user, loaded, entitlements, unlimited } = useMe();
  if (!loaded || !user) return null;
  return (
    <span className={['chip', 'bl-chip', className].filter(Boolean).join(' ')} title={unlimited ? 'No limits on this account' : 'Your plan'}>
      {entitlementText(entitlements)}
    </span>
  );
}
