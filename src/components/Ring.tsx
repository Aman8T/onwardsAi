import { clamp } from '../lib/ui';
import '../styles/kit.css';

export interface RingProps {
  /** Progress, 0 to 100. */
  value: number;
  /** Outer diameter in px. */
  size?: number;
  /** Stroke width in px. */
  stroke?: number;
  className?: string;
  /** Accessible name; used when `role` is set. */
  label?: string;
  /** `progressbar` for live progress, `img` for a static reading; omitted, the ring is decorative. */
  role?: 'progressbar' | 'img';
}

/** A hairline progress ring: a faint track and an accent arc that starts at 12 o'clock. */
export function Ring({ value, size = 28, stroke = 1.5, className, label, role }: RingProps) {
  const progress = clamp(value, 0, 100);
  const centre = size / 2;
  const radius = (size - stroke) / 2;
  const circumference = 2 * Math.PI * radius;

  const a11y =
    role === 'progressbar'
      ? { role, 'aria-label': label, 'aria-valuemin': 0, 'aria-valuemax': 100, 'aria-valuenow': Math.round(progress) }
      : role === 'img'
        ? { role, 'aria-label': label }
        : { 'aria-hidden': true };

  return (
    <svg
      className={className ? `ring ${className}` : 'ring'}
      width={size}
      height={size}
      viewBox={`0 0 ${size} ${size}`}
      focusable="false"
      data-empty={progress <= 0 ? '' : undefined}
      {...a11y}
    >
      <circle className="ring-track" cx={centre} cy={centre} r={radius} strokeWidth={stroke} />
      <circle
        className="ring-bar"
        cx={centre}
        cy={centre}
        r={radius}
        strokeWidth={stroke}
        transform={`rotate(-90 ${centre} ${centre})`}
        style={{ strokeDasharray: circumference, strokeDashoffset: circumference * (1 - progress / 100) }}
      />
    </svg>
  );
}
