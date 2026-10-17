---
category: Icons
---

The stroke icons: 24-unit boxes drawn with `currentColor`, 1.7 stroke, round caps. Eight exist and they are the only icons in the system: `IconDoc`, `IconCheck`, `IconArrowRight`, `IconPlus`, `IconMinus`, `IconStop`, `IconRefresh`, `IconUpload`. No icon font, no third-party set.

```jsx
<button type="button" className="btn btn-primary">
  <IconUpload size={16} /> Upload resume
</button>

<button type="button" className="btn btn-quiet btn-sm" aria-label="Remove link">
  <IconMinus size={16} />
</button>
```

- `size` sets width and height (18 default; 16 inside buttons, 24–32 in empty states). Colour comes from the surrounding text colour: set `color: var(--accent-ui)` or `var(--text-muted)` on the parent.
- Every icon is `aria-hidden`; an icon-only control needs an `aria-label`.
- Need a shape that does not exist? Write the word instead of inventing an icon.
