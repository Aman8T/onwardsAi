/**
 * Where the owner sheet's links go inside the app. "What would show it" opens Prepare for a near miss the way the graph
 * and Practice open it (`openChat` with the role, so the thread opens with Prepare's blocks, not prose). The near
 * miss's id is the role's id in the graph; when the graph in view does not hold that role, nothing happens.
 */
import { roleStanding } from '../lib/layout';
import { roleAction } from '../lib/prompts';
import { actions, getState } from '../store';

export function openPrepare(roleId: string): void {
  const profile = getState().profile;
  const role = profile?.roles.find((r) => r.id === roleId);
  if (!profile || !role) return;
  const a = roleAction(role, roleStanding(profile, roleId));
  actions.openChat(a.origin, a.question, { roleId });
}
