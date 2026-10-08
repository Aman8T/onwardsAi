/**
 * PortfolioView: the standard skin. One scoped wrapper (`.pf-scope`) carries the person's gradient family,
 * field shape, type pairing, corners and density; inside it the header, the blocks in spec order (the graph
 * band is the hero and graph side by side when the hero is `split`), and the provenance footer.
 * With `skin === 'press'` it hands over to the Old Newspaper renderer. Data flows in through props only.
 */
import { useEffect, useId } from 'react';
import type { MouseEvent, ReactNode } from 'react';
import { useReducedMotion } from 'motion/react';
import type { BlockKind, BlockSpec } from '../../shared/portfolio';
import { cssVars, initials } from '../lib/ui';
import '../styles/portfolio.css';
import { About } from './blocks/About';
import { Achievements } from './blocks/Achievements';
import { Clips } from './blocks/Clips';
import { Contact } from './blocks/Contact';
import { Education } from './blocks/Education';
import { Experience } from './blocks/Experience';
import { Footer } from './blocks/Footer';
import { Hero } from './blocks/Hero';
import { OpenTo } from './blocks/OpenTo';
import { Projects } from './blocks/Projects';
import { Proof } from './blocks/Proof';
import { BlockFrame, PfProvider, anchorId, blockHasContent } from './blocks/SectionShell';
import type { BlockProps, PfCtx } from './blocks/SectionShell';
import { scopeClass } from './kit/fields';
import { ensureFonts } from './kit/fonts';
import { typeVars } from './kit/type';
import { graphInHero, graphVariant, shownBlocks, shownDomains, styleVars } from './model';
import type { PortfolioViewProps } from './types';

/* ───────────────────────── graph block ───────────────────────── */

interface GraphFrameProps extends BlockProps {
  placement: 'hero' | 'section';
  renderGraph: PortfolioViewProps['renderGraph'];
  spec: PortfolioViewProps['spec'];
  content: PortfolioViewProps['content'];
  graph: PortfolioViewProps['graph'];
}

function GraphFrame({ block, index, placement, renderGraph, spec, content, graph }: GraphFrameProps) {
  const domains = shownDomains(graph, spec);
  if (!domains.length) return null;
  return (
    <BlockFrame block={block} index={index} ariaLabel="Skill Graph" className={`pf-graph pf-graph--${placement}`}>
      {renderGraph({ spec, graph, domains, variant: graphVariant(spec), content, placement })}
    </BlockFrame>
  );
}

/* ───────────────────────── navigation ───────────────────────── */

const NAV_LABEL: { [K in BlockKind]?: string } = {
  graph: 'Skills',
  experience: 'Work',
  projects: 'Projects',
  clips: 'Clips',
  education: 'Education',
  contact: 'Contact',
};

/** At most four links, dropping the least useful first; always in page order. */
const NAV_PRIORITY: BlockKind[] = ['contact', 'projects', 'experience', 'graph', 'clips', 'education'];

interface NavProps {
  items: { id: string; label: string }[];
  uid: string;
  pills?: boolean;
  onPick?: (blockId: string) => void;
}

function SectionNav({ items, uid, pills, onPick }: NavProps) {
  if (!items.length) return null;
  return (
    <nav aria-label="Sections" className={pills ? 'pf-nav pf-nav--pills' : 'pf-nav pf-nav--top'}>
      {items.map((it) => (
        <a
          key={it.id}
          className={pills ? 'pf-navlink pf-navpill pf-glass' : 'pf-navlink'}
          href={`#${anchorId(uid, it.id)}`}
          onClick={
            onPick
              ? (e: MouseEvent<HTMLAnchorElement>) => {
                  e.preventDefault();
                  onPick(it.id);
                }
              : undefined
          }
        >
          {it.label}
        </a>
      ))}
    </nav>
  );
}

function NewspaperIcon() {
  return (
    <svg
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      <path d="M4 5h13v14H6a2 2 0 0 1-2-2V5z" />
      <path d="M17 9h3v8a2 2 0 0 1-2 2" />
      <path d="M7 9h7M7 12.5h7M7 16h4" />
    </svg>
  );
}

/* ───────────────────────── the standard skin ───────────────────────── */

