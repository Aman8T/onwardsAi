/**
 * The types the resume screens pass around: the contract of `shared/resume.ts` (and the parts of `shared/match.ts`
 * it reuses), re-exported from one place, plus the few shapes that only the page knows.
 *
 * `Fact` here is the match contract's (a cited fact with the words of its source), not the portfolio's.
 */
import type { MergeReport, ResumeOpKind } from '../../shared/resume';

export type {
  BlockEntry,
  BlockDisagreement,
  BlockEntryKind,
  BlockWording,
  BlocksResponse,
  BulletMark,
  FinaliseReport,
  FinaliseRequest,
  FinaliseResponse,
  FromMatchRequest,
  GrammarProposal,
  GrammarResponse,
  LineWording,
  MergeCounts,
  MergeDiffer,
  MergeItem,
  MergeReport,
  ResumeFile,
  ResumeFileOrigin,
  ResumeFilesResponse,
  UploadResponse,
  WordingsResponse,
  Finding,
  FindingAnswer,
  FindingChoice,
  FindingKind,
  MarkKind,
  MoveDir,
  PathRequest,
  PathSlot,
  ResumeBlock,
  ResumeDetail,
  ResumeKind,
  ResumeList,
  ResumeOp,
  ResumeOpKind,
  ResumeOpsRequest,
  ResumeState,
  ResumeSummary,
} from '../../shared/resume';
export { MAX_LINE_CHARS, MAX_RESUME_FILES, MAX_RESUME_OPS, RESUME_VERSION } from '../../shared/resume';
export type { Bullet, EducationEntry, ExperienceEntry, Fact, FactRef, FactSource, LintItem, LintReport, ResumeVariant, SkillGroup } from '../../shared/match';

export type FileKind = 'pdf' | 'docx';

/** What the page is waiting on. One thing at a time: every action is refused while another is in flight. */
export type Busy =
  | { kind: 'op'; op: ResumeOpKind; target: string }
  | { kind: 'make'; roleId: string }
  | { kind: 'finalise' }
  | { kind: 'default' }
  | { kind: 'grammar' }
  | { kind: 'download'; file: FileKind }
  /** A second file is being read: a model call, which can take a while. */
  | { kind: 'upload'; filename: string }
  /** The stored original of an uploaded resume is being fetched. */
  | { kind: 'original'; resumeId: string };

export type LoadStatus = 'idle' | 'loading' | 'ready' | 'error';

/** A failed request, as the screens show it: a sentence, and the HTTP status the screens decide from. */
export interface Failure {
  message: string;
  status: number;
}

/** A merge report with the resume the file was also kept as. Held for this visit: no route returns an old report. */
export interface MergeEntry {
  report: MergeReport;
  /** The resume made from the file. */
  resumeId: string;
}

/** Why a file was not added, in plain words; `retry` says whether sending the same file again is worth it. */
export interface UploadFailure {
  message: string;
  retry: boolean;
}
