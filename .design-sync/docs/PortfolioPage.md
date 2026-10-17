---
category: Portfolio
---

The whole Resume → Portfolio page from a portfolio record: `spec` (composition, style, block order and variants), `content` (person, facts, slots, stats) and `graph` (the skill snapshot). It renders the standard skin or, with `skin="press"`, the Old Newspaper skin, and places the skill graph itself. This is the component to use for anything that shows a person's portfolio; the individual blocks exist for layouts that need one section on its own.

```jsx
import { PortfolioPage, sampleRiya } from 'tayyari';

const { content, graph, looks } = sampleRiya;   // Riya Kapoor, the sample persona, with her three stored looks

<PortfolioPage spec={looks[0]} content={content} graph={graph} mode="public" skin="standard" />
<PortfolioPage spec={looks[0]} content={content} graph={graph} mode="public" skin="press" onToggleSkin={toggle} />
```

- `mode="public"` leaves out blocks with nothing to show; `"owner"` keeps them. `editing` adds the editor's selection and slot hooks.
- The page sets its own palette from `spec.style.family` (Iris, Glacier, Lagoon, Sage, Dune, Clay, Rose, Orchid) inside a `.pf-scope` wrapper, so it never follows the app's ambient hue. Type pairing, corners and density come from `spec.style` too.
- Compositions: `signature`, `narrative`, `builder`, `foundations`, `ledger`, `pivot`. Graph variants are chosen by code from the data (peaks, halo, ledger, stellar, atlas, spotlight, focus).
- Never fabricate `content`: facts carry `sourceRefs`, slots cite `factIds`, and every number is passed through untouched. For mock-ups use `sampleRiya`.
