/**
 * Before you start, wired: asks for the plan (no model is called, so changing the length or what to spend more time on is
 * free), keeps what the person skips, remembers how they chose to answer and hear the interviewer, tests the microphone on
 * request, and starts the interview. Starting is the one call that costs: the questions are phrased once, here.
 *
 *   plan     `POST /api/interview/plan`, for Short and Full together so both cards can say how many questions and minutes
 *   start    `POST /api/interview/sessions` with the slots left in, then `openPractice({ screen: 'room', session })`
 *
 * The microphone test opens the microphone only while it runs (and for at most 30 seconds); nothing is recorded.
 *
 * Who will do the speech is asked of `GET /api/speech/status` (no session yet) only to choose the copy: Sarvam AI's privacy
 * line when the server offers it, the browser's otherwise. The room asks again for its own session when it loads.
 */
import { useEffect, useRef, useState } from 'react';
import type { GraphRoleSource, InterviewFocus, InterviewKind, InterviewLength, InterviewPlan, PlanRequest, RoleSource } from '../../../shared/interview';
import type { Profile } from '../../../shared/types';
import { useMe } from '../../auth/useMe';
import { dayLabel } from '../../discover/prepare/words';
import { useTracker } from '../../discover/applications/tracker';
import { isSampleId } from '../../match/model';
import { actions } from '../../store';
import { PracticeApiError, realApi, sentenceOf } from '../api';
import { fetchSpeechStatus } from '../sarvam';
import { cancelSpeech, chooseSpeech, primeVoice, readPrefs, speak, writePrefs } from '../speech';
import type { MeterTrouble, Prefs, SpeechChoice, VoiceSetting } from '../speech';
import { usePracticeNav } from '../useInterview';
import { GRAPH_ROLE_SUB, KIND_BLURB, KIND_LABEL } from '../words';
import { SetupView } from './SetupView';
import type { PlanProblem } from './SetupView';
import '../../styles/practice-room.css';

export interface SetupScreenProps {
  profile: Profile;
  kind: InterviewKind;
  role?: RoleSource;
  /** For `role`: a role of the person's graph instead of one from a search (ignored when `role` is given). */
  graphRole?: GraphRoleSource;
}

/** The microphone test stops by itself after this long. */
const TEST_MS = 30_000;

/** The setup page does not wait on the speech status for long: the browser's copy stands until it comes. */
const STATUS_WAIT_MS = 3000;

export function SetupScreen(props: SetupScreenProps) {
  // a different kind or role is a fresh page: its own plan, its own skips
  return <SetupBody key={`${props.profile.id}:${props.kind}:${props.role?.runId ?? ''}:${props.role?.jobId ?? ''}:${props.graphRole?.roleId ?? ''}`} {...props} />;
}

