---
category: Portfolio
---

One section of the portfolio page. Every block reads the portfolio from context (`usePf`) and takes only its `block` spec (`id`, `kind`, `variant`, `visible`, `public`) and its `index` among the rendered blocks. Outside `PortfolioPage` a block must sit inside `PfProvider` and a `.pf-scope` element, or it throws "Portfolio blocks must render inside <PortfolioView>".

| Block | Variants |
|---|---|
| `Hero` | `statement`, `split`, `centered`, `index` |
| `Proof` | `strip` |
| `About` | `lede`, `prose`, `facts` |
| `Experience` | `timeline`, `ledger`, `thenNow` |
| `Projects` | `featureList`, `grid`, `list` |
| `Clips` | one list |
| `Achievements` | `numbers`, `list` |
| `Education` | `row`, `compact` |
| `OpenTo` | `note`, `optIn` |
| `Contact` | `signOff`, `links` |
| `Footer` | provenance line, no props |

```jsx
import { Hero, Proof, PfProvider, sampleRiya, scopeClass, styleVars, typeVars, cssVars } from 'tayyari';

const spec = sampleRiya.looks[0];
const { content, graph } = sampleRiya;
const rendered = {};
for (const b of spec.blocks) if (b.visible) rendered[b.kind] = b.id;
const ctx = { spec, content, graph, mode: 'public', uid: 'pf', reduced: false, rendered };

<PfProvider value={ctx}>
  <div className={scopeClass(spec.style)} style={cssVars({ ...styleVars(spec), ...typeVars(spec.style.type, content.person.name) })}>
    <div className="pf-wrap">
      <Hero block={spec.blocks.find((b) => b.kind === 'hero')} index={0} />
      <Proof block={spec.blocks.find((b) => b.kind === 'proof')} index={1} />
    </div>
  </div>
</PfProvider>
```

- Blocks with nothing to show render nothing on the public page (and an empty prompt in the editor), so check the content before relying on one.
- Entrance motion is a 600 ms fade-up staggered by `index`; `reduced: true` in the context turns it off.
- Prefer `PortfolioPage` for a complete page: it computes `rendered`, the nav, the graph band and the footer for you.
