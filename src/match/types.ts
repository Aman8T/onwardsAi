/**
 * What the match screens pass around on top of the contract in `shared/match.ts`: the NDJSON events of
 * `POST /api/match` and `POST /api/match/{id}/resume`, and the shapes of the GET answers.
 *
 * `shared/match.ts` has no event types (it is the contract M1 mirrors in Python), so they live here until M2
 * settles the wire format. Where the backend's own names differ (`GatesReady.answers`, `CoverageBatch.rows`) the
 * reader in `api.ts` accepts both, so a route that forwards M1's events unchanged still works.
 *
 * `Fact` here is the match contract's, not the portfolio's: import it as `MatchFact`.
 */
import type {
  Coverage,
  CoverNote,
  Fact as MatchFact,
  GateAnswer,
  MatchBand,
  MatchResult,
  Posting,
  Question,
  Radar,
  ResumeVariant,
} from '../../shared/match';

export type { MatchFact };

/** Machine codes an `error` event (or a failed response) may carry; anything else is shown as its message. */
export type MatchErrorCode = 'unauthorized' | 'payment_required' | 'no_requirements' | 'export_blocked' | 'rate_limited';

/** `POST /api/match`: one JSON line per event. `facts` rides on `posting` so citations can render as rows arrive. */
export type MatchEvent =
  | { type: 'posting'; posting: Posting; facts?: MatchFact[] }
  | { type: 'gates'; gates: GateAnswer[]; blocked: boolean }
  | { type: 'coverage'; coverage: Coverage[] }
  | { type: 'radar'; radar: Radar }
  | { type: 'questions'; questions: Question[] }
  | { type: 'done'; matchId: string; result?: MatchResult; createdAt?: string }
  | { type: 'error'; message: string; code?: MatchErrorCode };

/** `POST /api/match/{id}/resume`: `variant` (the composed resume, possibly more than once) then `done`. */
export type ResumeEvent =
  | { type: 'variant'; variant: ResumeVariant }
  | { type: 'done'; variant?: ResumeVariant }
  | { type: 'error'; message: string; code?: MatchErrorCode };

/** `GET /api/match/{id}`: the stored match with the posting it was made from and the evidence it cites. */
export interface MatchDetail {
  id: string;
  createdAt?: string;
  posting: Posting;
  facts: MatchFact[];
  result: MatchResult;
}

/** One row of `GET /api/matches`. */
export interface MatchSummary {
  id: string;
  title: string;
  company?: string;
  band: MatchBand;
  createdAt: string;
}

/** `POST /api/match/{id}/answers` body item. */
export interface AnswerInput {
  questionId: string;
  text: string;
}

export interface SubmitInput {
  profileId: string;
  text?: string;
  url?: string;
}

export type { CoverNote, ResumeVariant };
