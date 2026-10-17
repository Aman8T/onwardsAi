---
category: Kit
---

The Drift / Still / Off control for the living gradient: a quiet menu button ("Atmosphere · Drifting") with three radio items. It reads and writes the persisted mode itself; place it at the right end of the header and pass nothing but an optional `className`.

```jsx
<Atmosphere />
```

- Drift lets the hue travel one circle every 42 minutes; Still holds one hue; Off collapses chroma to neutral greys (`--k: 0`). Reduced motion forces Still and the label reads "Still · system".
- The menu is a `.panel`-like opaque surface with a hairline; arrows move, Escape closes.
- The gradient itself is the `.ambient` layer (see the README conventions), not this control.
