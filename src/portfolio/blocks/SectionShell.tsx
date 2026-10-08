/**
 * Shared frame for every standard-skin block: the page context, the block wrapper (entrance motion and
 * edit-mode selection), the section label (a rail beside the content, or a bar for the Ledger
 * composition), slot editors, and the small helpers blocks share (safe links, "+n more", contact facts,
 * "does this block have anything to show"). The blocks import everything shared from here.
 */
import { createContext, useContext, useState } from 'react';
import type { KeyboardEvent, MouseEvent, ReactNode } from 'react';
import { motion } from 'motion/react';
import type {
  BlockKind,
  BlockSpec,
  Fact,
  GraphSnapshot,
  PortfolioContent,
  PortfolioSpec,
} from '../../../shared/portfolio';
import { EASE } from '../../lib/ui';
import { displayUrl, factsOf, safeHref, shownDomains, slot, slotText } from '../model';
import type { ViewMode } from '../model';
import type { EditHooks } from '../types';

/* ───────────────────────── context ───────────────────────── */

export interface PfCtx {
  spec: PortfolioSpec;
  content: PortfolioContent;
  graph: GraphSnapshot;
  mode: ViewMode;
  editing?: EditHooks;
  /** Prefix for element ids on this page (anchors, labelled-by), unique per rendered portfolio. */
  uid: string;
  reduced: boolean;
  /** Block id by kind for the blocks that will actually render (shown and with content). */
  rendered: { [K in BlockKind]?: string };
}

export interface BlockProps {
  block: BlockSpec;
  /** Position among the rendered blocks; the first few stagger their entrance. */
  index: number;
}

const Ctx = createContext<PfCtx | null>(null);
const BlockIdCtx = createContext<string | null>(null);

export const PfProvider = Ctx.Provider;

export function usePf(): PfCtx {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error('Portfolio blocks must render inside <PortfolioView>.');
  return ctx;
}

export const cx = (...parts: (string | false | null | undefined)[]): string => parts.filter(Boolean).join(' ');

/** The element id of a block's section, for anchors. */
export const anchorId = (uid: string, blockId: string): string => `${uid}-${blockId}`;

/* ───────────────────────── block frame ───────────────────────── */

interface FrameProps {
  block: BlockSpec;
  index: number;
  tag?: 'section' | 'div';
  className?: string;
  ariaLabel?: string;
  labelledBy?: string;
  children: ReactNode;
}

/**
 * The wrapper every block renders into: id, classes, entrance fade-up (600 ms, staggered by block, off under
 * reduced motion) and, when the editor passes `editing.onSelectBlock`, click / Enter / Space selection.
 */
export function BlockFrame({ block, index, tag = 'section', className, ariaLabel, labelledBy, children }: FrameProps) {
  const { editing, reduced, uid } = usePf();
  const M = (tag === 'div' ? motion.div : motion.section) as typeof motion.section;
  const select = editing?.onSelectBlock;
  const selected = editing?.selectedBlockId === block.id;

  const editProps = select
    ? {
        tabIndex: 0,
        'data-editable': 'true',
        'data-selected': selected ? 'true' : 'false',
        onClick: (e: MouseEvent<HTMLElement>) => {
          // Links in the preview must never navigate away from the editor.
          if ((e.target as HTMLElement).closest('a')) e.preventDefault();
          select(block.id);
        },
        onKeyDown: (e: KeyboardEvent<HTMLElement>) => {
          if (e.target === e.currentTarget && (e.key === 'Enter' || e.key === ' ')) {
            e.preventDefault();
            select(block.id);
          }
        },
      }
    : null;

  return (
    <BlockIdCtx.Provider value={block.id}>
      <M
        id={anchorId(uid, block.id)}
        className={cx('pf-block', `pf-b-${block.kind}`, `pf-v-${block.variant}`, className)}
        data-block={block.id}
        aria-label={ariaLabel}
        aria-labelledby={labelledBy}
        initial={reduced ? false : { opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.6, ease: EASE, delay: reduced ? 0 : Math.min(index, 4) * 0.08 }}
        {...editProps}
      >
        {children}
      </M>
    </BlockIdCtx.Provider>
  );
}

