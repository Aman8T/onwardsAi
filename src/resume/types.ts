/**
 * The types the resume screens pass around: the contract of `shared/resume.ts` (and the parts of `shared/match.ts`
 * it reuses), re-exported from one place, plus the few shapes that only the page knows.
 *
 * `Fact` here is the match contract's (a cited fact with the words of its source), not the portfolio's.
 */
import type { ResumeOpKind } from '../../shared/resume';

export type {
  BlockEntry,
  BlockEntryKind,
  BlocksResponse,
  BulletMark,
  FinaliseReport,
  FinaliseRequest,
  FinaliseResponse,
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
export { MAX_LINE_CHARS, MAX_RESUME_OPS, RESUME_VERSION } from '../../shared/resume';
export type { Bullet, EducationEntry, ExperienceEntry, Fact, FactRef, FactSource, LintItem, LintReport, ResumeVariant, SkillGroup } from '../../shared/match';

export type FileKind = 'pdf' | 'docx';

/** What the page is waiting on. One thing at a time: every action is refused while another is in flight. */
export type Busy =
  | { kind: 'op'; op: ResumeOpKind; target: string }
  | { kind: 'make'; roleId: string }
  | { kind: 'finalise' }
  | { kind: 'default' }
  | { kind: 'download'; file: FileKind };

export type LoadStatus = 'idle' | 'loading' | 'ready' | 'error';

/** A failed request, as the screens show it: a sentence, and the HTTP status the screens decide from. */
export interface Failure {
  message: string;
  status: number;
}
