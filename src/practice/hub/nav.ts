/**
 * Where the buttons of the Practice screens go. The screens (`HubView`, `DebriefView`, `ProgressView`) are presentational:
 * they call these, and `liveNav` is the one that moves the app (the store and the address). The dev page passes a stub
 * that says where each button would go, so a screen can be looked at with no app around it.
 */
import type { InterviewKind, RoleSource } from '../../../shared/interview';
import { actions } from '../../store';

export interface PracticeNav {
  /** The plan for a kind, before it starts; `role` names the role of a search with AI it is built from. */
  setup(kind: InterviewKind, role?: RoleSource): void;
  room(sessionId: string): void;
  debrief(sessionId: string): void;
  hub(): void;
  progress(): void;
  /** Discover's roles (to find one to practise for). */
  discover(): void;
  applications(): void;
  /** A role's prep page. */
  prepare(role: RoleSource): void;
  graph(): void;
  /** Home, to analyse a resume. */
  analyse(): void;
}

export const liveNav: PracticeNav = {
  setup: (kind, role) => actions.openPractice({ screen: 'setup', kind, run: role?.runId, job: role?.jobId }),
  room: (session) => actions.openPractice({ screen: 'room', session }),
  debrief: (session) => actions.openPractice({ screen: 'debrief', session }),
  hub: () => actions.openPractice({ screen: 'hub' }),
  progress: () => actions.openPractice({ screen: 'progress' }),
  discover: () => actions.openDiscover({ tab: 'roles' }),
  applications: () => actions.openDiscover({ tab: 'applications' }),
  prepare: (role) => actions.openDiscover({ tab: 'prepare', run: role.runId, job: role.jobId }),
  graph: () => actions.showGraph(),
  analyse: () => actions.goto('onboarding'),
};
