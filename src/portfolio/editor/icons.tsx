/** Small stroke icons for the editor chrome (the shared set in components/Icons.tsx has no eye, lock or chevrons). */
import type { ReactNode } from 'react';

function Svg({ size = 18, strokeWidth = 1.5, children }: { size?: number; strokeWidth?: number; children: ReactNode }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      {children}
    </svg>
  );
}

export const IconBack = () => (
  <Svg>
    <path d="M19 12H5" />
    <path d="M11 6l-6 6 6 6" />
  </Svg>
);

const EYE = 'M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z M12 9.5a2.5 2.5 0 1 1 0 5 2.5 2.5 0 0 1 0-5z';

export const IconEye = ({ off = false }: { off?: boolean }) => (
  <Svg>
    <path d={off ? `${EYE} M4 4l16 16` : EYE} />
  </Svg>
);

const GLOBE = 'M12 3a9 9 0 1 1 0 18 9 9 0 0 1 0-18z M3 12h18 M12 3c2.4 2.5 3.6 5.5 3.6 9s-1.2 6.5-3.6 9c-2.4-2.5-3.6-5.5-3.6-9S9.6 5.5 12 3z';
const LOCK = 'M7 11h10a1 1 0 0 1 1 1v7a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1v-7a1 1 0 0 1 1-1z M8.5 11V8a3.5 3.5 0 0 1 7 0v3';

export const IconVisibility = ({ isPublic }: { isPublic: boolean }) => (
  <Svg>
    <path d={isPublic ? GLOBE : LOCK} />
  </Svg>
);

export const IconChevron = ({ dir }: { dir: 'up' | 'down' }) => (
  <Svg size={16} strokeWidth={1.8}>
    <path d={dir === 'up' ? 'M6 15l6-6 6 6' : 'M6 9l6 6 6-6'} />
  </Svg>
);
