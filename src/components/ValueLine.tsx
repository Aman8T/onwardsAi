import { clamp } from '../lib/ui';
import '../styles/kit.css';

export interface ValueLineProps {
  /** Level, 0 to 100. */
  value: number;
  /** Optional benchmark, 0 to 100, drawn as a thin tick across the track. */
  target?: number;
  className?: string;
  /** When set the line is announced as an image with this name; otherwise it is decorative. */
  label?: string;
}

/** A 2px level line with an optional target tick (the bullet-graph reading: level bold, benchmark thin). */
export function ValueLine({ value, target, className, label }: ValueLineProps) {
  return (
    <span
      className={className ? `vl ${className}` : 'vl'}
      {...(label ? { role: 'img', 'aria-label': label } : { 'aria-hidden': true })}
    >
      <span className="vl-bar" style={{ transform: `scaleX(${clamp(value, 0, 100) / 100})` }} />
      {target !== undefined && <span className="vl-tick" style={{ left: `${clamp(target, 0, 100)}%` }} />}
    </span>
  );
}
