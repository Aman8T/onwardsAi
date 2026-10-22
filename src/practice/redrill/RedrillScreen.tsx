/**
 * Re-drill what wobbled, wired: the claims come from `GET /api/interview/redrill` (no model), starting is
 * `POST /api/interview/sessions` with `kind: 'redrill'` and the picked claim keys (the one call that costs: the questions
 * are phrased once), then the room. How the person answers is the same preference the setup screen keeps. Ticking and
 * taking out a kept Fix step change only the step. The screen itself is `RedrillView`.
 */
import { useState } from 'react';
import type { Profile } from '../../../shared/types';
import { useMe } from '../../auth/useMe';
import { isSampleId } from '../../match/model';
import { fetchRedrill, realApi, removeFix, sentenceOf, tickFix } from '../api';
import { liveNav } from '../hub/nav';
import { useLoad } from '../hub/useLoad';
import { primeVoice, readPrefs, writePrefs } from '../speech';
import type { Prefs } from '../speech';
import { usePracticeNav } from '../useInterview';
import { RedrillView } from './RedrillView';

export function RedrillScreen({ profile }: { profile: Profile }) {
  const open = usePracticeNav();
  const { unlimited } = useMe();
  const { load, reload, patch } = useLoad((signal) => fetchRedrill(profile.id, signal), [profile.id]);
  const [prefs, setPrefs] = useState<Prefs>(readPrefs);
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState<string | undefined>();

  const start = async (keys: string[]) => {
    if (starting || keys.length === 0) return;
    // a click: the interviewer's first line, in the room, is then allowed to play
    primeVoice();
    setStarting(true);
    setStartError(undefined);
    try {
      const session = await realApi.startSession({ profileId: profile.id, kind: 'redrill', claims: keys });
      open({ screen: 'room', session: session.id });
    } catch (err) {
      setStartError(sentenceOf(err));
      setStarting(false);
    }
  };

  return (
    <RedrillView
      data={load}
      sample={isSampleId(profile.id)}
      unlimited={unlimited}
      answer={prefs.answer}
      starting={starting}
      {...(startError ? { startError } : {})}
      onAnswer={(answer) => {
        const next = { ...prefs, answer };
        setPrefs(next);
        writePrefs(next);
      }}
      onStart={(keys) => void start(keys)}
      onTick={async (task, done) => {
        const saved = await tickFix(task.id, { profileId: profile.id, done });
        patch((v) => ({
          ...v,
          kept: v.kept.map((t) => (t.id === saved.id ? saved : t)),
          claims: v.claims.map((c) => (c.fix?.id === saved.id ? { ...c, fix: saved } : c)),
        }));
      }}
      onRemove={async (task) => {
        await removeFix(task.id, profile.id);
        patch((v) => ({
          ...v,
          kept: v.kept.filter((t) => t.id !== task.id),
          claims: v.claims.map((c) => {
            if (c.fix?.id !== task.id) return c;
            const { fix: _gone, ...rest } = c;
            return rest;
          }),
        }));
      }}
      onReload={reload}
      nav={liveNav}
    />
  );
}
