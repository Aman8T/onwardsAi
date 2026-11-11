/** One rotating line under the heading while the run works. Board: Thinking. Opacity transition only, no loop. */
import { useEffect, useMemo, useRef, useState } from 'react';
import { createPicker, FADE_MS, LABEL, ROTATE_MS, type Picked, type ThinkingValues } from './thinking';
import { THINKING_LINES, type ThinkingPhase } from './thinkingLines';

export function ThinkingLine({
  phase,
  vals,
  reduce,
  fallback,
  onShown,
}: {
  phase: ThinkingPhase;
  vals: ThinkingValues;
  reduce: boolean;
  /** Today's sentence, shown unchanged when the lines list gives nothing. */
  fallback: string;
  /** Called with the time each line becomes visible. */
  onShown?: (at: number) => void;
}) {
  const picker = useMemo(() => createPicker(THINKING_LINES), []);
  const latest = useRef({ phase, vals, reduce });
  latest.current = { phase, vals, reduce };
  const shown = useRef(onShown);
  shown.current = onShown;
  const [line, setLine] = useState<Picked | null>(null);
  const [out, setOut] = useState(false);
  const hasLine = useRef(false);
  const fade = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  const swap = () => {
    const next = picker.next(latest.current.phase, latest.current.vals);
    if (!next) return;
    const show = () => {
      hasLine.current = true;
      setLine(next);
      setOut(false);
      shown.current?.(Date.now());
    };
    clearTimeout(fade.current);
    if (latest.current.reduce || !hasLine.current) {
      show();
      return;
    }
    setOut(true);
    fade.current = setTimeout(show, FADE_MS);
  };
  const swapRef = useRef(swap);
  swapRef.current = swap;

  // the real step changed (or the first one began): the next line is about it, now
  useEffect(() => {
    swapRef.current();
  }, [phase]);

  useEffect(() => {
    const id = setInterval(() => swapRef.current(), ROTATE_MS);
    return () => {
      clearInterval(id);
      clearTimeout(fade.current);
    };
  }, []);

  if (!line) return <p className="an-sub">{fallback}</p>;
  return (
    <p className="an-sub an-think" aria-hidden="true">
      <span className="kicker an-think-type">{LABEL[line.type]}</span>
      <span className={`an-think-text${out ? ' is-out' : ''}${reduce ? ' is-still' : ''}`}>{line.text}</span>
    </p>
  );
}