/* ───────────────────────── section shell ───────────────────────── */

interface ShellProps extends BlockProps {
  /** Section label, e.g. "Experience". */
  label: string;
  /** Right-hand note in the Ledger composition's section bar (counts derived from the facts). */
  meta?: string;
  className?: string;
  children: ReactNode;
}

/** A labelled section: label rail on the left (phones: above), or a hairline bar for the Ledger composition. */
export function SectionShell({ block, index, label, meta, className, children }: ShellProps) {
  const { spec, uid } = usePf();
  const headId = `${uid}-${block.id}-h`;
  const bar = spec.composition === 'ledger';
  return (
    <BlockFrame block={block} index={index} labelledBy={headId} className={className}>
      {bar ? (
        <>
          <div className="pf-bar">
            <h2 id={headId} className="pf-label">
              {label}
            </h2>
            {meta ? <span className="pf-bar-meta">{meta}</span> : null}
          </div>
          <div className="pf-bar-body">{children}</div>
        </>
      ) : (
        <div className="pf-rail">
          <h2 id={headId} className="pf-label pf-rail-label">
            {label}
          </h2>
          <div className="pf-rail-body">{children}</div>
        </div>
      )}
    </BlockFrame>
  );
}

/** What an empty block shows in the editor (a prompt) instead of being left out as on the public page. */
export function EmptyBlock({ block, index, label }: BlockProps & { label: string }) {
  const { editing } = usePf();
  if (!editing) return null;
  return (
    <BlockFrame block={block} index={index} className="pf-b-empty">
      <p className="pf-empty">
        <span className="pf-label">{label}</span>
        <span className="pf-meta">Nothing to show here yet.</span>
      </p>
    </BlockFrame>
  );
}

/* ───────────────────────── slots (copy text, editable in place) ───────────────────────── */

const FIXED_SLOTS = new Set(['tagline', 'aboutLede', 'aboutBody']);

/**
 * The editor's textarea for a slot, when the owner has this block selected and the host passed
 * `editing.renderSlotEditor`; otherwise `null` (render the plain text).
 */
export function useSlotEditor(slotId: string): ReactNode | null {
  const { content, editing } = usePf();
  const blockId = useContext(BlockIdCtx);
  if (!editing?.renderSlotEditor || !blockId || editing.selectedBlockId !== blockId) return null;
  if (!FIXED_SLOTS.has(slotId) && !slot(content, slotId)) return null;
  return <div className="pf-slot-edit">{editing.renderSlotEditor(slotId, slotText(content, slotId))}</div>;
}

interface SlotProps {
  id: string;
  /** Wording to show when the slot is empty (always a fact field, never made up). */
  fallback?: string;
  as?: 'p' | 'span' | 'div';
  className?: string;
}

/** A copy slot rendered as text, or as the editor's textarea in place of the text. */
export function Slot({ id, fallback = '', as: Tag = 'p', className }: SlotProps) {
  const { content } = usePf();
  const editor = useSlotEditor(id);
  if (editor) return <>{editor}</>;
  const text = slotText(content, id).trim() || fallback;
  if (!text) return null;
  return <Tag className={className}>{text}</Tag>;
}

/* ───────────────────────── links ───────────────────────── */

interface ExtLinkProps {
  url: string | undefined;
  /** Link text; defaults to the display form of the URL. */
  label?: string;
  className?: string;
  ariaLabel?: string;
}

/** A real link, only for http(s) and mailto URLs (anything else renders nothing). */
export function ExtLink({ url, label, className, ariaLabel }: ExtLinkProps) {
  const href = safeHref(url);
  if (!href) return null;
  const external = !/^mailto:/i.test(href);
  return (
    <a
      className={cx('pf-link', className)}
      href={href}
      aria-label={ariaLabel}
      {...(external ? { target: '_blank', rel: 'noopener noreferrer' } : null)}
    >
      <span className="pf-link-text">
        {label ?? displayUrl(href)}
        <span aria-hidden="true"> ↗</span>
      </span>
    </a>
  );
}

