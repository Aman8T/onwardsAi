/**
 * Resume → Portfolio: the contract between the portfolio API and the web app.
 *
 * Mirrored by backend/tayyari/contract/portfolio.py; tests/unit/contract/test_portfolio_parity.py parses
 * this file and fails when the two drift. Keep to what that parser reads: interfaces, literal unions,
 * `x?:` optionals, `T[]`, inline objects. No `Record`, `null`, tuples or `typeof` in types.
 *
 * Shape: a `Portfolio` is presentation (`spec`: composition, style, ordered blocks) + content (`content`:
 * facts extracted from the resume, copy slots, proof stats) + a frozen `graph` snapshot. Both skins
 * (standard and the Old Newspaper "press") render the same spec + content + graph.
 */

/* ───────────────────────── catalog (the closed kit) ───────────────────────── */

export type BlockKind =
  | 'hero'
  | 'graph'
  | 'proof'
  | 'about'
  | 'experience'
  | 'projects'
  | 'clips'
  | 'achievements'
  | 'education'
  | 'openTo'
  | 'contact';

export type Composition = 'signature' | 'narrative' | 'builder' | 'foundations' | 'ledger' | 'pivot';
export type Family = 'iris' | 'glacier' | 'lagoon' | 'sage' | 'dune' | 'clay' | 'rose' | 'orchid';
export type FieldShape = 'dawn' | 'horizon' | 'corner';
export type TypePairing = 'studio' | 'editorial' | 'technical' | 'warm';
export type Corners = 'square' | 'soft' | 'round';
export type Density = 'airy' | 'compact';
export type GraphVariant = 'peaks' | 'halo' | 'ledger' | 'stellar' | 'atlas' | 'spotlight' | 'focus';
export type PortfolioSkin = 'standard' | 'press';
export type SlotOrigin = 'model' | 'resume' | 'user';
export type FactKind = 'experience' | 'project' | 'education' | 'achievement' | 'clip' | 'link' | 'contact' | 'quote';
export type PublishState = 'draft' | 'published';

/** Bumped when a block, variant or slot is added or retired; stored on every portfolio. */
export const KIT_VERSION = 1;

/** Every variant a block kind offers. A block's `variant` is a plain string validated against this list. */
export const BLOCK_VARIANTS = {
  hero: ['statement', 'split', 'centered', 'index'],
  graph: ['peaks', 'halo', 'ledger', 'stellar', 'atlas', 'spotlight', 'focus'],
  proof: ['strip'],
  about: ['lede', 'prose', 'facts'],
  experience: ['timeline', 'ledger', 'thenNow'],
  projects: ['featureList', 'grid', 'list'],
  clips: ['list'],
  achievements: ['numbers', 'list'],
  education: ['row', 'compact'],
  openTo: ['note'],
  contact: ['signOff', 'links'],
} as const satisfies { [K in BlockKind]: readonly string[] };

/** Gradient family → the hue fed to `--h` (lightness and chroma are fixed per role, see tokens.css). */
export const FAMILY_HUES = {
  iris: 275,
  glacier: 245,
  lagoon: 200,
  sage: 155,
  dune: 85,
  clay: 45,
  rose: 10,
  orchid: 320,
} as const satisfies { [K in Family]: number };

/**
 * Character limits of the copy slots. Fixed slot ids: tagline, aboutLede, aboutBody, pressHeadline,
 * pressDeck. Per-fact slots: `exp:<factId>` (up to 3 lines, one per line) and `proj:<factId>`.
 */
export const SLOT_LIMITS = {
  tagline: 90,
  aboutLede: 160,
  aboutBody: 420,
  pressHeadline: 80,
  pressDeck: 180,
  exp: 300,
  proj: 200,
} as const;

/* ───────────────────────── presentation ───────────────────────── */

export interface PortfolioStyle {
  family: Family;
  field: FieldShape;
  type: TypePairing;
  corners: Corners;
  density: Density;
}

export interface BlockSpec {
  /** Stable within a portfolio: the block kind ("hero", "projects"…). */
  id: string;
  kind: BlockKind;
  variant: string;
  /** Shown on the page at all (the owner can hide a block; empty blocks start hidden). */
  visible: boolean;
  /** Shown to viewers of the public page. */
  public: boolean;
}

export interface GraphSpec {
  /** Domains the owner hid from the page; at least min(3, n) always stay. */
  hiddenDomainIds: string[];
  /** Show the stats strip (source facts now, practice later) next to the graph. */
  stats: boolean;
}

/** Opt-in personal details; all false by default. */
export interface PortfolioPrivacy {
  email: boolean;
  phone: boolean;
  grades: boolean;
}

export interface DesignReason {
  dimension: string;
  choice: string;
  because: string;
}

export interface PortfolioSpec {
  composition: Composition;
  style: PortfolioStyle;
  /** In page order. Always contains exactly one `graph` block, within the first three. */
  blocks: BlockSpec[];
  graph: GraphSpec;
  privacy: PortfolioPrivacy;
  reasons: DesignReason[];
}

/* ───────────────────────── content ───────────────────────── */

/** Where a fact came from: the source id and a verbatim quote that was found in that source's text. */
export interface SourceRef {
  sourceId: string;
  quote: string;
}

