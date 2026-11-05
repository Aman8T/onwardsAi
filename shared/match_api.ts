/**
 * The posting-match API's own shapes: request bodies, the NDJSON events of `POST /api/match` and
 * `POST /api/match/{id}/resume`, and the JSON answers that wrap the match contract.
 *
 * `shared/match.ts` holds what a match IS; this file holds how the API carries it:
 *
 *   POST /api/match                body `MatchRequest`; NDJSON `MatchStreamEvent`: posting, gates, coverage (one line per
 *                                  batch), radar, questions, then done (or error at any point after the stream began)
 *   GET  /api/match/{id}           `MatchDetail`
 *   GET  /api/matches              `MatchList`
 *   POST /api/match/{id}/answers   body `AnswersRequest` -> `MatchDetail`
 *   POST /api/match/{id}/resume    NDJSON `ResumeStreamEvent`: variant, then done (or error)
 *   GET  /api/match/{id}/resume    `VariantResponse`; PATCH (body `DecisionsRequest`) -> `VariantResponse`
 *   POST|GET /api/match/{id}/cover `CoverResponse`
 *
 * Mirrored by backend/tayyari/contract/match_api.py; tests/unit/match/test_match_api_parity.py reads this file together
 * with shared/match.ts and fails when the two drift. Same subset as match.ts: interfaces, literal unions, `x?:`
 * optionals, `T[]` and inline objects. The screens read every line through the tolerant readers in
 * `src/match/api.ts`, whose own types (`src/match/types.ts`) are these shapes with a few fields relaxed.
 */

import type {
  Coverage,
  CoverNote,
  DecisionChoice,
  Fact,
  GateAnswer,
  MatchBand,
  MatchResult,
  Posting,
  Question,
  Radar,
  ResumeVariant,
} from './match';

/** The longest answer to a question (the answer box has the same limit). */
export const ANSWER_MAX_CHARS = 1500;
/** The most decisions one PATCH of a resume may carry. */
export const MAX_DECISIONS = 40;
export const URL_MAX_CHARS = 2048;

/** The machine code of an `error` event; the screen decides what to show from it. `failed` is everything else. */
export type MatchErrorCode = 'unauthorized' | 'payment_required' | 'no_requirements' | 'export_blocked' | 'rate_limited' | 'failed';

/* ───────────────────────── requests ───────────────────────── */

/** `POST /api/match`: `text` (pasted) or `url` (a public page); `text` wins when both are given. */
export interface MatchRequest {
  profileId: string;
  text?: string;
  url?: string;
}

export interface AnswerItem {
  questionId: string;
  text: string;
}

/** `POST /api/match/{id}/answers`: the person's own words for up to three open questions. */
export interface AnswersRequest {
  answers: AnswerItem[];
}

/** `PATCH /api/match/{id}/resume`: answers to the open lines of the resume. */
export interface DecisionsRequest {
  decisions: DecisionChoice[];
}

/* ───────────────────────── stream events ───────────────────────── */

/** `POST /api/match`, one JSON line each. `facts` rides on `posting` so citations can render as the rows arrive. */
export type MatchStreamEvent =
  | { type: 'posting'; posting: Posting; facts: Fact[] }
  | { type: 'gates'; gates: GateAnswer[]; blocked: boolean }
  | { type: 'coverage'; coverage: Coverage[] }
  | { type: 'radar'; radar: Radar }
  | { type: 'questions'; questions: Question[] }
  | { type: 'done'; matchId: string; createdAt: string }
  | { type: 'error'; message: string; code?: MatchErrorCode };

/** `POST /api/match/{id}/resume`: the composed resume, then `done`. */
export type ResumeStreamEvent =
  | { type: 'variant'; variant: ResumeVariant }
  | { type: 'done' }
  | { type: 'error'; message: string; code?: MatchErrorCode };

/* ───────────────────────── answers ───────────────────────── */

/** `GET /api/match/{id}` (and the answer to posting answers): the stored match, the posting it was made from and the
 * evidence its rows cite. */
export interface MatchDetail {
  id: string;
  createdAt: string;
  posting: Posting;
  facts: Fact[];
  result: MatchResult;
}

export interface MatchListItem {
  id: string;
  title: string;
  company?: string;
  band: MatchBand;
  createdAt: string;
}

export interface MatchList {
  matches: MatchListItem[];
}

export interface VariantResponse {
  variant: ResumeVariant;
}

export interface CoverResponse {
  cover: CoverNote;
}
