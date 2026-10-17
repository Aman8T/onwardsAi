---
category: Charts
---

A hairline progress ring: a faint track in `--grid` and an accent arc from 12 o'clock. The one circular indicator in the system; it never carries a number by itself.

```jsx
<div style={{ display: 'flex', alignItems: 'center', gap: 'var(--s4)' }}>
  <Ring value={62} role="img" label="Match 62 out of 100" />
  <span><span className="numeral">62</span> <span style={{ color: 'var(--text-muted)' }}>/ 100</span></span>
</div>
```

- `size` 28 by default (44 and 64 are the other sizes used); `stroke` 1.5, at most 2.
- `role="progressbar"` for live progress (announces the value), `role="img"` for a static reading with `label`; omit both when a numeral beside it already says the value (the ring is then decorative).
- At 0 the arc is hidden so no stray dot paints. Values are clamped to 0–100.
