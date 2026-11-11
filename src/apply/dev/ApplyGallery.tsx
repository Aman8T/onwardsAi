/**
 * Dev gallery (`/dev/apply`, development builds only): every auto-apply screen and state on stored fixtures, with no
 * server and no account. Pick a screen in the bar, or link straight to one:
 *
 *   /dev/apply?screen=applications
 *   screens: applications · applications-empty · applications-check · details · details-to-check · details-no-resume ·
 *            first-check · consent · start-offer · start-priced · start-unlimited · start-saved-none ·
 *            start-<reason> for every ApplyBlockReason · the run screens from runGallery.tsx
 *
 * Saving in Your details really updates the page; starting, consenting and pasting an email are canned.
 */
import { useId, useState } from 'react';
import type { ResumeChoice, SaveAnswerRequest, YourDetails } from '../../../shared/apply';
import { Brand } from '../../components/Brand';
import { ApplicationsView } from '../ApplicationsView';
import { Consent } from '../Consent';
import { DetailsView } from '../DetailsView';
import { FirstCheck } from '../FirstCheck';
import { StartView } from '../StartView';
import type { ApplyRoute, Navigate } from '../route';
import {
  APPLICATIONS,
  BLOCK_REASONS,
  CHECK_OFFERED,
  CHECK_OFFERED_DEFAULT,
  DETAILS,
  DETAILS_FIRST_TIME,
  DETAILS_NO_RESUME,
  EMAIL_RESULT,
  EMPLOYER_URL,
  MATCH,
  METRICS,
  NOW,
  STATUS,
  STATUS_PRICED,
  checkFailing,
} from './fixtures';
import { RUN_SCREENS, RunGallery } from './runGallery';
import '../../styles/apply.css';

const OWN_SCREENS: readonly (readonly [string, string])[] = [
  ['applications', 'Applications'],
  ['applications-empty', 'Applications · none yet'],
  ['applications-check', 'Applications · Check this one tab'],
  ['details', 'Your details'],
  ['details-to-check', 'Your details · To check'],
  ['details-no-resume', 'Your details · no resume'],
  ['first-check', 'First check'],
  ['consent', 'Consent'],
  ['start-offer', 'Apply for me · offered'],
  ['start-priced', 'Apply for me · priced, no free left'],
  ['start-unlimited', 'Apply for me · unlimited account'],
  ['start-default', 'Apply for me · default resume named'],
  ['start-saved-none', 'Apply for me · saved resume missing'],
  ...BLOCK_REASONS.map((r) => [`start-${r}`, `Apply for me · ${r.replace(/_/g, ' ')}`] as const),
];

const SCREENS = [...OWN_SCREENS, ...RUN_SCREENS];

function readScreen(): string {
  const s = new URLSearchParams(window.location.search).get('screen');
  return s && SCREENS.some(([id]) => id === s) ? s : 'applications';
}

const pause = () => new Promise<void>((resolve) => window.setTimeout(resolve, 250));

function screenFor(route: ApplyRoute): string {
  switch (route.screen) {
    case 'list':
      return 'applications';
    case 'details':
      return 'details';
    case 'new':
      return 'start-offer';
    case 'one':
      return RUN_SCREENS[0]?.[0] ?? 'applications';
  }
}

