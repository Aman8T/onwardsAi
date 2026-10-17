/**
 * What carries over, wired: the person's whole practice comes from the server (`GET /api/interview/sessions`), and taking
 * a fact back goes through `POST /api/interview/sessions/{id}/facts`. The screen itself is `ProgressView`.
 */
import type { Profile } from '../../../shared/types';
import { fetchHistory, setFact } from '../api';
import { liveNav } from '../hub/nav';
import { useLoad } from '../hub/useLoad';
import { ProgressView } from './ProgressView';

export function ProgressScreen({ profile }: { profile: Profile }) {
  const { load, reload, patch } = useLoad((signal) => fetchHistory(profile.id, signal), [profile.id]);
  return (
    <ProgressView
      history={load}
      onReload={reload}
      nav={liveNav}
      onRemoveFact={async (fact) => {
        await setFact(fact.sessionId, { profileId: profile.id, slotId: fact.slotId, add: false });
        patch((h) => ({ ...h, facts: h.facts.filter((f) => !(f.sessionId === fact.sessionId && f.slotId === fact.slotId)) }));
      }}
    />
  );
}
