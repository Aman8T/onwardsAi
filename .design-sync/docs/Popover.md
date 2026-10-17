---
category: Kit
---

The one popover shell for everything that opens from a chart or a list: an opaque `--surface` card with a hairline border (never a shadow), kicker, title, optional score line (numeral, suffix, tier chip, `ValueLine` with target), summary, tagged lines, a footnote, skill rows and one primary action. Under 760 px the same model renders as a bottom sheet.

```jsx
const model = {
  key: 'design-motion',
  kicker: 'Skill area',
  title: 'Design & Motion',
  score: 85, badge: 'Strong', target: 70,
  summary: 'Your clearest strength: motion-kit and Kite UI are both cited by the graph.',
  bullets: [
    { tag: 'Strength', text: 'motion-kit, a spring-physics animation library for React with 212 stars.' },
    { tag: 'Gap', text: 'No shipped Figma-to-code tooling beyond pairing with designers.' },
  ],
  skills: [{ id: 'motion', name: 'Motion & animation', score: 91 }],
  foot: { label: 'Source', value: 'Resume, GitHub' },
  cta: 'See the skills',
};

const narrow = useMediaQuery(NARROW_QUERY);

{open && (
  <Popover
    model={model}
    sheet={narrow}
    place={(height) => ({ left: vertex.x + 16, top: Math.max(8, vertex.y - height / 2) })}
    autoFocus={viaKeyboard ? 'dialog' : false}
    onClose={close}
    onAct={goToSkills}
    onPickSkill={(id) => select(id)}
  />
)}
```

- The anchored card is `position: absolute` inside the nearest positioned ancestor (the chart wrapper): `place` receives the real height and returns `left`/`top`. `width` is 304 by default.
- `sheet` turns it into a fixed bottom sheet with a tap-to-dismiss scrim; drive it from `useMediaQuery(NARROW_QUERY)` (both exported).
- Escape and outside clicks are the parent's job; the close button calls `onClose`. Wrap it in `AnimatePresence` from `motion/react` to get the exit fade.
- Every sentence in `summary`, `bullets` and `foot` must be grounded text from the evidence; the shell never invents copy.
