/** The small stroke icons the Discover screens use (drawn for the design canvas, 16 px, no icon font). */
import type { SVGProps } from 'react';

type P = SVGProps<SVGSVGElement> & { size?: number };

function Svg({ size = 16, children, ...rest }: P) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      {...rest}
    >
      {children}
    </svg>
  );
}

/** A bookmark; filled when the role is saved. */
export const IconBookmark = ({ filled, ...p }: P & { filled?: boolean }) => (
  <Svg {...p} fill={filled ? 'currentColor' : 'none'}>
    <path d="M4 2h8v12l-4-3-4 3z" />
  </Svg>
);
export const IconCompare = (p: P) => (
  <Svg {...p}>
    <path d="M5 2v12M11 2v12M2 5h6M8 11h6" />
  </Svg>
);
export const IconClose = (p: P) => (
  <Svg {...p}>
    <path d="M4 4l8 8M12 4l-8 8" />
  </Svg>
);
export const IconSearch = (p: P) => (
  <Svg {...p}>
    <circle cx="7" cy="7" r="4.5" />
    <path d="M10.5 10.5L14 14" />
  </Svg>
);
export const IconStop = (p: P) => (
  <Svg {...p}>
    <rect x="3.5" y="3.5" width="9" height="9" rx="1.5" />
  </Svg>
);
export const IconList = (p: P) => (
  <Svg {...p}>
    <path d="M3 4h10M3 8h10M3 12h10" />
  </Svg>
);
export const IconDeck = (p: P) => (
  <Svg {...p}>
    <rect x="3" y="3.5" width="10" height="9" rx="1.5" />
    <path d="M5 1.5h6" />
  </Svg>
);
export const IconUp = (p: P) => (
  <Svg {...p}>
    <path d="M3.5 10.5L8 6l4.5 4.5" />
  </Svg>
);
export const IconCheck = (p: P) => (
  <Svg {...p}>
    <path d="M3.5 8.5l3 3 6-7" />
  </Svg>
);
export const IconChevron = (p: P) => (
  <Svg {...p}>
    <path d="M6 3.5l4.5 4.5L6 12.5" />
  </Svg>
);
export const IconPrepare = (p: P) => (
  <Svg {...p}>
    <path d="M4 2.5h8v11H4z" />
    <path d="M6 6h4M6 8.5h4M6 11h2" />
  </Svg>
);
export const IconAlert = (p: P) => (
  <Svg {...p}>
    <circle cx="8" cy="8" r="6" />
    <path d="M8 5v3.5M8 11h.01" />
  </Svg>
);
