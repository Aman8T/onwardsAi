/**
 * The Practice hub, wired: the person's practice so far comes from the server (`GET /api/interview/sessions`), the roles
 * to practise for from Your applications (kept in this browser), and every button goes through the store. The screen
 * itself is `HubView`.
 */
import type { Profile } from '../../../shared/types';
import { useMe } from '../../auth/useMe';
import { useTracker } from '../../discover/applications/tracker';
import { isSampleId } from '../../match/model';
import { fetchHistory } from '../api';
import { HubView } from './HubView';
import { liveNav } from './nav';
import { useLoad } from './useLoad';

export function HubScreen({ profile }: { profile: Profile }) {
  const tracker = useTracker(profile.id);
  const { unlimited } = useMe();
  const { load, reload } = useLoad((signal) => fetchHistory(profile.id, signal), [profile.id]);
  return <HubView sample={isSampleId(profile.id)} tracked={tracker.items} history={load} unlimited={unlimited} onReload={reload} nav={liveNav} />;
}
