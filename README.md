# Tayyari — career intelligence (POC)

Connect a resume, GitHub, Lovable apps and any public link. Claude reads every source, builds an
interactive **skill graph of your current role**, flips it to a **map of the roles you are closest to
next**, and turns every node into a conversation: *"Help me prepare for this topic"*, *"Help me improve
my GitHub"*, *"How do I prepare for this role?"*

Design canvas (all screens, both explored directions): https://claude.ai/artifact/HLxXL14nAUeNHhLFNhozp9

## Run it

Requirements: Node 20+ (tested on 21.7), and **Claude Code signed in on this machine** — every AI call
goes through the Claude Agent SDK, which uses Claude Code's credentials.

```bash
npm install
claude auth status        # if not signed in or expired: claude auth login
npm run smoke             # optional: proves Haiku + Sonnet + session resume work (~15 s, < $0.02)
```

**Demo (one process, recommended for pitching):**

```bash
npm run demo              # builds the web app, then serves app + API on http://localhost:8787
```

**Development (hot reload):**

```bash
npm run dev               # API on :8787 (tsx watch) + Vite on :5173 with /api proxied
```

If the combined command misbehaves on your machine, run `npm run dev:api` and `npm run dev:web` in two
terminals.

Click **Try a sample profile** for a live run on the bundled fictional persona (Riya Kapoor), or connect
your own resume and links. A full analysis takes ~60–90 s. If Claude is unavailable, **Open the
pre-computed sample instantly** loads `server/sample/sample-profile.json` with no AI call.

## How it works

```
 resume / GitHub / Lovable / links / pasted notes
        │  deterministic ingest (server/ingest): PDF/DOCX text, GitHub REST (repos, READMEs, tests, CI),
        │  HTML + SPA-bundle reading for Lovable/Vite apps, link classification, private-IP guard
        ▼
 Haiku 4.5 readers — one per source, in parallel (thinking off; structured JSON via outputFormat)
        ▼
 Sonnet 5.5 synthesizer — calibrated skill graph: person, mastery, 4–7 domains, skills with evidence,
        4–6 next roles with alignment + have/gap requirements (validated with zod, repaired once)
        ▼
 NDJSON stream → live analysis screen → skill graph (current role) ⇄ 3D flip ⇄ next-roles map
        ▼
 click a node → popover → chat seeded with that question
        ▼
 Sonnet 5.5 chat (Agent SDK, streaming, resumable sessions, WebSearch/WebFetch) with the resume,
 source excerpts, graph and click origin as context
```

| Path | What |
| --- | --- |
| `server/claude.ts` | Agent SDK wrappers: `runStructured` (schema-validated JSON) and `streamChat` (streaming + resume). Isolated from user settings/MCP/CLAUDE.md. |
| `server/ingest/` | Source fetchers/extractors (no AI). `npm run ingest-check` exercises them. |
| `server/pipeline.ts`, `prompts.ts`, `schemas.ts` | Readers → synthesizer → `Profile`, streamed as `AnalysisEvent`s. |
| `server/chat.ts`, `store.ts`, `index.ts` | Chat context + sessions, JSON persistence in `data/`, Express routes. |
| `shared/types.ts` | The contract between server and UI. |
| `src/` | React app: `views/` (Onboarding, Analysis, GraphView → NowGraph/NextGraph, ChatView), `components/Radar.tsx` (the skill radar), `theme/` (OKLCH tokens + the drifting-hue system; see `docs/design-notes.md`), `styles/` (per-view stylesheets, no shadows or colour literals). |

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `TAYYARI_READER_MODEL` | `claude-haiku-4-5` | Source readers |
| `TAYYARI_SYNTH_MODEL` | `claude-sonnet-5-5` | Graph synthesizer |
| `TAYYARI_CHAT_MODEL` | `claude-sonnet-5-5` | Career chat |
| `TAYYARI_API_PORT` | `8787` | API (and built app) port |
| `GITHUB_TOKEN` | — | Raises the GitHub API limit from 60 to 5,000 requests/hour (one profile ≈ 12 calls) |
| `TAYYARI_READER_THINKING` | off | `1` re-enables extended thinking for readers (much slower) |
| `ANTHROPIC_API_KEY` | — | Optional: the SDK uses it instead of Claude Code's login if set |

`npm run make-sample` regenerates the offline sample profile with a live run.

## Atmosphere

The background is a soft colour field that drifts through the full hue circle once every
**42 minutes** (1° every 7 s). One hue number (`--h` on `<html>`) feeds every colour token in
`src/theme/tokens.css`; lightness and chroma are fixed per role, so contrast never changes while
the colour travels. The hue is a function of wall-clock time plus a random per-browser offset
(`localStorage` `tayyari.hue0`), so reloads never jump and nothing visibly "starts".

- **Atmosphere control** (header): **Drift** (the 42-minute cycle), **Still** (one calm colour,
  frozen where the clock was at load) or **Off** (neutral greys). The choice persists in
  `localStorage` `tayyari.atmo`.
- **Reduced motion** (`prefers-reduced-motion: reduce`) or a slow screen (`update: slow`) forces
  Still; the control then reads "Still · system".
- **`?h=<degrees>`** pins the base hue for previews and screenshots (`/?h=120`) and forces Still.
- **Fallbacks:** engines without `oklch()` get a fixed hex palette for hue 250 (regenerate it
  with `npm run emit:fallback` after changing a token); `prefers-contrast: more` removes the
  lobes and darkens the ink; `forced-colors: active` hides the field and uses system colours.

## Checks

| Script | Proves |
| --- | --- |
| `npm run check:palette` | Every token in `tokens.css` stays inside the sRGB gamut and meets WCAG contrast (text ≥ 4.5:1, UI ≥ 3:1) at all 360 hues, with the lobe offsets and chroma multipliers at their extremes. |
| `npm run emit:fallback` | Prints the hex `:root { … }` block for hue 250 to paste into `tokens.css`. |
| `npm run check:restraint` | No shadows, blur, filters, borders over 1px, colour literals outside `tokens.css`, paint-triggering keyframes, un-allowlisted infinite animations or hue transitions anywhere in `src/` or `index.html`; the old theme files are gone. |
| `npm run check:build` | After `vite build`, the bundle still has `@property --h`, the oklch tokens, the `@supports` block, no `box-shadow`, `color-scheme` light, the boot script before any stylesheet and the ambient layer. |
| `npm run check` | Typecheck (web + server) followed by the three checks above. |

The 10-step browser walkthrough (hues, modes, keyboard, mobile, idle-paint budget) is in
`scripts/walkthrough.md`.

## Notes and limits (POC)

- LinkedIn usually blocks automated reading; paste your About section into **Extra context** instead.
- Lovable *editor* links need a login; share the published `*.lovable.app` URL.
- Profiles and chat context are stored as JSON under `data/` (git-ignored). There is no auth — run it locally.