function StandardSkin({ spec, content, graph, mode, onToggleSkin, editing, renderGraph }: PortfolioViewProps) {
  const reduced = !!useReducedMotion();
  const uid = useId().replace(/[^A-Za-z0-9_-]/g, '');

  const shown = shownBlocks(spec, mode);
  const has = (b: BlockSpec) => blockHasContent(b.kind, content, spec, graph);
  const rendered: PfCtx['rendered'] = {};
  for (const b of shown) if (has(b)) rendered[b.kind] = b.id;

  // The public page leaves out blocks with nothing to show; the editor keeps them so each can show a prompt.
  const list = editing ? shown : shown.filter(has);
  const band = graphInHero(list) && rendered.graph !== undefined;
  const ctx: PfCtx = { spec, content, graph, mode, editing, uid, reduced, rendered };

  const pick = editing?.onSelectBlock;
  const navItems = (() => {
    const candidates = list.filter((b) => rendered[b.kind] !== undefined && NAV_LABEL[b.kind] !== undefined);
    const keep = new Set(
      NAV_PRIORITY.filter((k) => candidates.some((b) => b.kind === k)).slice(0, 4),
    );
    return candidates.filter((b) => keep.has(b.kind)).map((b) => ({ id: b.id, label: NAV_LABEL[b.kind] as string }));
  })();

  const renderBlock = (b: BlockSpec, i: number, placement: 'hero' | 'section' = 'section'): ReactNode => {
    switch (b.kind) {
      case 'hero':
        return <Hero key={b.id} block={b} index={i} />;
      case 'graph':
        return (
          <GraphFrame
            key={b.id}
            block={b}
            index={i}
            placement={placement}
            renderGraph={renderGraph}
            spec={spec}
            content={content}
            graph={graph}
          />
        );
      case 'proof':
        return <Proof key={b.id} block={b} index={i} />;
      case 'about':
        return <About key={b.id} block={b} index={i} />;
      case 'experience':
        return <Experience key={b.id} block={b} index={i} />;
      case 'projects':
        return <Projects key={b.id} block={b} index={i} />;
      case 'clips':
        return <Clips key={b.id} block={b} index={i} />;
      case 'achievements':
        return <Achievements key={b.id} block={b} index={i} />;
      case 'education':
        return <Education key={b.id} block={b} index={i} />;
      case 'openTo':
        return <OpenTo key={b.id} block={b} index={i} />;
      case 'contact':
        return <Contact key={b.id} block={b} index={i} />;
    }
  };

  // On phones the section links leave the header and sit right under the hero (and graph).
  const pillsAfter = band || list[1]?.kind === 'graph' ? 1 : 0;
  const nodes: ReactNode[] = [];
  for (let i = 0; i < list.length; i++) {
    const b = list[i];
    if (band && i === 0) {
      nodes.push(
        <div key="band" className="pf-band">
          {renderBlock(b, 0)}
          {renderBlock(list[1], 1, 'hero')}
        </div>,
      );
      i = 1;
    } else {
      nodes.push(renderBlock(b, i));
    }
    if (i === pillsAfter) {
      nodes.push(<SectionNav key="pills" items={navItems} uid={uid} pills onPick={pick} />);
    }
  }

  const name = content.person.name;
  const mono = content.person.monogram || initials(name);
  const style = cssVars({ ...styleVars(spec), ...typeVars(spec.style.type, name) });

  return (
    <PfProvider value={ctx}>
      <div
        className={scopeClass(spec.style)}
        style={style}
        data-skin="standard"
        data-mode={mode}
        data-editing={editing ? 'true' : undefined}
      >
        <div className="pf-wrap">
          <header className="pf-header">
            <a
              className="pf-brand"
              href={`#${uid}-top`}
              aria-label={`${name}, back to top`}
              onClick={
                editing
                  ? (e) => {
                      e.preventDefault();
                    }
                  : undefined
              }
            >
              <span className="pf-mark" aria-hidden="true">
                {mono}
              </span>
              <span className="pf-brand-name">{name}</span>
            </a>
            <div className="pf-header-end">
              <SectionNav items={navItems} uid={uid} onPick={pick} />
              {onToggleSkin ? (
                <button type="button" className="pf-pill pf-glass" onClick={onToggleSkin}>
                  <NewspaperIcon />
                  Try Old Newspaper Style
                </button>
              ) : null}
            </div>
          </header>
          <main id={`${uid}-top`} className="pf-main">
            {nodes}
          </main>
          <Footer />
        </div>
      </div>
    </PfProvider>
  );
}

/* ───────────────────────── entry point ───────────────────────── */

export function PortfolioView(props: PortfolioViewProps) {
  const { skin, spec, content, graph, mode, onToggleSkin, renderPress } = props;
  const press = skin === 'press';
  const fonts = press ? 'press' : spec.style.type;
  useEffect(() => {
    ensureFonts(fonts);
  }, [fonts]);

  if (press) return <>{renderPress({ spec, content, graph, mode, onToggleSkin })}</>;
  return <StandardSkin {...props} />;
}
