/**
 * Fixtures for the Ask dock's gallery (`/dev/ask`): the starters and the messages each state is drawn from. Nothing here
 * touches the network or the store; a `Thread` is just the shape the panel reads.
 */
import type { Profile } from '../../../shared/types';
import sampleProfile from '../../../backend/tayyari/sample/sample-profile.json';
import type { ChatMessage, Thread } from '../../store';
import type { Starter } from '../DockPanel';

export const PROFILE = sampleProfile as unknown as Profile;

export const STARTERS: Starter[] = [
  { q: 'What should I rehearse for GitLab?', reason: 'GitLab moved to interviewing on Saturday and you have not practised for it' },
  { q: 'Why could I not defend the LCP number?', reason: "last night's session · question 5" },
  { q: 'What is left in week 2 of my plan?', reason: 'AI Product Engineer · 1 of 3 moves done' },
  { q: "What are Northwind's three open questions?", reason: 'the match from Saturday is waiting on them' },
];

export const QUESTION = 'What should I rehearse for GitLab?';

export const ANSWER =
  'Three things, in this order. First, the checkout LCP number: last night you could not say how you measured it, and it is line 14 of your resume. Second, Kite UI: the posting asks who has owned a design system, and you have defended that claim twice, so lead with it. Third, testing: Engineering Practice is 55 on your graph and the posting names it, so have one honest example ready.';

const user: ChatMessage = { id: 'u1', role: 'user', text: QUESTION, tools: [], status: 'done' };

const thread = (reply: Omit<ChatMessage, 'id' | 'role' | 'tools'> & { tools?: ChatMessage['tools'] }): Thread => ({
  id: 'dock-fixture',
  origin: { kind: 'free', title: 'About you', caption: 'Asked from the dashboard' },
  createdAt: 0,
  messages: [user, { id: 'a1', role: 'assistant', tools: [], ...reply }],
});

export type StateId = 'resting' | 'open' | 'waiting' | 'streaming' | 'done' | 'stopped' | 'failed' | 'rate' | 'ceiling';

export interface DockState {
  id: StateId;
  label: string;
  /** The panel is open. */
  open: boolean;
  /** The thread in the panel; null shows the starters. */
  thread: Thread | null;
  streaming: boolean;
}

export const STATES: DockState[] = [
  { id: 'resting', label: 'Resting bar', open: false, thread: null, streaming: false },
  { id: 'open', label: 'Open · starters', open: true, thread: null, streaming: false },
  { id: 'waiting', label: 'Waiting', open: true, thread: thread({ text: '', status: 'streaming' }), streaming: true },
  {
    id: 'streaming',
    label: 'Streaming',
    open: true,
    thread: thread({ text: ANSWER.slice(0, 214), status: 'streaming', tools: [{ name: 'graph', detail: 'Reading your graph' }] }),
    streaming: true,
  },
  {
    id: 'done',
    label: 'Done · a button',
    open: true,
    thread: thread({
      text: `${ANSWER} I can set up a session on exactly these. The button below opens it; I have not started anything.`,
      status: 'done',
      action: { destination: 'practice', label: 'Start Practice', title: 'This role · Senior Frontend Engineer at GitLab' },
    }),
    streaming: false,
  },
  {
    id: 'stopped',
    label: 'Stopped',
    open: true,
    thread: thread({ text: 'The first one. In four sessions you have been asked about a number five times and defended it', status: 'stopped' }),
    streaming: false,
  },
  {
    id: 'failed',
    label: 'Failed',
    open: true,
    thread: thread({ text: '', status: 'error', failure: 'failed', error: 'The connection dropped before Leafout finished. Your question is still here.' }),
    streaming: false,
  },
  { id: 'rate', label: 'Rate-limited', open: true, thread: thread({ text: '', status: 'error', failure: 'rate', retryAfter: 24 }), streaming: false },
  { id: 'ceiling', label: 'Ceiling', open: true, thread: thread({ text: '', status: 'error', failure: 'ceiling' }), streaming: false },
];
