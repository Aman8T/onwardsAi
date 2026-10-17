# design-sync notes for Tayyari

Repo-specific facts a re-sync needs. The config is `.design-sync/config.json`; the Claude Design project is
https://claude.ai/design/p/b24d61ea-deb6-4a70-86fe-1d4e55964d71 (created 2026-10-17).

## Shape and entry

- Tayyari is a Vite **app**, not a component library: no `dist/`, no published `.d.ts`. The converter runs the
  package shape against a hand-written entry, `.design-sync/ds-entry.ts`, which re-exports the shipped components
  from `src/` (never a copy). Add a component there **and** in `componentSrcMap`; the entry alone only puts it on
  `window.Tayyari`.
- Never point `srcDir` at `src/`: the synth-entry fallback would `export *` from every `.tsx` (App, views, `main.tsx`
  with its `createRoot` side effect) and duplicate names (`ValueLine` exists in `components/` and `portfolio/graph/parts.tsx`)
  would be dropped silently.
- Prop contracts come from `.d.ts` files emitted by `node .design-sync/emit-types.mjs` (`cfg.buildCmd`) into `ds-types/`
  (gitignored). Run it before every converter run. The converter's `**/*.d.ts` glob skips dot-directories, which is
  why the output lives in `ds-types/` and why the script moves the entry's own declaration up out of
  `ds-types/.design-sync/`. Components whose props are not named `<Name>Props` in source (icons, blocks, Atmosphere,
  PortfolioPage) get a `<Name>Props` alias in `ds-entry.ts`; that is where the converter finds them.
- `.design-sync/ds-fonts.css` is imported first by the entry so the Google Fonts `@import` for Geist, Geist Mono and
  Instrument Serif is hoisted to the top of `_ds_bundle.css`; validate reports it as `[FONT_REMOTE]`. The portfolio's
  other type pairings load their own Google sheets at runtime (`src/portfolio/kit/fonts.ts`), hence `runtimeFontPrefixes`.
- `tokens.css` and `global.css` are imported by the entry (as `main.tsx` does), so tokens and primitives (`.btn`,
  `.panel`, `.chip`, `.field`, `.kicker`, `.serif`, `.numeral`) ship inside `_ds_bundle.css`; there is no `tokens/` dir.

## Environment

- Converter deps live in `.ds-sync/` (gitignored): `esbuild`, `ts-morph`, `@types/react`, `playwright@1.63.0`. The
  machine's cached Chromium is build 1243 (`%LOCALAPPDATA%\ms-playwright\chromium-1243`), which only Playwright 1.63.x
  pins; a newer playwright would try to download another browser.
- `npm ci` warns `EBADENGINE` on Node 21.7.1 for some converter deps; harmless.
- Git Bash on this machine choked on a multi-file heredoc batch (`unexpected EOF`); write files with the Write tool.
- Another chat's dev server may be running in this folder; the sync never needs it.

## Previews

- `.design-sync/previews/_shared.tsx` holds the helpers (`PfFrame`, `PfBlock`, `IconSheet`, `IconButtons`); the converter
  compiles only `<ComponentName>.tsx`, so it is not a card. Blocks render through `PfBlock`, which builds the
  portfolio context from `sampleRiya` (exported from the entry; it is `src/portfolio/dev/samples.ts`) with
  `reduced: true` so the capture never catches the entrance fade.
- `Clips` has no preview: the sample persona has no clip facts and the block renders nothing on the public page
  without them. Floor card by design.
- `Atmosphere` reads the ambient mode from localStorage; the preview shows the closed button only (the menu is state).
- `settleMotion()` in `_shared.tsx`, called by the Popover and PortfolioPage previews: the capture harness freezes
  the page clock (`page.clock.setFixedTime`), so motion/react tweens that start at opacity 0 never advance and the
  element stays invisible in the per-story capture (validate's render check, without the frozen clock, showed them
  fine). The helper pins `.pop` and `.pf-block` at their settled state with an `!important` style. Blocks previewed
  through `PfBlock` need nothing: their context has `reduced: true`.
- Card modes: `column` for the wide portfolio pieces, Radar and Atmosphere; Popover is `single` with `DomainCard` as
  the shown story because the bottom-sheet story is `position: fixed` and cannot sit in a grid (`[GRID_OVERFLOW]`).
- Docs: `.design-sync/docs/*.md` are the `.prompt.md` bodies. Core components are discovered by name; the eight
  icons share `Icons.md` and the eleven blocks share `PortfolioBlocks.md` through `docsMap`. Frontmatter
  `category:` sets the card groups Charts, Kit, Icons; blocks keep the `blocks` group from their source path.

## Known render warns

- `[FONT_REMOTE] "Instrument Serif", "Geist Mono", "Old English Text MT", "Geist"`: the Google Fonts import is
  the intended delivery; "Old English Text MT" is a local fallback named by the newspaper skin's font stack.
- `[DTS_STYLE_SYSTEM] filtering @types/react props`: the icon props extend `SVGProps`; the filtered shorthands are
  not real API.

## First sync (2026-10-17)

- 27 components uploaded (143 files); 26 with authored previews graded good, `Clips` on the floor card. Validate
  exited 0 with the warns above; the driver verdict was clean (`learningsUnmerged: []`, nothing pending grade).

## Re-sync risks

- `sampleRiya` is inlined into previews and docs through the entry export; if `src/portfolio/dev/samples.ts` changes
  shape, the block previews and `PortfolioPage` cells change with it (grades follow the sources, so they re-verify).
- The entry re-exports are hand-maintained: a new component under `src/components` or a new block is invisible to
  the sync until added to `ds-entry.ts` and `componentSrcMap`.
- `emit-types.mjs` shells out to `npx tsc` (TypeScript from the repo's devDependencies); a TypeScript version with a
  different `.d.ts` emit could change the extracted prop bodies.
- Fonts come from Google at runtime; offline the cards render in fallback faces and grading will look wrong.
