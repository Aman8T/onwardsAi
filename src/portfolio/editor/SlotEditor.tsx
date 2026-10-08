/**
 * The in-place editor for one copy slot, rendered by the portfolio blocks (via `EditHooks.renderSlotEditor`)
 * where the slot's text would be. A textarea that grows with its text and inherits the slot's typography,
 * a live `n / limit` counter that warns past the limit, a provenance line, and the two actions.
 * Edits debounce 600 ms into one `slots` patch; a pending edit is flushed when the editor goes away.
 *
 * Built from phrasing elements only (spans, textarea, buttons): the blocks render slots inside <p> and <h*>.
 */
import { useEffect, useId, useLayoutEffect, useRef, useState } from 'react';
import type { ChangeEvent, ReactNode } from 'react';
import type { CopySlot, Fact, PortfolioPatch } from '../../../shared/portfolio';
import { slotLimit } from '../model';
import { looksLikeMarkup } from './labels';

export const SLOT_DEBOUNCE_MS = 600;
const EXP_MAX_LINES = 3;
const TITLE_MAX = 48;

const chars = (s: string): number => Array.from(s).length;

/** Why this text cannot be saved yet, or null. */
export function slotProblem(slotId: string, value: string): string | null {
  const limit = slotLimit(slotId);
  const n = chars(value);
  if (limit !== undefined && n > limit) {
    const over = n - limit;
    return `${over} ${over === 1 ? 'character' : 'characters'} over the limit. Shorten it and it saves.`;
  }
  if (looksLikeMarkup(value)) return 'Plain text only. Remove the tags and it saves.';
  if (slotId.startsWith('exp:') && value.split('\n').filter((l) => l.trim()).length > EXP_MAX_LINES) {
    return `Up to ${EXP_MAX_LINES} lines, one per bullet. Remove a line and it saves.`;
  }
  return null;
}

const SLOT_NAMES: Record<string, string> = {
  tagline: 'Tagline',
  aboutLede: 'About lede',
  aboutBody: 'About text',
  pressHeadline: 'Newspaper headline',
  pressDeck: 'Newspaper summary line',
};

function slotName(slotId: string, facts: Fact[]): string {
  if (SLOT_NAMES[slotId]) return SLOT_NAMES[slotId];
  const [prefix, rest] = slotId.split(':', 2);
  const fact = rest ? facts.find((f) => f.id === rest) : undefined;
  if (prefix === 'exp') return fact ? `Bullets for ${fact.title}` : 'Experience bullets';
  if (prefix === 'proj') return fact ? `Description of ${fact.title}` : 'Project description';
  return slotId;
}

const clip = (s: string) => (s.length > TITLE_MAX ? `${s.slice(0, TITLE_MAX - 1).trimEnd()}…` : s);

/** Titles of the facts a slot cites, with the employer or institution when the fact has one. */
function groundedIn(slot: CopySlot | undefined, facts: Fact[]): string[] {
  if (!slot) return [];
  const titles: string[] = [];
  for (const id of slot.factIds) {
    const f = facts.find((x) => x.id === id);
    if (!f) continue;
    const t = clip(f.org ? `${f.title}, ${f.org}` : f.title);
    if (!titles.includes(t)) titles.push(t);
  }
  return titles;
}

export interface SlotEditorProps {
  slotId: string;
  /** The slot's text as the portfolio holds it now. */
  text: string;
  slot?: CopySlot;
  facts: Fact[];
  /** A rewrite or another long action is running; the buttons wait. */
  busy: boolean;
  onPatch(patch: PortfolioPatch): void;
  onRewrite(slotId: string): void;
}