/* ───────────────────────── long lists ───────────────────────── */

export const LIST_LIMIT = 6;

/** Lists collapse after 6 items behind a "+n more" button (the editor always shows everything). */
export function useLimit<T>(items: T[], limit = LIST_LIMIT) {
  const { editing } = usePf();
  const [open, setOpen] = useState(false);
  const all = open || !!editing || items.length <= limit;
  const visible = all ? items : items.slice(0, limit);
  return { visible, hidden: items.length - visible.length, open, toggle: () => setOpen((o) => !o), collapsible: items.length > limit && !editing };
}

export function MoreButton({ hidden, open, toggle, collapsible }: ReturnType<typeof useLimit>) {
  if (!collapsible) return null;
  return (
    <button type="button" className="pf-more" onClick={toggle} aria-expanded={open}>
      {open ? 'Show fewer' : `+${hidden} more`}
    </button>
  );
}

/* ───────────────────────── contact facts ───────────────────────── */

export interface ContactItem {
  key: string;
  label: string;
  /** Link target (http, https or mailto); phone numbers carry none and render as text. */
  href?: string;
  text: string;
}

const EMAIL = /e-?mail/i;
const PHONE = /phone|mobile|\btel\b|call|whatsapp/i;

/** Links the person gave, plus email / phone only when `spec.privacy` allows each. */
export function contactItems(content: PortfolioContent, spec: PortfolioSpec): ContactItem[] {
  const out: ContactItem[] = [];
  const seen = new Set<string>();
  for (const f of factsOf(content, 'link')) {
    const href = safeHref(f.url);
    if (!href || seen.has(href)) continue;
    seen.add(href);
    out.push({ key: f.id, label: f.title, href, text: displayUrl(href) });
  }
  for (const f of factsOf(content, 'contact')) {
    const href = safeHref(f.url);
    const isEmail = /^mailto:/i.test(href ?? '') || EMAIL.test(f.title);
    const isPhone = !isEmail && PHONE.test(f.title);
    if (isEmail && spec.privacy.email) {
      const text = href ? displayUrl(href) : (f.detail ?? f.sourceRefs[0]?.quote ?? f.title);
      out.push({ key: f.id, label: f.title, href, text });
    } else if (isPhone && spec.privacy.phone) {
      out.push({ key: f.id, label: f.title, text: f.detail ?? f.sourceRefs[0]?.quote ?? f.title });
    }
  }
  return out;
}

/* ───────────────────────── does a block have anything to show ───────────────────────── */

/** The public page leaves a block out when this is false; the editor shows a prompt instead. */
export function blockHasContent(kind: BlockKind, content: PortfolioContent, spec: PortfolioSpec, graph: GraphSnapshot): boolean {
  switch (kind) {
    case 'hero':
      return true;
    case 'graph':
      return shownDomains(graph, spec).length > 0;
    case 'proof':
      return content.stats.length > 0;
    case 'about':
      return slotText(content, 'aboutLede').trim() !== '' || slotText(content, 'aboutBody').trim() !== '';
    case 'experience':
      return factsOf(content, 'experience').length > 0;
    case 'projects':
      return factsOf(content, 'project').length > 0;
    case 'clips':
      return factsOf(content, 'clip').length > 0;
    case 'achievements':
      return factsOf(content, 'achievement').length > 0;
    case 'education':
      return factsOf(content, 'education').length > 0;
    case 'openTo':
      return content.openTo.length > 0;
    case 'contact':
      return contactItems(content, spec).length > 0;
  }
}

/* ───────────────────────── small formatters ───────────────────────── */

/** "Bengaluru" from "Bengaluru, India". */
export const cityOf = (location: string | undefined): string => (location ?? '').split(',')[0].trim();

/** "2.4" / "11". */
export const fmtYears = (n: number): string => (Number.isInteger(n) ? String(n) : n.toFixed(1));

/** Earliest `start` among facts, as stated ("2024-05"), for "since" lines. */
export function earliestStart(facts: Fact[]): string | undefined {
  return facts
    .map((f) => f.start)
    .filter((s): s is string => !!s)
    .sort()[0];
}
