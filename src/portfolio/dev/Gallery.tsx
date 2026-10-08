/**
 * Dev gallery (`/dev/portfolios`, development builds only): every sample portfolio in both skins at desktop
 * (1280) and phone (390) width, side by side, for visual review against the design canvas.
 *
 * The entries are RIYA's three looks plus every `backend/tests/data/portfolio/rendered/*.portfolio.json` the
 * backend has produced (the folder may be empty). Each frame is an iframe onto this same route in "solo" mode
 * (`?solo=<id>&skin=…`), so the page really is 1280 or 390 px wide and its media queries behave as on a phone.
 */
import { useState } from 'react';
import type { CSSProperties } from 'react';
import type { Portfolio, PortfolioSkin } from '../../../shared/portfolio';
import { graphVariant } from '../model';
import type { ViewMode } from '../model';
import { PortfolioPage } from '../PublicView';
import { RIYA } from './samples';

interface Entry {
  id: string;
  title: string;
  detail: string;
  portfolio: Portfolio;
}

const rendered = import.meta.glob<Portfolio>('../../../backend/tests/data/portfolio/rendered/*.portfolio.json', {
  eager: true,
  import: 'default',
});

const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

function entryOf(id: string, title: string, portfolio: Portfolio): Entry {
  const { spec } = portfolio;
  return { id, title, detail: `${cap(spec.composition)} · ${graphVariant(spec)} graph · ${cap(spec.style.family)}`, portfolio };
}

function entries(): Entry[] {
  const out: Entry[] = RIYA.looks.map((look, i) =>
    entryOf(`riya-${look.composition}`, `Riya Kapoor · look ${i + 1}`, { ...RIYA, spec: look, lookIndex: i }),
  );
  for (const [path, portfolio] of Object.entries(rendered).sort(([a], [b]) => a.localeCompare(b))) {
    const file = path.split('/').pop()?.replace(/\.portfolio\.json$/, '') ?? path;
    out.push(entryOf(`file-${file}`, `${portfolio.content.person.name || file} · ${file}`, portfolio));
  }
  return out;
}

const ALL = entries();

const WIDTHS = [
  { label: '1280', width: 1280, height: 860 },
  { label: '390', width: 390, height: 780 },
] as const;
const SKINS: { skin: PortfolioSkin; label: string }[] = [
  { skin: 'standard', label: 'Standard' },
  { skin: 'press', label: 'Newspaper' },
];

/* ───────────────────────── solo: one portfolio, full page ───────────────────────── */

function Solo({ entry, initialSkin, mode }: { entry: Entry; initialSkin: PortfolioSkin; mode: ViewMode }) {
  const [skin, setSkin] = useState<PortfolioSkin>(initialSkin);
  const { spec, content, graph } = entry.portfolio;
  return (
    <PortfolioPage
      spec={spec}
      content={content}
      graph={graph}
      mode={mode}
      skin={skin}
      onToggleSkin={() => setSkin((s) => (s === 'press' ? 'standard' : 'press'))}
    />
  );
}

/* ───────────────────────── the gallery ───────────────────────── */

const pageStyle: CSSProperties = { padding: 'var(--s5) var(--s5) var(--s8)', fontSize: 'var(--fs-2)' };
const barStyle: CSSProperties = { display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 'var(--s4)', marginBottom: 'var(--s6)' };
const rowStyle: CSSProperties = { display: 'flex', alignItems: 'flex-start', gap: 'var(--s5)', overflowX: 'auto', paddingBottom: 'var(--s3)' };
const figureStyle: CSSProperties = { flex: 'none', margin: 0 };
const frameStyle: CSSProperties = { display: 'block', border: 'var(--hair) solid var(--line)', background: 'var(--surface)' };

export function Gallery() {
  const params = new URLSearchParams(window.location.search);
  const solo = params.get('solo');
  const soloEntry = solo ? ALL.find((e) => e.id === solo) : undefined;
  const [mode, setMode] = useState<ViewMode>(params.get('mode') === 'owner' ? 'owner' : 'public');

  if (soloEntry) {
    return <Solo entry={soloEntry} initialSkin={params.get('skin') === 'press' ? 'press' : 'standard'} mode={params.get('mode') === 'owner' ? 'owner' : 'public'} />;
  }

  const src = (e: Entry, skin: PortfolioSkin) =>
    `/dev/portfolios?${new URLSearchParams({ solo: e.id, skin, mode }).toString()}`;

  return (
    <main style={pageStyle}>
      <header style={barStyle}>
        <h1 className="serif" style={{ margin: 0, fontSize: 'var(--fs-6)' }}>
          Portfolio gallery
        </h1>
        <span className="kicker">
          Dev only · {ALL.length} portfolios · each frame is the real page at that width
        </span>
        <div role="group" aria-label="View mode" style={{ display: 'flex', gap: 'var(--s2)' }}>
          {(['public', 'owner'] as const).map((m) => (
            <button key={m} type="button" className="btn btn-sm" aria-pressed={mode === m} onClick={() => setMode(m)}>
              {m === 'public' ? 'Public view' : 'Owner view'}
            </button>
          ))}
        </div>
        <nav aria-label="Portfolios" style={{ display: 'flex', flexWrap: 'wrap', gap: 'var(--s3)' }}>
          {ALL.map((e) => (
            <a key={e.id} href={`#${e.id}`}>
              {e.title}
            </a>
          ))}
        </nav>
      </header>

      {ALL.map((e) => (
        <section key={e.id} id={e.id} aria-labelledby={`${e.id}-h`} style={{ marginBottom: 'var(--s8)' }}>
          <h2 id={`${e.id}-h`} className="serif" style={{ margin: 0, fontSize: 'var(--fs-5)' }}>
            {e.title}
          </h2>
          <p className="kicker" style={{ margin: 'var(--s1) 0 var(--s4)' }}>
            {e.detail}
          </p>
          <div style={rowStyle}>
            {WIDTHS.flatMap((w) =>
              SKINS.map((s) => (
                <figure key={`${w.label}-${s.skin}`} style={figureStyle}>
                  <figcaption className="kicker" style={{ marginBottom: 'var(--s2)' }}>
                    {s.label} · {w.label}
                  </figcaption>
                  <iframe
                    title={`${e.title}, ${s.label.toLowerCase()} skin, ${w.label} px wide`}
                    src={src(e, s.skin)}
                    loading="lazy"
                    width={w.width}
                    height={w.height}
                    style={frameStyle}
                  />
                </figure>
              )),
            )}
          </div>
        </section>
      ))}
    </main>
  );
}
