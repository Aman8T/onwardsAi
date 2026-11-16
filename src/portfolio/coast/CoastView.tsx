/**
 * The Neon Coast skin: the same spec, content and graph as the standard page, set as a sunset poster.
 *
 * One page model (derive.ts) becomes a stack of bands, cut at shallow angles like the panels of a poster: the
 * title over the horizon, the Skill Graph, About beside the public numbers, the work, the projects, achievements
 * beside education, the clips, and a sign-off, with the person's own words set between two of them. A band exists only when its block is
 * shown and has something to say; the seams between bands lean one way and then the other, decided here in code
 * so any two neighbours always share a parallel cut. Phones get one straight column (coast.css).
 *
 * The skin changes how things look and nothing else: every line is a fact field, a copy slot or the kit's own
 * chrome (the panel titles below), as in the other skins.
 */
import { useId, useMemo, useState } from 'react';
import type { CSSProperties, ReactNode } from 'react';
import { cssVars } from '../../lib/ui';
import { graphVariant, shownDomains } from '../model';
import { SkinPicker } from '../SkinPicker';
import type { CoastRenderArgs } from '../types';
import '../../styles/coast.css';
import { CoastGraph } from './CoastGraph';
import { buildCoast } from './derive';
import type { CoastAnchor, CoastPage } from './derive';
import { Horizon } from './Horizon';

/** Lists collapse after this many items behind a "+n more" button, as in the standard skin. */
const LIST_LIMIT = 6;

type Tone = 'night' | 'plum' | 'teal' | 'mint' | 'pink' | 'gold' | 'orange';
type Seam = 'flat' | 'rise' | 'fall';

interface Band {
  key: string;
  /** A night panel: two of them never sit next to each other when the page can avoid it. */
  night: boolean;
  node: (seams: { top: Seam; bottom: Seam }) => ReactNode;
}

function useLimit<T>(items: T[]) {
  const [open, setOpen] = useState(false);
  const over = items.length > LIST_LIMIT;
  return { visible: over && !open ? items.slice(0, LIST_LIMIT) : items, hidden: items.length - LIST_LIMIT, over, open, toggle: () => setOpen((o) => !o) };
}

function More({ lim }: { lim: ReturnType<typeof useLimit> }) {
  if (!lim.over) return null;
  return (
    <button type="button" className="nc-more" onClick={lim.toggle} aria-expanded={lim.open}>
      {lim.open ? 'Show fewer' : `+${lim.hidden} more`}
    </button>
  );
}

function Out({ href, children, label }: { href: string; children: ReactNode; label?: string }) {
  const mail = /^mailto:/i.test(href);
  return (
    <a className="nc-link" href={href} aria-label={label} {...(mail ? {} : { target: '_blank', rel: 'noopener noreferrer' })}>
      {children}
      {!mail && <span aria-hidden="true">&nbsp;↗</span>}
    </a>
  );
}

/* ───────────────────────── panels ───────────────────────── */

function Panel(props: { tone: Tone; id?: string; labelledBy?: string; label?: string; className?: string; children: ReactNode }) {
  return (
    <section
      id={props.id}
      className={`nc-panel nc-panel--${props.tone}${props.className ? ` ${props.className}` : ''}`}
      aria-labelledby={props.labelledBy}
      aria-label={props.label}
    >
      {props.children}
    </section>
  );
}

function About({ about, titleId }: { about: NonNullable<CoastPage['about']>; titleId: string }) {
  return (
    <div className="nc-about">
      <div className="nc-about-text">
        <h2 id={titleId} className="nc-h">
          About
        </h2>
        {about.lede && <p className="nc-lede">{about.lede}</p>}
        {about.body && <p className="nc-body">{about.body}</p>}
      </div>
      {about.facts.length > 0 && (
        <dl className="nc-dl">
          {about.facts.map((r) => (
            <div key={r.k}>
              <dt>{r.k}</dt>
              <dd>{r.v}</dd>
            </div>
          ))}
        </dl>
      )}
    </div>
  );
}