export interface Fact {
  /** "exp-1", "proj-2", "edu-1"… */
  id: string;
  kind: FactKind;
  /** Role title, project name, degree, award, clip title, link label, contact label, or the quote itself. */
  title: string;
  /** Employer, institution, publication. */
  org?: string;
  location?: string;
  /** "2025-07" or "2025", as stated. */
  start?: string;
  /** Same format; absent with `current`. */
  end?: string;
  current?: boolean;
  /** http, https or mailto only. */
  url?: string;
  /** One line of description, verbatim from the source. */
  detail?: string;
  /** Education grade as stated ("CGPA 8.6 / 10"); public only with privacy.grades. */
  grade?: string;
  /** Verbatim bullet lines from the source. */
  highlights: string[];
  /** Technologies and topics named in the source. */
  tags: string[];
  sourceRefs: SourceRef[];
}

export interface CopySlot {
  /** "tagline", "aboutLede", "aboutBody", "pressHeadline", "pressDeck", "exp:<factId>", "proj:<factId>". */
  id: string;
  text: string;
  origin: SlotOrigin;
  /** The facts this text is grounded in (empty for user text). */
  factIds: string[];
}

/** A real count from a connected source ("389" · "GitHub stars"). */
export interface ProofStat {
  value: string;
  label: string;
  sourceId: string;
}

/** Reserved for the practice feature; never filled yet. */
export interface PracticeStats {
  sessions: number;
  mockInterviews: number;
  domainsPractised: number;
  asOf: string;
}

export interface PortfolioPerson {
  name: string;
  headline: string;
  role: string;
  location?: string;
  yearsExperience?: number;
  /** One or two letters for the typographic monogram. */
  monogram: string;
}

export interface PortfolioContent {
  person: PortfolioPerson;
  facts: Fact[];
  slots: CopySlot[];
  stats: ProofStat[];
  practice?: PracticeStats;
  /** Role titles for the opt-in "Open to" block (1–2, from the profile's best-fit roles). */
  openTo: string[];
}

/* ───────────────────────── the graph snapshot ───────────────────────── */

export interface GraphSkill {
  id: string;
  name: string;
  score: number;
  /** One evidence line for the axis popover. */
  evidence?: string;
}

export interface GraphDomain {
  id: string;
  name: string;
  short: string;
  score: number;
  /** Strongest first. The public page never carries skills under 45. */
  skills: GraphSkill[];
}

export interface GraphSnapshot {
  /** ISO date the graph was frozen (printed on the page as "as of"). */
  asOf: string;
  /** In the profile's fixed order (never sorted). */
  domains: GraphDomain[];
  skillCount: number;
  mastery?: { score: number; role: string };
  /** Labels of the sources the graph was scored from ("Resume", "GitHub"…). */
  sources: string[];
}

/* ───────────────────────── the portfolio ───────────────────────── */

export interface PublishInfo {
  state: PublishState;
  publishedAt?: string;
  defaultView: PortfolioSkin;
}

export interface Portfolio {
  profileId: string;
  /** Separate random id for the public page; sharing never exposes the profile id. */
  publicId: string;
  version: number;
  kitVersion: number;
  createdAt: string;
  updatedAt: string;
  /** Which of `looks` the spec started from. */
  lookIndex: number;
  /** Up to three candidate designs from the composer ("Try another look"). */
  looks: PortfolioSpec[];
  /** The live design (a look plus the owner's edits). */
  spec: PortfolioSpec;
  content: PortfolioContent;
  graph: GraphSnapshot;
  publish: PublishInfo;
  /** True when the profile was re-analysed after this snapshot (the editor offers "Update graph"). */
  graphOutdated?: boolean;
}

/** What a viewer receives: only visible, public blocks and opted-in details; no profile id. */
export interface PublicPortfolio {
  publicId: string;
  kitVersion: number;
  spec: PortfolioSpec;
  content: PortfolioContent;
  graph: GraphSnapshot;
  publishedAt?: string;
  defaultView: PortfolioSkin;
}

/* ───────────────────────── requests ───────────────────────── */

export interface PortfolioRequest {
  profileId: string;
  /** Build a fresh portfolio even when one exists (facts are reused when the sources did not change). */
  regenerate?: boolean;
}

export interface SlotEdit {
  id: string;
  text: string;
}

export interface PortfolioPatch {
  lookIndex?: number;
  style?: PortfolioStyle;
  /** The full ordered block list (same ids; kinds unchanged; one graph block in the first three). */
  blocks?: BlockSpec[];
  graph?: GraphSpec;
  privacy?: PortfolioPrivacy;
  /** Owner text edits (plain text, stored with origin "user"). */
  slots?: SlotEdit[];
  /** Slot ids to put back to the generated wording. */
  resetSlots?: string[];
  defaultView?: PortfolioSkin;
  /** Re-freeze the graph from the current profile. */
  refreshGraph?: boolean;
}

/* ───────────────────────── stream ───────────────────────── */

export type PortfolioEvent =
  | { type: 'progress'; step: 'facts' | 'design' | 'copy' | 'check'; label: string }
  | { type: 'draft'; portfolio: Portfolio }
  | { type: 'done'; portfolio: Portfolio }
  | { type: 'error'; message: string };
