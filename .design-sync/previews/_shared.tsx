/**
 * Helpers shared by the authored previews. Not a component preview itself (the converter compiles only
 * `<ComponentName>.tsx`), so nothing here becomes a card.
 *
 * Everything renders through the shipped bundle (`import ... from 'tayyari'` resolves to `window.Tayyari`),
 * so the portfolio blocks see the same `PfProvider` context the real page gives them.
 */
import type { ComponentType, CSSProperties, ReactNode } from 'react';
import { PfProvider, cssVars, sampleRiya, scopeClass, styleVars, typeVars } from 'tayyari';
import type { BlockProps, PfCtx } from 'tayyari';

/* ───────────── settled motion ───────────── */

/**
 * Pin motion/react entrance animations at their final state. The capture harness freezes the page clock, so a
 * JavaScript tween from opacity 0 never advances and the element stays invisible; a static card should show the
 * settled state anyway. Only the two components that animate without a reduced-motion opt-out need it:
 * `.pop` (Popover's fade-and-rise) and `.pf-block` (PortfolioPage's staggered block fade-up).
 */
export function settleMotion(): void {
  if (typeof document === 'undefined' || document.getElementById('ds-settle-motion')) return;
  const style = document.createElement('style');
  style.id = 'ds-settle-motion';
  style.textContent = '.pop, .pf-block { opacity: 1 !important; transform: none !important; }';
  document.head.appendChild(style);
}

/* ───────────── portfolio blocks ───────────── */

/** Riya Kapoor's three stored looks: 0 Signature (iris, studio), 1 Narrative (glacier, editorial), 2 Builder (sage, technical). */
export type Look = 0 | 1 | 2;

/** The page wrapper a block needs: the portfolio context plus the `.pf-scope` element carrying the look's hue and type. */
export function PfFrame({ look = 0, children }: { look?: Look; children: ReactNode }) {
  const spec = sampleRiya.looks[look];
  const { content, graph } = sampleRiya;
  const rendered: PfCtx['rendered'] = {};
  for (const b of spec.blocks) if (b.visible) rendered[b.kind] = b.id;
  const ctx: PfCtx = { spec, content, graph, mode: 'public', uid: `pv${look}`, reduced: true, rendered };
  const style = cssVars({ ...styleVars(spec), ...typeVars(spec.style.type, content.person.name) });
  return (
    <PfProvider value={ctx}>
      <div className={scopeClass(spec.style)} style={style} data-skin="standard" data-mode="public">
        <div className="pf-wrap">{children}</div>
      </div>
    </PfProvider>
  );
}

/** One block from one look, exactly as PortfolioView would render it. */
export function PfBlock({ look = 0, kind, as: Block }: { look?: Look; kind: string; as: ComponentType<BlockProps> }) {
  const spec = sampleRiya.looks[look];
  const block = spec.blocks.find((b) => b.kind === kind);
  if (!block) return <p className="kicker">no {kind} block in look {look}</p>;
  return (
    <PfFrame look={look}>
      <Block block={block} index={0} />
    </PfFrame>
  );
}

/* ───────────── icons ───────────── */

type IconComponent = ComponentType<{ size?: number; className?: string; style?: CSSProperties }>;

const row: CSSProperties = { display: 'flex', alignItems: 'center', gap: 'var(--s5)', color: 'var(--text)' };
const col: CSSProperties = { display: 'grid', justifyItems: 'center', gap: 'var(--s2)' };

/** The icon at the three sizes the app uses, then in the two ink colours it is drawn with. */
export function IconSheet({ icon: Icon, name }: { icon: IconComponent; name: string }) {
  return (
    <div style={{ display: 'grid', gap: 'var(--s4)' }}>
      <p className="kicker">{name}</p>
      <div style={row}>
        {[18, 24, 32].map((size) => (
          <div key={size} style={col}>
            <Icon size={size} />
            <span className="numeral" style={{ fontSize: 'var(--fs-0)', color: 'var(--text-muted)' }}>
              {size}
            </span>
          </div>
        ))}
        <div style={{ ...col, color: 'var(--accent-ui)' }}>
          <Icon size={24} />
          <span className="kicker">accent-ui</span>
        </div>
        <div style={{ ...col, color: 'var(--text-muted)' }}>
          <Icon size={24} />
          <span className="kicker">muted</span>
        </div>
      </div>
    </div>
  );
}

/** The icon inside the three button primitives: a labelled button, the primary button, and an icon-only quiet button. */
export function IconButtons({ icon: Icon, label }: { icon: IconComponent; label: string }) {
  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 'var(--s3)' }}>
      <button type="button" className="btn">
        <Icon size={16} /> {label}
      </button>
      <button type="button" className="btn btn-primary">
        <Icon size={16} /> {label}
      </button>
      <button type="button" className="btn btn-quiet btn-sm" aria-label={label}>
        <Icon size={16} />
      </button>
    </div>
  );
}
