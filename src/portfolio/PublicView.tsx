/**
 * The public page (`/p/:publicId`): a read-only portfolio for anyone with the link. It fetches the filtered
 * public DTO (never the profile id, private blocks or opt-in details) and renders `PortfolioView` in `public`
 * mode. The skin comes from `?view=press` (or `?view=standard`), else the owner's default view; the
 * "Try Old Newspaper Style" pill toggles it and keeps the address bar in sync with `history.replaceState`,
 * so the current look is always a shareable link. An unknown or unpublished id gets a calm page, not an error.
 *
 * This file also hosts the pieces every portfolio screen shares (graph / newspaper renderers wired to the
 * real components, and the calm full-page message), so the owner screen and the dev gallery import them
 * from here and the public bundle never pulls in the editor.
 */
import { useCallback, useEffect, useState } from 'react';
import type { CSSProperties, ReactNode } from 'react';
import type { PortfolioSkin, PublicPortfolio } from '../../shared/portfolio';
import { getPublicPortfolio } from '../lib/api';
import { SkillGraph } from './graph/SkillGraph';
import { PressSkillsIndex } from './graph/PressSkillsIndex';
import { PressView } from './press/PressView';
import { PortfolioView } from './PortfolioView';
import type { GraphRenderArgs, PortfolioViewProps, PressIndexArgs, PressRenderArgs } from './types';

/* ───────────────────────── shared wiring ───────────────────────── */

export const renderGraph = (args: GraphRenderArgs): ReactNode => <SkillGraph {...args} />;
const renderIndex = (args: PressIndexArgs): ReactNode => <PressSkillsIndex {...args} />;
export const renderPress = (args: PressRenderArgs): ReactNode => <PressView {...args} renderIndex={renderIndex} />;

/** `PortfolioView` with the real graph and newspaper renderers plugged in. */
export function PortfolioPage(props: Omit<PortfolioViewProps, 'renderGraph' | 'renderPress'>) {
  return <PortfolioView {...props} renderGraph={renderGraph} renderPress={renderPress} />;
}

const pageStyle: CSSProperties = {
  display: 'grid',
  placeItems: 'center',
  minHeight: '100dvh',
  padding: 'var(--s6) var(--s4)',
  textAlign: 'center',
};
const boxStyle: CSSProperties = { display: 'grid', justifyItems: 'center', gap: 'var(--s3)', maxWidth: '46ch' };
const titleStyle: CSSProperties = { margin: 0, fontSize: 'var(--fs-6)' };
const textStyle: CSSProperties = { margin: 0, color: 'var(--text-muted)' };
const actionsStyle: CSSProperties = { display: 'flex', flexWrap: 'wrap', justifyContent: 'center', gap: 'var(--s2)', marginTop: 'var(--s2)' };

/** A calm full-page message: a small kicker, a serif title, one line of text and optional actions. */
export function CalmPage({
  kicker,
  title,
  children,
  actions,
  live,
}: {
  kicker: string;
  title: string;
  children?: ReactNode;
  actions?: ReactNode;
  /** The text below the title changes while the page is shown (progress): announce it politely. */
  live?: boolean;
}) {
  return (
    <main style={pageStyle}>
      <div style={boxStyle}>
        <span className="kicker">{kicker}</span>
        <h1 className="serif" style={titleStyle}>
          {title}
        </h1>
        {children !== undefined && (
          <div style={textStyle} role={live ? 'status' : undefined} aria-live={live ? 'polite' : undefined}>
            {children}
          </div>
        )}
        {actions && <div style={actionsStyle}>{actions}</div>}
      </div>
    </main>
  );
}

/* ───────────────────────── the page ───────────────────────── */

type Load = { kind: 'loading' } | { kind: 'ready'; data: PublicPortfolio } | { kind: 'missing' } | { kind: 'error'; message: string };

/** The id in `/p/<id>`, or '' when the path has none. */
function publicIdFromPath(): string {
  const raw = /^\/p\/([^/?#]+)/.exec(window.location.pathname)?.[1];
  if (!raw) return '';
  try {
    return decodeURIComponent(raw);
  } catch {
    return '';
  }
}

/** The skin the link asks for, if any. */
function skinFromQuery(): PortfolioSkin | null {
  const view = new URLSearchParams(window.location.search).get('view');
  return view === 'press' || view === 'standard' ? view : null;
}

/** Keep the address bar on the look being shown: `?view=` only when it differs from the owner's default. */
function syncUrl(skin: PortfolioSkin, defaultView: PortfolioSkin) {
  try {
    const url = new URL(window.location.href);
    if (skin === defaultView) url.searchParams.delete('view');
    else url.searchParams.set('view', skin);
    window.history.replaceState(window.history.state, '', url);
  } catch {
    /* the address bar is cosmetic; the page works without it */
  }
}

export function PublicView() {
  const publicId = publicIdFromPath();
  const [load, setLoad] = useState<Load>({ kind: 'loading' });
  const [attempt, setAttempt] = useState(0);
  const [picked, setPicked] = useState<PortfolioSkin | null>(skinFromQuery);

  useEffect(() => {
    if (!publicId) {
      setLoad({ kind: 'missing' });
      return;
    }
    const ctrl = new AbortController();
    setLoad({ kind: 'loading' });
    getPublicPortfolio(publicId, ctrl.signal)
      .then((data) => setLoad(data ? { kind: 'ready', data } : { kind: 'missing' }))
      .catch((err: unknown) => {
        if (ctrl.signal.aborted) return;
        setLoad({ kind: 'error', message: err instanceof Error ? err.message : String(err) });
      });
    return () => ctrl.abort();
  }, [publicId, attempt]);

  const ready = load.kind === 'ready' ? load.data : null;
  const name = ready?.content.person.name;
  useEffect(() => {
    const before = document.title;
    document.title = name ? `${name} · Portfolio` : load.kind === 'loading' ? before : 'Portfolio · Tayyari';
    return () => {
      document.title = before;
    };
  }, [name, load.kind]);

  const skin: PortfolioSkin = picked ?? ready?.defaultView ?? 'standard';
  const toggleSkin = useCallback(() => {
    if (!ready) return;
    const next: PortfolioSkin = skin === 'press' ? 'standard' : 'press';
    setPicked(next);
    syncUrl(next, ready.defaultView);
  }, [ready, skin]);

  if (ready) {
    return <PortfolioPage spec={ready.spec} content={ready.content} graph={ready.graph} mode="public" skin={skin} onToggleSkin={toggleSkin} />;
  }
  if (load.kind === 'missing') {
    return (
      <CalmPage
        kicker="Portfolio"
        title="This portfolio isn’t published"
        actions={
          <a className="btn" href="/">
            Go to Tayyari
          </a>
        }
      >
        The link may be mistyped, or its owner has not shared this page, or has taken it down.
      </CalmPage>
    );
  }
  if (load.kind === 'error') {
    return (
      <CalmPage
        kicker="Portfolio"
        title="This portfolio didn’t load"
        actions={
          <button type="button" className="btn" onClick={() => setAttempt((n) => n + 1)}>
            Try again
          </button>
        }
      >
        {load.message}
      </CalmPage>
    );
  }
  return (
    <CalmPage kicker="Portfolio" title="Opening the portfolio" live>
      One moment.
    </CalmPage>
  );
}