function Work({ page, titleId }: { page: CoastPage; titleId: string }) {
  const lim = useLimit(page.roles);
  return (
    <>
      <header className="nc-head">
        <h2 id={titleId} className="nc-h">
          Experience
        </h2>
        {page.rolesMeta && <p className="nc-meta">{page.rolesMeta}</p>}
      </header>
      <ol className="nc-roles">
        {lim.visible.map((r) => (
          <li key={r.id} className="nc-role">
            <p className="nc-when">{r.period}</p>
            <div className="nc-role-main">
              <h3 className="nc-title">{r.title}</h3>
              {r.org && <p className="nc-org">{r.org}</p>}
              {r.sub && <p className="nc-sub">{r.sub}</p>}
              {r.lines.length > 0 && (
                <ul className="nc-lines">
                  {r.lines.map((line, i) => (
                    <li key={i}>{line}</li>
                  ))}
                </ul>
              )}
            </div>
          </li>
        ))}
      </ol>
      <More lim={lim} />
    </>
  );
}

function Projects({ page, titleId }: { page: CoastPage; titleId: string }) {
  const lim = useLimit(page.projects);
  const one = page.projects.length === 1;
  return (
    <>
      <header className="nc-head">
        <h2 id={titleId} className="nc-h">
          {one ? 'Featured project' : 'Selected work'}
        </h2>
        <p className="nc-meta">{one ? '1 project' : `${page.projects.length} projects`}</p>
      </header>
      <ul className={one ? 'nc-projects nc-projects--one' : 'nc-projects'}>
        {lim.visible.map((p) => (
          <li key={p.id} className="nc-project">
            <h3 className="nc-title">{p.title}</h3>
            {p.meta && <p className="nc-sub">{p.meta}</p>}
            {p.blurb && <p className="nc-body">{p.blurb}</p>}
            {p.tags.length > 0 && (
              <ul className="nc-tags" aria-label="Built with">
                {p.tags.map((t) => (
                  <li key={t}>{t}</li>
                ))}
              </ul>
            )}
            {p.href && <Out href={p.href}>{p.linkLabel}</Out>}
          </li>
        ))}
      </ul>
      <More lim={lim} />
    </>
  );
}

function Honours({ page, titleId }: { page: CoastPage; titleId: string }) {
  const lim = useLimit(page.honours);
  return (
    <>
      <h2 id={titleId} className="nc-h">
        Achievements
      </h2>
      <ul className="nc-honours">
        {lim.visible.map((h) => (
          <li key={h.id} className="nc-honour">
            {h.figure && <span className="nc-figure">{h.figure}</span>}
            <span className="nc-honour-line">{h.title}</span>
            {h.meta && <span className="nc-honour-meta">{h.meta}</span>}
          </li>
        ))}
      </ul>
      <More lim={lim} />
    </>
  );
}

function Schools({ page, titleId }: { page: CoastPage; titleId: string }) {
  const lim = useLimit(page.schools);
  return (
    <>
      <h2 id={titleId} className="nc-h">
        Education
      </h2>
      <ul className="nc-schools">
        {lim.visible.map((s) => (
          <li key={s.id} className="nc-school">
            {s.period && <p className="nc-when">{s.period}</p>}
            <h3 className="nc-title">{s.title}</h3>
            {s.place && <p className="nc-org">{s.place}</p>}
            {s.detail && <p className="nc-sub">{s.detail}</p>}
            {s.grade && <p className="nc-sub">{s.grade}</p>}
          </li>
        ))}
      </ul>
      <More lim={lim} />
    </>
  );
}

function Clips({ page, titleId }: { page: CoastPage; titleId: string }) {
  const lim = useLimit(page.clips);
  return (
    <>
      <header className="nc-head">
        <h2 id={titleId} className="nc-h">
          Clips
        </h2>
        <p className="nc-meta">{page.clips.length === 1 ? '1 piece' : `${page.clips.length} pieces`}</p>
      </header>
      <ul className="nc-clips">
        {lim.visible.map((c) => (
          <li key={c.id} className="nc-clip">
            <div className="nc-clip-main">
              <h3 className="nc-title">{c.title}</h3>
              {c.meta && <p className="nc-sub">{c.meta}</p>}
            </div>
            {c.href && (
              <Out href={c.href} label={`Read: ${c.title}`}>
                Read
              </Out>
            )}
          </li>
        ))}
      </ul>
      <More lim={lim} />
    </>
  );
}

