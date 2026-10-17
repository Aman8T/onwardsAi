---
category: Charts
---

A 20 px sparkline of the same shape the `Radar` draws: one ring and one polygon, decorative (`aria-hidden`). Put it beside a name in a rail row, a list, or a comparison strip so the person's shape is recognisable at a glance.

```jsx
<li style={{ display: 'flex', alignItems: 'center', gap: 'var(--s3)' }}>
  <RadarGlyph values={[81, 85, 58, 36, 55, 55]} />
  <span>Riya Kapoor</span>
</li>
```

- `values` are 0–100 in the chart's fixed axis order; fewer than three values draws a plain ring.
- `size` scales it (20 default; 32–64 for headers). Colours come from the tokens, so it follows the ambient hue.
- Never the only carrier of a number: pair it with the numeral or the full chart.
