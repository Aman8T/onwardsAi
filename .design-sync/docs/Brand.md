---
category: Kit
---

The Tayyari wordmark: a hairline ring with a centre dot in `--accent-ui`, then the name in the display serif. Top-left of every screen; with `onClick` it is the home button.

```jsx
<header style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
  <Brand tagline onClick={goHome} />
  <Atmosphere />
</header>
```

- `tagline` adds the "Career intelligence" kicker behind a hairline divider.
- `size` is a scale reference (28 default: 22 px word, 20 px mark); 22 for dense bars, 40 for a landing hero.
- It is the one serif line of the header; do not add another serif headline beside it on the same screen.