export function SlotEditor({ slotId, text, slot, facts, busy, onPatch, onRewrite }: SlotEditorProps): ReactNode {
  const uid = useId();
  const limit = slotLimit(slotId);
  const [draft, setDraft] = useState(text);
  const [touched, setTouched] = useState(false);
  const [saving, setSaving] = useState(false);
  const area = useRef<HTMLTextAreaElement>(null);
  const timer = useRef<number | null>(null);
  /** Texts this editor sent: when one comes back as the slot's text it is an echo, not a new wording. */
  const sent = useRef(new Set<string>());
  const live = useRef({ draft, text, slotId, onPatch });

  useLayoutEffect(() => {
    live.current = { draft, text, slotId, onPatch };
  });

  const send = (value: string) => {
    const l = live.current;
    setSaving(false);
    if (value === l.text || slotProblem(l.slotId, value)) return;
    sent.current.add(value);
    l.onPatch({ slots: [{ id: l.slotId, text: value }] });
  };

  const cancel = () => {
    if (timer.current !== null) window.clearTimeout(timer.current);
    timer.current = null;
    setSaving(false);
  };

  // New wording from elsewhere (a rewrite, a reset, a regenerate) replaces the draft; our own echo does not.
  useEffect(() => {
    if (text === live.current.draft || sent.current.has(text)) return;
    sent.current.clear();
    setDraft(text);
  }, [text]);

  // Leaving the block (or the page) must not drop an edit that is still waiting for its debounce.
  useEffect(
    () => () => {
      if (timer.current === null) return;
      window.clearTimeout(timer.current);
      timer.current = null;
      const l = live.current;
      if (l.draft !== l.text && !slotProblem(l.slotId, l.draft)) l.onPatch({ slots: [{ id: l.slotId, text: l.draft }] });
    },
    [],
  );

  // The textarea is exactly as tall as its text, also when the preview is resized.
  useLayoutEffect(() => {
    const el = area.current;
    if (!el) return;
    const fit = () => {
      el.style.height = 'auto';
      el.style.height = `${el.scrollHeight}px`;
    };
    fit();
    if (typeof ResizeObserver === 'undefined') return;
    let width = el.clientWidth;
    const observer = new ResizeObserver(() => {
      if (el.clientWidth === width) return;
      width = el.clientWidth;
      fit();
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, [draft]);

  const onChange = (e: ChangeEvent<HTMLTextAreaElement>) => {
    const value = e.target.value;
    setDraft(value);
    setTouched(true);
    if (timer.current !== null) window.clearTimeout(timer.current);
    setSaving(!slotProblem(slotId, value) && value !== text);
    timer.current = window.setTimeout(() => {
      timer.current = null;
      send(value);
    }, SLOT_DEBOUNCE_MS);
  };

  // Both actions replace the wording, so an edit still waiting for its debounce is dropped and the editor
  // shows the saved wording until the new one arrives (and keeps showing it if the action fails).
  const rewrite = () => {
    cancel();
    sent.current.clear();
    setTouched(false);
    setDraft(text);
    onRewrite(slotId);
  };

  const reset = () => {
    cancel();
    sent.current.clear();
    setTouched(false);
    setDraft(text);
    onPatch({ resetSlots: [slotId] });
  };

  const n = chars(draft);
  const problem = slotProblem(slotId, draft);
  const over = limit !== undefined && n > limit;
  const edited = touched || slot?.origin === 'user';
  const titles = edited ? [] : groundedIn(slot, facts);
  const name = slotName(slotId, facts);
  const isExp = slotId.startsWith('exp:');

  return (
    <span className="pe-slot" data-over={over || undefined}>
      <span className="pe-slot-top">
        <span className="pe-slot-chips">
          <span className="pe-chip">Editing</span>
          {saving && <span className="pe-slot-saving">Saving…</span>}
        </span>
        <span id={`${uid}-count`} className="pe-count numeral" data-over={over || undefined}>
          {n}
          {limit !== undefined ? ` / ${limit}` : ''}
        </span>
      </span>
      <textarea
        ref={area}
        className="pe-slot-area"
        rows={1}
        value={draft}
        spellCheck
        aria-label={`${name}, editable`}
        aria-describedby={`${uid}-count ${uid}-note`}
        aria-invalid={problem ? true : undefined}
        aria-busy={busy || undefined}
        onChange={onChange}
      />
      <span id={`${uid}-note`} className="pe-slot-note">
        {problem ? (
          <span role="status" className="pe-slot-warn">
            {problem}
          </span>
        ) : edited ? (
          <span>Edited by you</span>
        ) : titles.length > 0 ? (
          <span>
            <span className="pe-slot-strong">Grounded in:</span> {titles.join(' · ')}
          </span>
        ) : slot?.origin === 'resume' ? (
          <span>Wording taken from your resume</span>
        ) : null}
        {isExp && !problem && <span className="pe-slot-hint"> One line per bullet, up to {EXP_MAX_LINES}.</span>}
      </span>
      <span className="pe-slot-actions">
        <button type="button" className="pe-solid" disabled={busy} onClick={rewrite}>
          Rewrite this section
        </button>
        <button type="button" className="pe-quiet" disabled={busy || !edited} onClick={reset}>
          Reset to generated wording
        </button>
      </span>
    </span>
  );
}
