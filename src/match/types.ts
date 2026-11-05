/**
 * What the match screens pass around on top of the contract in `shared/match.ts`.
 *
 * The wire shapes themselves are `shared/match_api.ts` (mirrored by `backend/tayyari/contract/match_api.py`, checked by
 * `tests/unit/match/test_match_api_parity.py`). Requests and list rows are those shapes as they are. The stream events
 * and the stored match below are the same shapes with a few fields relaxed, because the readers in `api.ts` take what
 * an older server sent too (a `done` without `createdAt`, `GatesReady.answers` for `gates`, a flattened result).
 *
 * `Fact` here is the match contract's, not the portfolio's: import it as `MatchFact`.
 */
import type {
  Coverage,
  CoverNote,
  Fact as MatchFact,
  GateAnswer,
  MatchResult,
  Posting,
  Question,
  Radar,
  ResumeVariant,
} from '../../shared/match';
import type { AnswerItem, MatchErrorCode, MatchListItem, MatchRequest } from '../../shared/match_api';

export type { MatchFact };

/** Machine codes an `error` event (or a failed response) may carry; `failed` is everything else. */
export type { MatchErrorCode };

/** `POST /api/match`, as `api.ts` reads it (`MatchStreamEvent` relaxed). `facts` rides on `posting` so citations can
 * render as rows arrive. */
export type MatchEvent =
  | { type: 'posting'; posting: Posting; facts?: MatchFact[] }
  | { type: 'gates'; gates: GateAnswer[]; blocked: boolean }
  | { type: 'coverage'; coverage: Coverage[] }
  | { type: 'radar'; radar: Radar }
  | { type: 'questions'; questions: Question[] }
  | { type: 'done'; matchId: string; result?: MatchResult; createdAt?: string }
  | { type: 'error'; message: string; code?: MatchErrorCode };

/** `POST /api/match/{id}/resume`, as `api.ts` reads it (`ResumeStreamEvent` relaxed): `variant` (the composed resume,
 * possibly more than once) then `done`. */
export type ResumeEvent =
  | { type: 'variant'; variant: ResumeVariant }
  | { type: 'done'; variant?: ResumeVariant }
  | { type: 'error'; message: string; code?: MatchErrorCode };

/** `GET /api/match/{id}`, as `api.ts` reads it (`MatchDetail` of `shared/match_api.ts` with `createdAt` optional): the
 * stored match with the posting it was made from and the evidence it cites. */
export interface MatchDetail {
  id: string;
  createdAt?: string;
  posting: Posting;
  facts: MatchFact[];
  result: MatchResult;
}

/** One row of `GET /api/matches`. */
export type MatchSummary = MatchListItem;

/** `POST /api/match/{id}/answers` body item. */
export type AnswerInput = AnswerItem;

/** `POST /api/match` body. */
export type SubmitInput = MatchRequest;

export type { CoverNote, ResumeVariant };
