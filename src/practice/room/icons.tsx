/** Stroke icons for the practice room: 16 px, 1.5 px stroke, `currentColor`, decorative (the button carries the label). */
import type { SVGProps } from 'react';

type IconProps = Omit<SVGProps<SVGSVGElement>, 'viewBox' | 'fill' | 'stroke'> & { size?: number };

function Icon({ size = 16, children, ...rest }: IconProps) {
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

export const MicIcon = (p: IconProps) => (
  <Icon {...p}>
    <path d="M8 1.5a2 2 0 0 0-2 2v4a2 2 0 0 0 4 0v-4a2 2 0 0 0-2-2z" />
    <path d="M4 7.5a4 4 0 0 0 8 0M8 11.5v3" />
  </Icon>
);

export const MicOffIcon = (p: IconProps) => (
  <Icon {...p}>
    <path d="M8 1.5a2 2 0 0 0-2 2v4a2 2 0 0 0 4 0v-4a2 2 0 0 0-2-2z" />
    <path d="M4 7.5a4 4 0 0 0 8 0M8 11.5v3" />
    <path d="M2 2l12 12" />
  </Icon>
);

export const SpeakerIcon = (p: IconProps) => (
  <Icon {...p}>
    <path d="M2.5 6.2h2.3L8 3.5v9L4.8 9.8H2.5z" />
    <path d="M10.5 6a3 3 0 0 1 0 4M12.3 4.2a5.5 5.5 0 0 1 0 7.6" />
  </Icon>
);

export const SpeakerOffIcon = (p: IconProps) => (
  <Icon {...p}>
    <path d="M2.5 6.2h2.3L8 3.5v9L4.8 9.8H2.5z" />
    <path d="M10.5 6.5l3 3M13.5 6.5l-3 3" />
  </Icon>
);

export const PauseIcon = (p: IconProps) => (
  <Icon {...p}>
    <path d="M5.5 3v10" />
    <path d="M10.5 3v10" />
  </Icon>
);

export const PlayIcon = (p: IconProps) => (
  <Icon {...p}>
    <path d="M5 3l8 5-8 5z" />
  </Icon>
);

export const KeyboardIcon = (p: IconProps) => (
  <Icon {...p}>
    <rect x="1.5" y="4" width="13" height="8" rx="1.5" />
    <path d="M4.5 7h.01M7 7h.01M9 7h.01M11.5 7h.01M5 9.5h6" />
  </Icon>
);

export const RepeatIcon = (p: IconProps) => (
  <Icon {...p}>
    <path d="M3 7.5a5 5 0 0 1 9-2.8M13 2.5v2.5h-2.5" />
    <path d="M13 8.5a5 5 0 0 1-9 2.8M3 13.5V11h2.5" />
  </Icon>
);

export const ChevronIcon = (p: IconProps) => (
  <Icon {...p}>
    <path d="M4 6l4 4 4-4" />
  </Icon>
);

export const SendIcon = (p: IconProps) => (
  <Icon {...p}>
    <path d="M2 8h11M9 4l4 4-4 4" />
  </Icon>
);

export const FileIcon = (p: IconProps) => (
  <Icon {...p}>
    <path d="M3.5 1.5h6l3 3v10h-9z" />
    <path d="M9.5 1.5v3h3" />
  </Icon>
);