function SetupBody({ profile, kind, role, graphRole: asked }: SetupScreenProps) {
  const open = usePracticeNav();
  const tracker = useTracker(profile.id);
  const tracked = role ? tracker.get(role.jobId) : undefined;
  // a graph role is only for `role` practice, and a search's role wins when both are named
  const graphRole = kind === 'role' && !role ? asked : undefined;
  const graph = graphRole ? profile.roles.find((r) => r.id === graphRole.roleId) : undefined;
  const { unlimited } = useMe();

  const [length, setLength] = useState<InterviewLength>('full');
  const [focus, setFocus] = useState<InterviewFocus | undefined>();
  const [skipped, setSkipped] = useState<ReadonlySet<string>>(new Set());
  const [prefs, setPrefs] = useState<Prefs>(readPrefs);
  const [plans, setPlans] = useState<Partial<Record<InterviewLength, InterviewPlan>>>({});
  const [loading, setLoading] = useState(true);
  const [problem, setProblem] = useState<PlanProblem | undefined>();
  const [attempt, setAttempt] = useState(0);
  const [left, setLeft] = useState<number | undefined>();
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState<string | undefined>();
  const [testing, setTesting] = useState(false);
  const [heard, setHeard] = useState(false);
  const [micProblem, setMicProblem] = useState<MeterTrouble | undefined>();

  // who will do the speech, as far as this page knows: the browser's own until the server says Sarvam is on
  const [choice, setChoice] = useState<SpeechChoice>(() => chooseSpeech(null));
  const speechSupported = choice.listen !== 'none';
  const voiceSupported = choice.voice !== 'none';

  useEffect(() => {
    const ctl = new AbortController();
    const timer = window.setTimeout(() => ctl.abort(), STATUS_WAIT_MS);
    fetchSpeechStatus(profile.id, undefined, ctl.signal).then(
      (status) => {
        if (!ctl.signal.aborted) setChoice(chooseSpeech(status));
      },
      () => undefined,
    );
    return () => {
      window.clearTimeout(timer);
      ctl.abort();
    };
  }, [profile.id]);

  // the plan: both lengths at once, so the length cards can say what each one is
  useEffect(() => {
    const ctl = new AbortController();
    setLoading(true);
    const base: PlanRequest = { profileId: profile.id, kind, ...(role ? { role } : {}), ...(graphRole ? { graphRole } : {}), ...(kind === 'role' && focus ? { focus } : {}) };
    const lengths: Array<InterviewLength | undefined> = kind === 'work' ? [undefined] : ['full', 'short'];
    void Promise.allSettled(lengths.map((l) => realApi.fetchPlan({ ...base, ...(l ? { length: l } : {}) }, ctl.signal))).then((results) => {
      if (ctl.signal.aborted) return;
      const next: Partial<Record<InterviewLength, InterviewPlan>> = {};
      let failure: unknown;
      results.forEach((r, i) => {
        const l = lengths[i] ?? 'full';
        if (r.status === 'fulfilled') next[l] = r.value;
        else failure ??= r.reason;
      });
      setPlans(next);
      setLoading(false);
      if (Object.keys(next).length === 0) {
        const status = failure instanceof PracticeApiError ? failure.status : 0;
        setProblem({ message: sentenceOf(failure), noTemplate: kind === 'work' && status === 400, gone: status === 404 });
      } else {
        setProblem(undefined);
      }
    });
    return () => ctl.abort();
  }, [profile.id, kind, role?.runId, role?.jobId, graphRole?.roleId, focus, attempt]);

  // how many practice interviews are left today (free for now); a failure here is not worth a word
  useEffect(() => {
    const ctl = new AbortController();
    realApi.fetchHistory(profile.id, ctl.signal).then(
      (h) => {
        if (!ctl.signal.aborted) setLeft(h.leftToday);
      },
      () => undefined,
    );
    return () => ctl.abort();
  }, [profile.id]);

  // the microphone test lets go by itself, and when the page does
  const testTimer = useRef<number | undefined>(undefined);
  useEffect(() => {
    window.clearTimeout(testTimer.current);
    if (testing) testTimer.current = window.setTimeout(() => setTesting(false), TEST_MS);
    return () => window.clearTimeout(testTimer.current);
  }, [testing]);
  useEffect(() => () => cancelSpeech(), []);

  // the chosen length; if only the other one could be made, that one is better than nothing
  const plan = plans[kind === 'work' ? 'full' : length] ?? plans.full ?? plans.short ?? null;

  const change = (patch: Partial<Prefs>) => {
    const next = { ...prefs, ...patch };
    setPrefs(next);
    writePrefs(next);
  };

  const job = tracked?.job;
  const title = plan?.title || (job ? `${job.title} at ${job.employer}` : graph?.title || KIND_LABEL[kind]);
  const sub = (() => {
    if (graphRole) return GRAPH_ROLE_SUB;
    if (kind !== 'role' && !(kind === 'work' && tracked)) return KIND_BLURB[kind];
    if (!tracked) return kind === 'role' ? 'From a role Search with AI found' : KIND_BLURB[kind];
    const where = 'Apply link stays in Your applications';
    return tracked.stage === 'applied' && tracked.appliedAt ? `From the posting you applied to on ${dayLabel(tracked.appliedAt)} · ${where}` : `From a role you saved · ${where}`;
  })();

  const start = async () => {
    if (!plan || starting) return;
    // a click: the interviewer's first line, in the room, is then allowed to play
    primeVoice();
    setStarting(true);
    setStartError(undefined);
    setTesting(false);
    try {
      const session = await realApi.startSession({
        profileId: profile.id,
        kind,
        ...(role ? { role } : {}),
        ...(graphRole ? { graphRole } : {}),
        ...(kind !== 'work' ? { length } : {}),
        ...(kind === 'role' && focus ? { focus } : {}),
        ...(skipped.size > 0 ? { skip: [...skipped] } : {}),
      });
      open({ screen: 'room', session: session.id });
    } catch (err) {
      setStartError(sentenceOf(err));
      setStarting(false);
    }
  };

  /** A change to the plan's shape: the old skips named slots that are no longer the same. */
  const reshape = () => setSkipped(new Set());

  return (
    <SetupView
      kind={kind}
      sample={isSampleId(profile.id)}
      title={title}
      sub={sub}
      {...(job ? { job } : {})}
      {...(graph ? { graphRole: graph } : {})}
      plan={plan}
      loading={loading}
      {...(problem ? { problem } : {})}
      lengths={plans}
      length={length}
      {...(focus ? { focus } : {})}
      skipped={skipped}
      prefs={prefs}
      speechSupported={speechSupported}
      voiceSupported={voiceSupported}
      listenBy={choice.listen === 'sarvam' ? 'sarvam' : 'browser'}
      mic={{ testing, heard, ...(micProblem ? { problem: micProblem } : {}) }}
      {...(left !== undefined ? { left } : {})}
      unlimited={unlimited}
      starting={starting}
      {...(startError ? { startError } : {})}
      onBack={() => open({ screen: 'hub' })}
      onSkip={(id) =>
        setSkipped((prev) => {
          const next = new Set(prev);
          if (next.has(id)) next.delete(id);
          else next.add(id);
          return next;
        })
      }
      onFocus={(value) => {
        reshape();
        setFocus(value);
      }}
      onLength={(value) => {
        reshape();
        setLength(value);
      }}
      onAnswer={(answer) => {
        if (answer === 'type') setTesting(false);
        change({ answer });
      }}
      onVoice={(voice: VoiceSetting) => {
        change({ voice });
        // a taste of the voice, so the choice is a choice: it only speaks when the person just asked for it. Sarvam's voice reads
        // only the interview's own lines, so there is no taste of it before the interview exists.
        if (voice !== 'off' && choice.voice === 'browser') speak('This is how the interviewer sounds.', { slow: voice === 'slower', onEnd: () => undefined });
        else cancelSpeech();
      }}
      onTestMic={() => {
        setMicProblem(undefined);
        setHeard(false);
        setTesting((on) => !on);
      }}
      onMicLevel={(peak) => {
        if (peak > 0.25) setHeard(true);
      }}
      onMicBlocked={(why) => {
        setTesting(false);
        setMicProblem(why);
      }}
      onStart={() => void start()}
      onRetry={() => setAttempt((n) => n + 1)}
      onOtherKind={() => (role ? open({ screen: 'setup', kind: 'role', run: role.runId, job: role.jobId }) : open({ screen: 'hub' }))}
      onAnalyse={() => actions.goto('onboarding')}
      onAddToProfile={() => actions.goto('onboarding')}
    />
  );
}
