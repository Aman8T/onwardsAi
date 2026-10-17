---
category: Charts
---

A 2 px level line with an optional benchmark tick: the bullet-graph reading, **level bold, benchmark thin**. Use it under a skill name in lists, popovers and tables wherever a 0–100 value is compared with a target.

```jsx
<div style={{ display: 'grid', gap: 'var(--s2)' }}>
  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
    <span>Design systems</span>
    <span className="numeral" style={{ color: 'var(--text-muted)', fontSize: 'var(--fs-1)' }}>87</span>
  </div>
  <ValueLine value={87} target={70} label="Design systems, 87 against a target of 70" />
</div>
```

- It is `display: block; width: 100%`: give it a column or a fixed-width container, and always a label row above or beside it with the numeral in `.numeral`.
- `target` draws the tick at that percent; omit it when there is no benchmark.
- `label` makes it an image with that name; without one it is decorative and the numeral must be in the DOM.
- Never a percentage match or an ATS score: values are skill levels or requirement coverage computed from cited evidence.
