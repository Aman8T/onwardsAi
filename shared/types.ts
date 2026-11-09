/**
 * Types shared by the server and the web app. This file is the contract:
 * the synthesizer produces a `Profile`, the graphs render it, and the chat
 * receives it as context.
 */

export type SourceKind = 'resume' | 'github' | 'lovable' | 'linkedin' | 'portfolio' | 'link' | 'notes';

/** One connected source after a Haiku reader has gone through it. */
export interface SourceSummary {
  id: string; // stable id, e.g. "resume", "github", "lovable-1", "link-2"
  kind: SourceKind;
  label: string; // "Resume", "GitHub", "Lovable", "Portfolio", "Kaggle"…
  url?: string;
  /** Short stat line shown under the label: "34 repos · 9 languages". */
  sub: string;
  /** 1–3 sentence summary of what this source shows about the person. */
  summary: string;
  /** Concrete observations, including improvement opportunities (shown in the source popover). */
  notes: string[];
  ok: boolean;
  error?: string;
}

export interface Evidence {
  /** SourceSummary.id this evidence comes from. */
  sourceId: string;
  text: string;
}

export interface Domain {
  id: string;
  name: string; // "Frontend Engineering"
  short: string; // "Frontend" — label under the node
  score: number; // 0–100
  summary: string;
}

export interface Skill {
  id: string;
  name: string;
  domainId: string;
  score: number; // 0–100
  evidence: Evidence[];
  /** Titles of roles (Role.title) this skill would unlock or strengthen. */
  unlocks: string[];
}

export interface RoleRequirement {
  name: string;
  /** Skill.id when this requirement maps to a skill on the current graph. */
  skillId?: string;
  have: boolean;
  current: number; // 0–100, the person's level today
  target: number; // 0–100, what the role expects
}

export interface Role {
  id: string;
  title: string;
  alignment: number; // 0–100
  eta: string; // "~4 months"
  why: string; // one or two sentences
  requirements: RoleRequirement[];
}

export interface Person {
  name: string;
  headline: string; // "Frontend Engineer · 2.4 yrs · Bengaluru"
  currentRole: string; // "Frontend Engineer"
  location?: string;
  yearsExperience?: number;
  summary: string;
}

/**
 * One piece of the read: the three or four sentences about the person that open the dashboard. The pieces join with
 * nothing between them into the prose; a piece that rests on a skill of the graph carries that skill's id, and the
 * dashboard opens that skill's evidence under it.
 */
export interface ReadSegment {
  text: string;
  skillId?: string;
}

export interface Profile {
  id: string;
  createdAt: string;
  goal?: string;
  person: Person;
  /** How well the person covers their CURRENT role, with a one-line insight. */
  mastery: { score: number; insight: string };
  sources: SourceSummary[];
  domains: Domain[];
  skills: Skill[];
  roles: Role[];
  /** Model ids used, for the "built with" footnote. */
  models: { reader: string; synth: string };
  /** The read (absent on profiles built before it existed, and when the synthesizer gave too little of it). */
  read?: ReadSegment[];
}

/* ───────────────────────── analysis stream ───────────────────────── */

export type ReaderStatus = 'queued' | 'reading' | 'done' | 'error';

export interface ReaderState {
  sourceId: string;
  kind: SourceKind;
  /** What the source is: file name or URL without protocol. */
  src: string;
  /** Human label of the source type: "Resume · PDF", "GitHub profile". */
  kindLabel: string;
  /** Agent display name: "Resume reader", "GitHub analyst"… */
  agent: string;
  status: ReaderStatus;
  /** Current activity or final one-line result. */
  line: string;
  progress: number; // 0–100
}

export type AnalysisEvent =
  | { type: 'readers'; readers: ReaderState[] }
  | { type: 'reader'; reader: ReaderState }
  | { type: 'signal'; label: string; src: string; kind: SourceKind }
  | { type: 'log'; who: string; text: string; kind?: SourceKind | 'sonnet' }
  | { type: 'phase'; phase: 'reading' | 'synthesizing' | 'done'; label: string }
  | { type: 'synth'; step: 0 | 1 | 2 | 3; chars?: number }
  | { type: 'done'; profile: Profile }
  | { type: 'error'; message: string };

/* ───────────────────────── chat ───────────────────────── */

/** What the user clicked to start a thread. */
export type ChatOriginKind = 'skill' | 'domain' | 'source' | 'role' | 'requirement' | 'you' | 'free';

export interface ChatOrigin {
  kind: ChatOriginKind;
  /** id of the node clicked (skill id, role id, source id…). */
  refId?: string;
  /** Display title: "RAG", "AI Product Engineer", "GitHub". */
  title: string;
  /** Small caption: "AI & LLM Apps · 34/100", "57% aligned". */
  caption: string;
}

export interface ChatRequest {
  profileId: string;
  message: string;
  origin?: ChatOrigin;
  /** Agent SDK session id returned by the previous turn of this thread. */
  sessionId?: string;
}

export type ChatEvent =
  | { type: 'delta'; text: string }
  | { type: 'tool'; name: string; detail: string }
  | { type: 'done'; sessionId: string }
  | { type: 'error'; message: string };
