/**
 * The dock's markup with no state of its own: the frame, the panel it grows upward, and the bar. `AskDock` feeds it from
 * the store; the dev gallery (`dev/AskGallery.tsx`) feeds it fixtures. One component for both, so what the gallery shows
 * is what the dashboard draws.
 */
import type { ChangeEvent, FormEvent, RefObject } from 'react';
import { IconStop } from '../components/Icons';
import { DockPanel } from './DockPanel';
import type { PanelProps, Starter } from './DockPanel';
import '../styles/ask.css';

export const PLACEHOLDER = 'Ask about your graph, your roles, or how anything here works';
/** The bar's hint once the panel is open (the starters are in the panel then, not in the bar). */
export const OPEN_PLACEHOLDER = 'Ask anything about your graph or this app';

export interface BarProps {
  inputId: string;
  /** The panel's id, for `aria-controls`, while it is open. */
  panelId: string;
  open: boolean;
  text: string;
  /** An answer is on show: the input asks a follow-up. */
  following: boolean;
  /** An answer is streaming: send becomes Stop. */
  streaming: boolean;
  /** The starters that ride in the bar while it rests (the first two). */
  chips: Starter[];
  inputRef?: RefObject<HTMLInputElement | null>;
  onText: (value: string) => void;
  onSubmit: (e: FormEvent) => void;
  onFocus: () => void;
  onOpen: () => void;
  onChip: (q: string) => void;
  onStop: () => void;
}

export function DockBar({ inputId, panelId, open, text, following, streaming, chips, inputRef, onText, onSubmit, onFocus, onOpen, onChip, onStop }: BarProps) {
  const placeholder = following ? 'Ask a follow-up' : open ? OPEN_PLACEHOLDER : PLACEHOLDER;
  return (
    <form className="ak-bar" onSubmit={onSubmit}>
      <svg className="ak-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
        <path d="M12 3v4m0 10v4M3 12h4m10 0h4M6 6l2.5 2.5m7 7L18 18M6 18l2.5-2.5m7-7L18 6" />
      </svg>
      <label className="sr-only" htmlFor={inputId}>
        {following ? 'Ask a follow-up' : PLACEHOLDER}
      </label>
      <input
        ref={inputRef}
        id={inputId}
        className="ak-input"
        type="text"
        value={text}
        placeholder={placeholder}
        autoComplete="off"
        enterKeyHint="send"
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        onChange={(e: ChangeEvent<HTMLInputElement>) => onText(e.target.value)}
        onFocus={onFocus}
        onClick={onOpen}
      />
      {!open && chips.length > 0 && (
        <span className="ak-chips">
          {chips.map((s) => (
            <button key={s.q} type="button" className="ak-chip" onClick={() => onChip(s.q)}>
              {s.q}
            </button>
          ))}
        </span>
      )}
      {open && !streaming && <span className="ak-fine ak-hint">Enter to send</span>}
      {streaming ? (
        <button type="button" className="btn ak-send ak-stop" onClick={onStop}>
          <IconStop size={14} strokeWidth={1.5} />
          <span>Stop</span>
        </button>
      ) : (
        <button type="submit" className="btn btn-primary ak-send" aria-label="Send" disabled={!text.trim()}>
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
            <path d="M12 19V5m0 0-6 6m6-6 6 6" />
          </svg>
        </button>
      )}
    </form>
  );
}

export interface DockViewProps {
  open: boolean;
  /** In the page's flow instead of sticky to the window: the gallery. */
  flow?: boolean;
  frameRef?: RefObject<HTMLDivElement | null>;
  panel: PanelProps;
  bar: BarProps;
}

export function DockView({ open, flow, frameRef, panel, bar }: DockViewProps) {
  return (
    <div className={`ak-dock${open ? ' is-open' : ''}${flow ? ' is-flow' : ''}`} ref={frameRef}>
      {open && <DockPanel {...panel} />}
      <DockBar {...bar} />
    </div>
  );
}
