---
category: Charts
---

The centrepiece chart: a person's **shape of skills**. One polygon per series over hairline rings at 25 / 50 / 75 / 100, axis labels outside the rim as real buttons, a centre slot, and a visually hidden data table that is the source of truth.

## Use it for

- Skill areas (domains) of one person, 5 to 8 axes, **in a fixed order** that never changes with the values, so the shape becomes recognisable.
- A person against a role: `series.secondary` names the target; the person is the bold fill, the role a light fill under a dashed hairline (benchmark thin, actual bold). Give every axis a `secondary` value.
- One domain's skills (drill-down): change `axisSetKey` and the chart crossfades, pivoting on `pivotId`.

Do not sort axes by value, do not add an "Other" axis (name what fits nowhere in text beside the chart), do not render fewer than three axes (show a list instead), never a third series.

## Composition

```jsx
const areas = [
  { id: 'frontend', label: 'Frontend', value: 81, secondary: 85 },
  { id: 'design-motion', label: 'Design & Motion', value: 85, secondary: 70 },
  { id: 'backend-apis', label: 'Backend & APIs', value: 58, secondary: 45 },
  { id: 'ai-llm', label: 'AI & LLM', value: 36, secondary: 55 },
  { id: 'eng-practice', label: 'Eng Practice', value: 55, secondary: 65 },
  { id: 'product-collab', label: 'Product & Collab', value: 55, secondary: 60 },
];

<div style={{ maxWidth: 480 }}>
  <Radar
    axes={areas}
    axisSetKey="areas"
    series={{ primary: 'You', secondary: 'Design Engineer expects' }}
    selectedId={selected}
    axisState="expanded"
    onSelect={(id, geom) => openPopover(id, geom)}
    onHover={setHovered}
    ariaLabel="Your skill areas against the Design Engineer role"
    tableCaption="Skill areas against the role, 0 to 100"
    centre={<span className="numeral" style={{ fontSize: 'var(--fs-6)' }}>78</span>}
  />
</div>
```

- The chart fills its container up to `--radar-max` (620 px) and stays square. Set `--radar-pad` (52px on phones) when labels need room.
- `onSelect` gives the vertex position (`geom`) so a `Popover` can be placed beside it; use `axisState="expanded"` when it opens a dialog, `"pressed"` for a toggle, omit when axes navigate.
- `dim` with `relatedIds` fades every vertex that is neither selected nor related.
- `reveal` draws the first set from the centre once over 900 ms; the chart honours reduced motion by itself.
- `ariaLabel` and `tableCaption` are required: the hidden table is how the chart is read by assistive tech and by parity tests.
