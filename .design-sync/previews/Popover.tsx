import type { CSSProperties } from 'react';
import { Popover } from 'tayyari';
import type { PopoverModel } from 'tayyari';
import { settleMotion } from './_shared';

settleMotion();

const noop = () => {};

/** A domain popover from the Now graph: score, tier, role target, summary, two tagged lines, skill rows. */
const DOMAIN: PopoverModel = {
  key: 'design-motion',
  kicker: 'Skill area',
  title: 'Design & Motion',
  score: 85,
  badge: 'Strong',
  target: 70,
  summary: 'Your clearest strength: motion-kit and Kite UI are both cited by the graph.',
  bullets: [
    { tag: 'Strength', text: 'motion-kit, a spring-physics animation library for React with 212 stars.' },
    { tag: 'Gap', text: 'No shipped Figma-to-code tooling beyond pairing with designers.' },
  ],
  skills: [
    { id: 'motion', name: 'Motion & animation', score: 91 },
    { id: 'design-systems', name: 'Design systems', score: 87 },
    { id: 'figma', name: 'Figma-to-code', score: 74 },
  ],
  foot: { label: 'Source', value: 'Resume, GitHub' },
  cta: 'See the skills',
};

/** A next-role popover: no score line, a summary and one action. */
const ROLE: PopoverModel = {
  key: 'design-engineer',
  kicker: 'Next role',
  title: 'Design Engineer',
  summary: 'Your motion and design-systems work already covers most of what this role asks for.',
  bullets: [
    { tag: 'You have', text: 'Component library ownership, motion, TypeScript.' },
    { tag: 'To build', text: 'LLM API integration shows up in 6 of 10 postings.' },
  ],
  cta: 'Match a posting',
};

/* The popover positions itself in its nearest positioned ancestor; the frames below stand in for the graph. */
const anchored: CSSProperties = { position: 'relative', height: 600, maxWidth: 420 };
const phone: CSSProperties = {
  position: 'relative',
  height: 560,
  maxWidth: 390,
  overflow: 'hidden',
  border: 'var(--hair) solid var(--line)',
  borderRadius: 'var(--r-l)',
  background: 'var(--bg)',
  transform: 'translateZ(0)',
};

/** Anchored card beside a radar vertex: `place` returns the spot once the real height is known. */
export const DomainCard = () => (
  <div style={anchored}>
    <Popover model={DOMAIN} place={() => ({ left: 16, top: 16 })} onClose={noop} onAct={noop} onPickSkill={noop} />
  </div>
);

/** The same shell with no score: a role from the Next view. */
export const RoleCard = () => (
  <div style={{ ...anchored, height: 360 }}>
    <Popover model={ROLE} place={() => ({ left: 16, top: 16 })} onClose={noop} onAct={noop} />
  </div>
);

/** Under 760 px every popover becomes a bottom sheet (`sheet`), with a scrim that dismisses on tap. */
export const BottomSheet = () => (
  <div style={phone}>
    <p className="kicker" style={{ padding: 'var(--s4)' }}>
      Phone viewport
    </p>
    <Popover model={DOMAIN} sheet onClose={noop} onAct={noop} onPickSkill={noop} />
  </div>
);
