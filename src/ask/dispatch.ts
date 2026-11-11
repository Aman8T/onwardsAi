/**
 * What each button the coach can hand over does. One entry per destination of `shared/capabilities.ts`, typed as a
 * `Record<Destination, …>`, so a destination added there without a handler here fails the typecheck.
 *
 * Every handler only opens a screen. Four shapes, because the app has no router and some screens are not reached by a
 * store action:
 *   go       a store action (the common case); it may fail to find its target and says so by returning false
 *   href     a real link to a page of its own (Apply for me is its own shell, so this is `<a href>`)
 *   share    the radar card's share sheet, which owns its own state (`card/ShareButton`)
 *   confirm  a screen that clears the person's work, so the card asks first (New analysis)
 */
import type { Destination } from '../../shared/capabilities';
import type { Profile } from '../../shared/types';
import { applyPath } from '../apply/route';
import { rankRoles, roleStanding } from '../lib/layout';
import { requirementAction, roleAction } from '../lib/prompts';
import { listPlans } from '../prepare/api';
import { actions } from '../store';

export interface DispatchContext {
  profile: Profile;
  /** The id the capability's target kind names (a role, plan, match or tracked role), when the coach gave one. */
  target?: string;
}

export type Handler =
  | { kind: 'go'; noun: string; run: (c: DispatchContext) => boolean | Promise<boolean> }
  | { kind: 'href'; noun: string; href: (c: DispatchContext) => string }
  | { kind: 'share'; noun: string }
  | { kind: 'confirm'; noun: string; warning: string; run: (c: DispatchContext) => void };

/** Reopen a stored plan the way the dashboard does: the role's question, or the gap's, so its thread opens with that plan. */
async function reopenPlan({ profile, target }: DispatchContext): Promise<boolean> {
  if (!target) return false;
  const plans = await listPlans(profile.id).catch(() => []);
  const plan = plans.find((p) => p.id === target);
  const role = plan ? profile.roles.find((r) => r.id === plan.roleId) : undefined;
  if (!plan || !role) return false;
  const req = plan.requirement ? role.requirements.find((r) => r.name === plan.requirement) : undefined;
  const a = req ? requirementAction(req, role) : roleAction(role, roleStanding(profile, role.id));
  actions.openChat(a.origin, a.question, { roleId: role.id, ...(req ? { requirement: req.name } : {}) });
  return true;
}

export const DISPATCH: Record<Destination, Handler> = {
  open_role: {
    kind: 'go',
    noun: 'role',
    run: ({ profile, target }) => {
      const known = target ? profile.roles.some((r) => r.id === target) : false;
      // an id the graph does not hold opens the top role instead of a blank screen
      actions.openRole(known ? target! : (rankRoles(profile)[0]?.id ?? null));
      return known || !target;
    },
  },
  practice: { kind: 'go', noun: 'Practice', run: () => (actions.openPractice({ screen: 'hub' }), true) },
  redrill: { kind: 'go', noun: 'Re-drill', run: () => (actions.openPractice({ screen: 'redrill' }), true) },
  match: { kind: 'go', noun: 'Match', run: () => (actions.openMatch(), true) },
  discover: { kind: 'go', noun: 'Discover', run: () => (actions.openDiscover({ tab: 'roles' }), true) },
  // Your applications holds no per-role address: the tab opens, and the role is in its list
  open_application: { kind: 'go', noun: 'applications', run: () => (actions.openDiscover({ tab: 'applications' }), true) },
  apply: {
    kind: 'href',
    noun: 'Apply',
    href: ({ target }) => applyPath({ screen: 'new', resume: 'saved', ...(target ? { matchId: target } : {}) }),
  },
  open_plan: { kind: 'go', noun: 'plan', run: reopenPlan },
  portfolio: { kind: 'go', noun: 'portfolio', run: () => (void actions.openPortfolio(), true) },
  resumes: { kind: 'go', noun: 'resumes', run: () => (actions.openResume(), true) },
  share_card: { kind: 'share', noun: 'sharing' },
  weekly: { kind: 'go', noun: 'Weekly', run: () => (actions.openWeekly(), true) },
  new_analysis: {
    kind: 'confirm',
    noun: 'New analysis',
    warning: 'This clears your graph and every chat thread on this device.',
    run: () => actions.startOver(),
  },
  pricing: { kind: 'go', noun: 'Pricing', run: () => (actions.openPricing(), true) },
};