function SignOff({ page, titleId }: { page: CoastPage; titleId: string }) {
  return (
    <>
      {page.openTo.length > 0 && (
        <p className="nc-open">
          <span className="nc-open-k">Open to</span>
          <span className="nc-open-v nc-neon nc-neon--aqua">{page.openTo.join(' · ')}</span>
        </p>
      )}
      {page.contact.length > 0 && (
        <>
          <h2 id={titleId} className="nc-h nc-h--sign">
            Say hello.
          </h2>
          <ul className="nc-contact">
            {page.contact.map((it) => (
              <li key={it.key}>
                {it.href ? (
                  <Out href={it.href}>{it.text}</Out>
                ) : (
                  <span className="nc-contact-text">
                    <span className="nc-contact-k">{it.label}</span> {it.text}
                  </span>
                )}
              </li>
            ))}
          </ul>
        </>
      )}
      <div className="nc-dusk" aria-hidden="true" />
    </>
  );
}

/* ───────────────────────── the page ───────────────────────── */

export function CoastView({ spec, content, graph, mode, onPickSkin }: CoastRenderArgs) {
  const uid = useId().replace(/[^A-Za-z0-9_-]/g, '');
  const page = useMemo(() => buildCoast(spec, content, graph, mode), [spec, content, graph, mode]);
  const domains = shownDomains(graph, spec);
  const id = (key: string) => `${uid}-${key}`;
  const anchor = (to: CoastAnchor) => `#${id(to)}`;

  // No entrance when the page loads in a hidden tab (a link opened in the background, a headless capture): animations
  // do not advance there, and the title would sit at opacity 0.
  const [still] = useState(() => typeof document !== 'undefined' && document.visibilityState === 'hidden');

  // The title's entrance: each line of the hero fades in a beat after the one before it (CSS; off under reduced motion).
  let step = 0;
  const rise = (): CSSProperties => cssVars({ '--i': String(step++) });

  const band = (key: string, tone: Tone | null, body: ReactNode, opts: { id?: string; labelledBy?: string; label?: string; className?: string } = {}): Band => ({
    key,
    night: tone === 'night',
    node: ({ top, bottom }) =>
      tone ? (
        <section
          key={key}
          id={opts.id}
          className={`nc-band nc-panel nc-panel--${tone}${opts.className ? ` ${opts.className}` : ''}`}
          data-top={top}
          data-bottom={bottom}
          aria-labelledby={opts.labelledBy}
          aria-label={opts.label}
        >
          {body}
        </section>
      ) : (
        <div key={key} className={`nc-band${opts.className ? ` ${opts.className}` : ''}`} data-top={top} data-bottom={bottom}>
          {body}
        </div>
      ),
  });

  /** Two panels side by side, parted by a leaning cut; one panel alone takes the whole band. */
  const pair = (key: string, lean: 'fwd' | 'back', left: ReactNode, right: ReactNode): Band | null => {
    if (!left && !right) return null;
    const both = !!left && !!right;
    return band(key, null, <>{left}{right}</>, { className: both ? `nc-duo nc-duo--${lean}` : 'nc-solo' });
  };

  const quote = page.quote
    ? band(
        'quote',
        'night',
        <figure className="nc-quote">
          <blockquote>
            <p className={page.quote.text.length <= 110 ? 'nc-quote-text nc-neon' : 'nc-quote-text'}>“{page.quote.text}”</p>
          </blockquote>
          <figcaption className="nc-meta">{page.quote.caption}</figcaption>
        </figure>,
        { label: 'In their own words', className: 'nc-p-quote' },
      )
    : null;

  const bands: (Band | null)[] = [
    band(
      'hero',
      null,
      <>
        <div className="nc-sky">
          <div className="nc-hero-copy">
            {page.eyebrow && (
              <p className="nc-eyebrow nc-rise" style={rise()}>
                {page.eyebrow}
              </p>
            )}
            <h1
              id={id('name')}
              className={`nc-name nc-rise${page.plainName ? ' nc-name--plain' : ''}`}
              style={{ ...rise(), ...cssVars({ '--nc-longest': String(page.longest) }) }}
            >
              {page.name}
            </h1>
            {page.tagline && (
              <p className="nc-tagline nc-rise" style={rise()}>
                {page.tagline}
              </p>
            )}
            {page.facts.length > 0 && (
              <ul className="nc-facts nc-rise" style={rise()}>
                {page.facts.map((f) => (
                  <li key={f}>{f}</li>
                ))}
              </ul>
            )}
            {page.actions.length > 0 && (
              <div className="nc-actions nc-rise" style={rise()}>
                {page.actions.map((a, i) => (
                  <a key={a.to} className={i === 0 ? 'nc-btn' : 'nc-btn nc-btn--ghost'} href={anchor(a.to)}>
                    {a.label}
                  </a>
                ))}
              </div>
            )}
          </div>
        </div>
        <Horizon />
      </>,
      { className: 'nc-hero' },
    ),

    page.showGraph
      ? band('skills', 'night', <CoastGraph domains={domains} variant={graphVariant(spec)} graph={graph} titleId={id('skills-h')} />, {
          id: id('skills'),
          labelledBy: id('skills-h'),
          className: 'nc-p-graph',
        })
      : null,

    pair(
      'about',
      'fwd',
      page.about ? (
        <Panel key="about" tone="mint" labelledBy={id('about-h')} className="nc-p-about">
          <About about={page.about} titleId={id('about-h')} />
        </Panel>
      ) : null,
      page.stats.length > 0 ? (
        <Panel key="proof" tone="gold" label="Public proof" className="nc-p-proof">
          <ul className="nc-stats">
            {page.stats.map((s, i) => (
              <li key={`${s.label}-${i}`} className="nc-stat">
                <span className="nc-stat-v">{s.value}</span>
                <span className="nc-stat-l">{s.label}</span>
              </li>
            ))}
          </ul>
        </Panel>
      ) : null,
    ),

    page.roles.length > 0
      ? band('work', 'plum', <Work page={page} titleId={id('work-h')} />, { id: id('work'), labelledBy: id('work-h') })
      : null,

    page.projects.length > 0
      ? band('projects', 'teal', <Projects page={page} titleId={id('projects-h')} />, { id: id('projects'), labelledBy: id('projects-h') })
      : null,

    pair(
      'record',
      'back',
      page.honours.length > 0 ? (
        <Panel key="honours" tone="pink" labelledBy={id('honours-h')}>
          <Honours page={page} titleId={id('honours-h')} />
        </Panel>
      ) : null,
      page.schools.length > 0 ? (
        <Panel key="schools" tone="mint" labelledBy={id('schools-h')}>
          <Schools page={page} titleId={id('schools-h')} />
        </Panel>
      ) : null,
    ),

    page.clips.length > 0
      ? band('clips', 'orange', <Clips page={page} titleId={id('clips-h')} />, { id: id('clips'), labelledBy: id('clips-h') })
      : null,

    page.contact.length > 0 || page.openTo.length > 0
      ? band('sign', 'night', <SignOff page={page} titleId={id('contact-h')} />, {
          id: id('contact'),
          labelledBy: page.contact.length > 0 ? id('contact-h') : undefined,
          label: page.contact.length > 0 ? undefined : 'Open to',
          className: 'nc-p-sign',
        })
      : null,
  ];

  const live = bands.filter((b): b is Band => b !== null);
  // The person's own words go in as a night panel at the first seam after the title where neither neighbour is night
  // (after About when it is there, else further down); with no such seam, right after the Skill Graph.
  if (quote) {
    const free = live.findIndex((b, i) => i >= 1 && !b.night && !(live[i + 1]?.night ?? false));
    const lit = live.findIndex((b, i) => i >= 1 && !b.night);
    live.splice((free !== -1 ? free : lit !== -1 ? lit : Math.min(1, live.length - 1)) + 1, 0, quote);
  }

  // Seams lean one way, then the other; the first band's top and the last band's foot stay straight.
  const seam = (i: number): Seam => (i % 2 === 0 ? 'rise' : 'fall');

  return (
    <div className={still ? 'pf-scope nc nc--still' : 'pf-scope nc'} data-skin="coast" data-mode={mode}>
      <div className="nc-page">
        <header className="nc-strip">
          <a className="nc-brand" href={`#${id('top')}`} aria-label={`${page.name}, back to top`}>
            <span className="nc-mark" aria-hidden="true">
              {page.monogram}
            </span>
            <span className="nc-brand-name">{page.name}</span>
          </a>
          {page.nav.length > 0 && (
            <nav className="nc-nav" aria-label="Sections">
              {page.nav.map((n) => (
                <a key={n.to} href={anchor(n.to)}>
                  {n.label}
                </a>
              ))}
            </nav>
          )}
          {onPickSkin && <SkinPicker skin="coast" onPick={onPickSkin} />}
        </header>

        <main id={id('top')} className="nc-poster" aria-labelledby={id('name')}>
          {live.map((b, i) => b.node({ top: i === 0 ? 'flat' : seam(i - 1), bottom: i === live.length - 1 ? 'flat' : seam(i) }))}
        </main>

        <footer className="nc-foot">
          <span>{page.footer}</span>
          <span>Made with Leafout</span>
        </footer>
      </div>
    </div>
  );
}
