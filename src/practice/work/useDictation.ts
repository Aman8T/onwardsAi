/**
 * "Talk it through instead" in the work sample: the same speech-to-text as the room (Sarvam AI when the interview has it,
 * the browser's otherwise, with the same fallback), adding what is said to a text box the person can fix afterwards. Only
 * the text is kept: nothing is kept but what ends up in the box.
 */
import { useEffect, useRef, useState } from 'react';
import { createListener } from '../speech';
import type { Listener, SpeechPlan } from '../speech';
import type { SpeechBy } from '../words';

export type DictationIssue = 'blocked' | 'network' | 'unsupported';

export interface Dictation {
  on: boolean;
  /** The few words still settling. */
  interim: string;
  issue?: DictationIssue;
  /** Who writes the words down. */
  by: SpeechBy;
  /** A piece of what was said is being written down (Sarvam); its words land in the box when it is. */
  writing: boolean;
  /** Why the speech changed hands, or that a piece could not be made out: said once, calmly. */
  note?: string;
  start(): void;
  /** Stop listening. Resolves once the last words have landed in the box. */
  stop(): Promise<void>;
  /** Seconds spent listening, read on a timer. */
  clock(): number;
  /** The microphone could not be opened by the level meter. */
  blocked(): void;
}

export function useDictation(onText: (words: string) => void, plan: SpeechPlan): Dictation {
  const [on, setOn] = useState(false);
  const [interim, setInterim] = useState('');
  const [writing, setWriting] = useState(false);
  const [note, setNote] = useState<string | undefined>();
  const [issue, setIssue] = useState<DictationIssue | undefined>();
  // Sarvam given up on while dictating: from then on the browser's recogniser (or none) is what listens
  const [fellTo, setFellTo] = useState<'browser' | 'none' | null>(null);
  const rec = useRef<Listener | null>(null);
  const latest = useRef(onText);
  latest.current = onText;
  const planRef = useRef(plan);
  planRef.current = plan;
  const rest = useRef('');
  const time = useRef({ ms: 0, since: null as number | null });

  const stopClock = () => {
    const t = time.current;
    if (t.since !== null) {
      t.ms += Date.now() - t.since;
      t.since = null;
    }
  };

  const flush = () => {
    const words = rest.current.trim();
    rest.current = '';
    setInterim('');
    if (words) latest.current(words);
  };

  const dictation: Dictation = {
    on,
    interim,
    ...(issue ? { issue } : {}),
    by: fellTo === null && plan.listen === 'sarvam' ? 'sarvam' : 'browser',
    writing,
    ...(note ? { note } : {}),
    start() {
      if (planRef.current.listen === 'none' || fellTo === 'none') {
        setIssue('unsupported');
        return;
      }
      setIssue(undefined);
      setNote(undefined);
      rec.current ??= createListener(planRef.current, {
        onFinal: (words) => {
          rest.current = '';
          setInterim('');
          latest.current(words);
        },
        onInterim: (words) => {
          rest.current = words;
          setInterim(words);
        },
        onWriting: setWriting,
        onPieceTrouble: setNote,
        onFallback: (why, to) => {
          setNote(why);
          setFellTo(to);
          if (to === 'none') {
            stopClock();
            flush();
            setOn(false);
            setIssue('unsupported');
          }
        },
        onIssue: (what, fatal) => {
          if (!fatal) return;
          stopClock();
          flush();
          setOn(false);
          setIssue(what === 'blocked' ? 'blocked' : what === 'unsupported' ? 'unsupported' : 'network');
        },
      });
      time.current = { ms: 0, since: Date.now() };
      setOn(true);
      rec.current?.start();
    },
    stop() {
      stopClock();
      setOn(false);
      setIssue(undefined);
      return rec.current ? rec.current.stop().then(flush) : Promise.resolve();
    },
    clock: () => Math.floor((time.current.ms + (time.current.since !== null ? Date.now() - time.current.since : 0)) / 1000),
    blocked() {
      stopClock();
      rec.current?.abort();
      setOn(false);
      setWriting(false);
      setIssue('blocked');
    },
  };

  useEffect(
    () => () => {
      rec.current?.abort();
    },
    [],
  );

  return dictation;
}