export function ApplyGallery() {
  const [screen, setScreen] = useState(readScreen);
  const [details, setDetails] = useState<YourDetails>(DETAILS);
  const [resume, setResume] = useState<ResumeChoice>('tailored');
  const pickId = useId();

  const go = (next: string) => {
    setScreen(next);
    window.history.replaceState(null, '', `/dev/apply?screen=${next}`);
    window.scrollTo({ top: 0 });
  };
  const navigate: Navigate = (route) => go(screenFor(route));

  const saveAnswer = async (body: SaveAnswerRequest) => {
    await pause();
    setDetails((d) => {
      const known = d.answers.find((a) => a.questionKey === body.questionKey);
      const asked = d.notAskedYet.find((q) => q.questionKey === body.questionKey);
      const now = new Date(NOW).toISOString();
      const answers = known
        ? d.answers.map((a) => (a.questionKey === body.questionKey ? { ...a, value: body.value, askEachTime: body.askEachTime, source: 'you' as const, answeredAt: now, stale: false } : a))
        : asked
          ? [...d.answers, { questionKey: asked.questionKey, question: asked.question, group: asked.group, source: 'you' as const, askEachTime: body.askEachTime, answeredAt: now, value: body.value }]
          : d.answers;
      return { ...d, answers, notAskedYet: d.notAskedYet.filter((q) => q.questionKey !== body.questionKey) };
    });
  };

  const start = (status = STATUS, check = CHECK_OFFERED, d: YourDetails = details, unlimited = false) => (
    <StartView
      status={status}
      check={check}
      matchId={MATCH.id}
      match={MATCH}
      details={d}
      resume={resume}
      onResume={setResume}
      onStart={pause}
      navigate={navigate}
      employerUrl={EMPLOYER_URL}
      unlimited={unlimited}
      onSignIn={() => undefined}
    />
  );

  let body;
  if (screen.startsWith('start-') && BLOCK_REASONS.some((r) => `start-${r}` === screen)) {
    body = start(STATUS, checkFailing(screen.slice('start-'.length) as (typeof BLOCK_REASONS)[number]));
  } else {
    switch (screen) {
      case 'applications':
      case 'applications-check':
        body = (
          <ApplicationsView
            key={screen}
            applications={APPLICATIONS}
            navigate={navigate}
            now={NOW}
            onStage={pause}
            onEmail={async () => {
              await pause();
              return EMAIL_RESULT;
            }}
            balance={<span className="chip">7 free left</span>}
            metrics={METRICS}
            initialTab={screen === 'applications-check' ? 'check' : undefined}
          />
        );
        break;
      case 'applications-empty':
        body = <ApplicationsView applications={[]} navigate={navigate} now={NOW} onStage={pause} onEmail={async () => EMAIL_RESULT} />;
        break;
      case 'details':
      case 'details-to-check':
      case 'details-no-resume':
        body = (
          <DetailsView
            key={screen}
            details={screen === 'details-no-resume' ? DETAILS_NO_RESUME : details}
            navigate={navigate}
            now={NOW}
            initialFilter={screen === 'details-to-check' ? 'check' : undefined}
            onSave={saveAnswer}
            onStillTrue={async (key) => {
              await pause();
              setDetails((d) => ({ ...d, answers: d.answers.map((a) => (a.questionKey === key ? { ...a, stale: false } : a)) }));
            }}
            onSelfId={async (on) => {
              await pause();
              setDetails((d) => ({ ...d, selfIdOnForms: on }));
            }}
            onUpload={pause}
            onRemoveResume={pause}
          />
        );
        break;
      case 'first-check':
        body = <FirstCheck details={DETAILS_FIRST_TIME} onSubmit={async () => go('consent')} />;
        break;
      case 'consent':
        body = <Consent onAgree={async () => go('start-offer')} onCancel={() => go('applications')} />;
        break;
      case 'start-offer':
        body = start();
        break;
      case 'start-priced':
        body = start(STATUS_PRICED);
        break;
      case 'start-unlimited':
        body = start(STATUS, CHECK_OFFERED, details, true);
        break;
      case 'start-default':
        body = (
          <StartView
            status={STATUS}
            check={CHECK_OFFERED_DEFAULT}
            matchId={MATCH.id}
            match={MATCH}
            details={details}
            resume="saved"
            onResume={() => undefined}
            onStart={pause}
            navigate={navigate}
            employerUrl={EMPLOYER_URL}
          />
        );
        break;
      case 'start-saved-none':
        body = (
          <StartView
            status={STATUS}
            check={CHECK_OFFERED}
            matchId={MATCH.id}
            match={MATCH}
            details={DETAILS_NO_RESUME}
            resume="saved"
            onResume={() => undefined}
            onStart={pause}
            navigate={navigate}
            employerUrl={EMPLOYER_URL}
          />
        );
        break;
      default:
        body = <RunGallery screen={screen} navigate={navigate} />;
    }
  }

  return (
    <div className="app-view ap-root">
      <header className="ap-header">
        <Brand />
        <span className="kicker">Dev only · auto-apply on fixtures</span>
        <div className="ap-header-end">
          <label className="ap-label" htmlFor={pickId}>
            Screen
          </label>
          <select id={pickId} className="field ap-pick" value={screen} onChange={(e) => go(e.target.value)}>
            {SCREENS.map(([id, label]) => (
              <option key={id} value={id}>
                {label}
              </option>
            ))}
          </select>
        </div>
      </header>
      <main className="ap-page">{body}</main>
    </div>
  );
}
