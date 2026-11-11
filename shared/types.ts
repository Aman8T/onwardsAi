/**
 * Types shared by the server and the web app. This file is the contract:
 * the synthesizer produces a `Profile`, the graphs render it, and the chat
 * receives it as context.
 */
import type { Destination } from './capabilities';

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

export type ReadTone = 'proud' | 'roast' | 'motivational' | 'quirky' | 'sarcastic' | 'plain';

/**
 * One line of the read: a one-liner about the person in a named tone, shown one at a time at the top of the dashboard.
 * A line that rests on one skill of the graph carries that skill's id, and the dashboard opens that skill's evidence
 * under it. `tone` is optional only so profiles written before the lines existed still load; the dashboard shows toned lines.
 */
export interface ReadLine {
  text: string;
  tone?: ReadTone;
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
  read?: ReadLine[];
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
  | { type: 'done'; profile: Profile; /** Only on a re-read from a profile: did the resumes, own-words lines and notes move to the new profile? */ carried?: boolean }
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

/** One tracked role from the person's browser (tracked roles live only there). The server clips every string, strips NULs and
 * keeps at most 20; `id` is only ever a valid button target (`open_application`), and the rest is shown to the coach as
 * data, never as instructions. */
export interface ChatDeskTracked {
  id: string;
  title: string;
  employer: string;
  stage: string;
  note?: string;
  appliedAt?: string;
}

/** What only the browser knows, sent with a chat turn. */
export interface ChatDesk {
  tracked: ChatDeskTracked[];
}

/** The resume the person has open beside the coach (`/resume`). It only names it: a resume the caller may not open is ignored. */
export interface ChatResume {
  id: string;
}

export interface ChatRequest {
  profileId: string;
  message: string;
  origin?: ChatOrigin;
  /** Agent SDK session id returned by the previous turn of this thread. */
  sessionId?: string;
  /** The person's tracked roles (at most 20), so the coach can answer about them and hand a button to one. */
  desk?: ChatDesk;
  /**
   * The resume in view. When the caller may open it, the coach sees its lines (each with a short id, its text and the quotes
   * of the facts it rests on) and may propose one rewrite against a line (a `rewrite` event).
   */
  resume?: ChatResume;
}

export type ChatEvent =
  | { type: 'delta'; text: string }
  | { type: 'tool'; name: string; detail: string }
  /**
   * The coach proposes one button, after the text. `destination` is one of `shared/capabilities.ts`; `target` is the id
   * its kind needs (a role, plan, match, tracked role or skill id) or absent; `label` is the capability's button text;
   * `title` is the target's own title (role title, plan role, match title, tracked title and employer) or absent. No model
   * prose rides on it. The button only opens a screen.
   */
  | { type: 'action'; destination: Destination; target?: string; label: string; title?: string }
  /**
   * The coach proposes different words for one line of the resume in view, after its text. Code has checked it: the line is
   * on that resume, the words rest only on the facts the line cites (no new figure, name, term or scope verb), they fit
   * `MAX_LINE_CHARS` and differ from `before`. Nothing has changed: the person accepts it, and the client then sends the
   * `edit` op `{ op: 'edit', bulletId: lineId, text: after }` through `PATCH /api/resumes/{resumeId}`, so Finalise still
   * checks the line; or dismisses it. At most one `rewrite` or `action` per answer.
   */
  | { type: 'rewrite'; resumeId: string; lineId: string; before: string; after: string }
  | { type: 'done'; sessionId: string }
  | { type: 'error'; message: string };
