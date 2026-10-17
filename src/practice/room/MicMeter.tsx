/**
 * The microphone level meter: about 24 thin bars that move only with the person's voice. While `active` it reads the
 * microphone (`startMeter` in `../speech.ts`, which shares the one stream with the recogniser) and writes each bar's
 * `transform` straight to the element every frame, never through React state. Under reduced motion nothing is painted: the bars stay flat (the level is still read, so
 * "We heard you" can be said in words). Stopped, paused or unmounted, the microphone is let go and the bars lie flat.
 *
 * `preview` is for the dev gallery only: fixed bar heights in pixels (4 to 26), drawn once, with no microphone.
 */
import { useReducedMotion } from 'motion/react';
import { useEffect, useRef } from 'react';
import { METER_BARS, startMeter } from '../speech';
import type { MeterTrouble } from '../speech';

/** The tallest bar, in px; the flat bar is 4 px. */
const TALLEST = 26;

export interface MicMeterProps {
  active: boolean;
  /** The microphone could not be opened. */
  onBlocked?: (why: MeterTrouble) => void;
  /** The loudest bar, 0 to 1 (every frame; about seven times a second under reduced motion). */
  onLevel?: (peak: number) => void;
  preview?: readonly number[];
  className?: string;
}

export function MicMeter({ active, onBlocked, onLevel, preview, className }: MicMeterProps) {
  const reduced = useReducedMotion() ?? false;
  const box = useRef<HTMLDivElement>(null);
  const callbacks = useRef({ onBlocked, onLevel });
  callbacks.current = { onBlocked, onLevel };

  useEffect(() => {
    if (!active || preview) return;
    const el = box.current;
    if (!el) return;
    return startMeter({
      bars: Array.from(el.children) as HTMLElement[],
      reduced,
      onLevel: (p) => callbacks.current.onLevel?.(p),
      onBlocked: (why) => callbacks.current.onBlocked?.(why),
    });
  }, [active, preview, reduced]);

  return (
    <div className={`rm-meter${className ? ` ${className}` : ''}`} ref={box} aria-hidden="true">
      {Array.from({ length: METER_BARS }, (_, i) => (
        <i key={i} style={preview ? { transform: `scaleY(${((preview[i % preview.length] ?? 4) / TALLEST).toFixed(3)})` } : undefined} />
      ))}
    </div>
  );
}
