/**
 * The run screens' part of the `/dev/apply` gallery: one application in every stage of spec 3 and every stop reason,
 * from runFixtures.ts, with no server. The gallery's picker lists `RUN_SCREENS` after its own screens:
 *
 *   /dev/apply?screen=run-filling
 *
 * The actions are canned and move the fixture the way the server would: answering queues it again ("2 more forms
 * carried on"), approving sends it to Sending, It went through marks it sent, skipping cancels it.
 */
import { useState } from 'react';
import type { ApplicationDetail, ApplicationStage } from '../../../shared/apply';
import { ApplicationRun } from '../ApplicationScreen';
import type { RunActions } from '../ApplicationScreen';
import type { Navigate } from '../route';
import { EMAIL_RESULT, NOW } from './fixtures';
import {
  RUN_APPROVED,
  RUN_CANCELLED,
  RUN_CHALLENGE,
  RUN_CHECK,
  RUN_CLOSED,
  RUN_CODE,
  RUN_FAILED,
  RUN_FAILED_GUARD,
  RUN_FILLING,
  RUN_FORM_CHANGED,
  RUN_INTERVIEWING,
  RUN_MATCH,
  RUN_QUESTIONS,
  RUN_QUEUE,
  RUN_QUEUED,
  RUN_REVIEW,
  RUN_REVIEW_DEFAULT,
  RUN_REVIEW_NEEDS,
  RUN_REVIEW_ONSITE,
  RUN_REVIEW_SIGN,
  RUN_REVIEW_WARNINGS,
  RUN_SENT,
  RUN_SENT_DEFAULT,
  RUN_SIGNIN,
  RUN_SUBMITTING,
  requeued,
} from './runFixtures';
import '../../styles/apply-run.css';

interface RunFixture {
  id: string;
  label: string;
  detail: ApplicationDetail;
  /** Opened from Filling's finished state rather than straight from the list. */
  watched?: boolean;
}

const RUNS: readonly RunFixture[] = [
  // first: the gallery opens any application link (`{screen: 'one'}`) here
  { id: 'run-review', label: 'Run · review, ready to approve', detail: RUN_REVIEW },
  { id: 'run-queued', label: 'Run · waiting to fill', detail: RUN_QUEUED },
  { id: 'run-filling', label: 'Run · filling', detail: RUN_FILLING },
  { id: 'run-filled', label: 'Run · filled, ready to review', detail: RUN_REVIEW, watched: true },
  { id: 'run-questions', label: 'Run · questions', detail: RUN_QUESTIONS },
  { id: 'run-review-needs', label: 'Run · review, two need you', detail: RUN_REVIEW_NEEDS },
  { id: 'run-review-sign', label: 'Run · review, signature left', detail: RUN_REVIEW_SIGN },
  { id: 'run-review-onsite', label: 'Run · review, ID number on site', detail: RUN_REVIEW_ONSITE },
  { id: 'run-review-default', label: 'Run · review, default resume', detail: RUN_REVIEW_DEFAULT },
  { id: 'run-review-warnings', label: 'Run · review with a warning', detail: RUN_REVIEW_WARNINGS },
  { id: 'run-form-changed', label: 'Run · form changed', detail: RUN_FORM_CHANGED },
  { id: 'run-approved', label: 'Run · approved', detail: RUN_APPROVED },
  { id: 'run-submitting', label: 'Run · sending', detail: RUN_SUBMITTING },
  { id: 'run-signin', label: 'Run · needs sign-in', detail: RUN_SIGNIN },
  { id: 'run-challenge', label: 'Run · a check for you', detail: RUN_CHALLENGE },
  { id: 'run-code', label: 'Run · emailed code', detail: RUN_CODE },
  { id: 'run-check', label: 'Run · check this one', detail: RUN_CHECK },
  { id: 'run-failed', label: "Run · didn't go, refunded", detail: RUN_FAILED },
  { id: 'run-failed-guard', label: 'Run · safety check failed', detail: RUN_FAILED_GUARD },
  { id: 'run-closed', label: 'Run · posting closed', detail: RUN_CLOSED },
  { id: 'run-cancelled', label: 'Run · skipped', detail: RUN_CANCELLED },
  { id: 'run-sent', label: 'Run · sent', detail: RUN_SENT },
  { id: 'run-sent-default', label: 'Run · sent with the default resume', detail: RUN_SENT_DEFAULT },
  { id: 'run-interviewing', label: 'Run · interviewing', detail: RUN_INTERVIEWING },
];

export const RUN_SCREENS: readonly (readonly [string, string])[] = RUNS.map((r) => [r.id, r.label] as const);

const pause = () => new Promise<void>((resolve) => window.setTimeout(resolve, 250));
const stamp = () => new Date(NOW).toISOString();

function GalleryRun({ fixture, navigate }: { fixture: RunFixture; navigate: Navigate }) {
  const [detail, setDetail] = useState<ApplicationDetail>(fixture.detail);
  const move = async (stage: ApplicationStage, over: Partial<ApplicationDetail> = {}): Promise<ApplicationDetail> => {
    await pause();
    return { ...detail, stage, stopReason: undefined, updatedAt: stamp(), ...over };
  };
  const actions: RunActions = {
    answer: async () => {
      await pause();
      return { application: requeued(detail), requeued: 2 };
    },
    approve: () => move('approved', { approvedAt: stamp() }),
    cancel: () => move('cancelled', { creditState: 'released' }),
    resolve: (o) =>
      o === 'sent'
        ? move('sent', { creditState: 'charged' })
        : move('waiting_for_you', {
            creditState: 'released',
            events: [...detail.events, { at: stamp(), kind: 'stage', source: 'user', fromStage: 'check_this_one', toStage: 'waiting_for_you', note: "It didn't go through" }],
          }),
    appliedThere: () => move('sent', { submittedAt: stamp() }),
    fillAgain: () => move('queued', { creditState: 'held' }),
    stage: (s) => move(s),
    email: async () => {
      await pause();
      return EMAIL_RESULT;
    },
  };
  return <ApplicationRun detail={detail} onDetail={setDetail} navigate={navigate} actions={actions} queue={RUN_QUEUE} match={RUN_MATCH} initialWatched={fixture.watched} />;
}

export function RunGallery({ screen, navigate }: { screen: string; navigate: Navigate }) {
  const fixture = RUNS.find((r) => r.id === screen);
  if (!fixture) return null;
  return <GalleryRun key={fixture.id} fixture={fixture} navigate={navigate} />;
}
