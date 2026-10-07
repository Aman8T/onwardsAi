import { useEffect, useId, useRef, useState } from 'react';
import type { KeyboardEvent } from 'react';
import { CYCLE_MINUTES, useAtmoMode } from '../theme/ambient';
import type { AtmoMode } from '../theme/ambient';

interface Option {
  mode: AtmoMode;
  label: string;
  hint: string;
}

const OPTIONS: readonly Option[] = [
  { mode: 'drift', label: 'Drift', hint: `colour travels the spectrum, one circle every ${CYCLE_MINUTES} minutes` },
  { mode: 'still', label: 'Still', hint: 'a single calm colour' },
  { mode: 'off', label: 'Off', hint: 'neutral greys' },
];

/** The word after "Atmosphere": what the field is doing right now. */
function stateWord(chosen: AtmoMode, effective: AtmoMode): string {
  if (chosen === 'drift' && effective === 'still') return 'Still · system';
  if (effective === 'drift') return 'Drifting';
  return effective === 'off' ? 'Off' : 'Still';
}

/**
 * Drift / Still / Off control for the living gradient. A menu button with
 * three radio items: arrows move, Escape closes and refocuses the button,
 * a pointer outside closes, choosing applies the mode and closes.
 */
export function Atmosphere({ className }: { className?: string }) {
  const [chosen, effective, setMode] = useAtmoMode();
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const itemRefs = useRef<(HTMLButtonElement | null)[]>([]);
  const menuId = useId();

  // A pointer anywhere outside the control closes the menu.
  useEffect(() => {
    if (!open) return;
    const onPointerDown = (e: PointerEvent) => {
      if (!wrapRef.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('pointerdown', onPointerDown);
    return () => document.removeEventListener('pointerdown', onPointerDown);
  }, [open]);

  // Opening moves focus onto the checked item.
  useEffect(() => {
    if (!open) return;
    const index = Math.max(0, OPTIONS.findIndex((o) => o.mode === chosen));
    itemRefs.current[index]?.focus();
  }, [open, chosen]);

  const close = (refocus: boolean) => {
    setOpen(false);
    if (refocus) buttonRef.current?.focus();
  };

  const choose = (mode: AtmoMode) => {
    setMode(mode);
    close(true);
  };

  const onButtonKeyDown = (e: KeyboardEvent<HTMLButtonElement>) => {
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      setOpen(true);
    }
  };

  const onMenuKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const items = itemRefs.current.filter((el): el is HTMLButtonElement => el !== null);
    const current = items.indexOf(document.activeElement as HTMLButtonElement);
    const focusAt = (i: number) => items[(i + items.length) % items.length]?.focus();
    switch (e.key) {
      case 'ArrowDown':
        e.preventDefault();
        focusAt(current < 0 ? 0 : current + 1);
        break;
      case 'ArrowUp':
        e.preventDefault();
        focusAt(current < 0 ? items.length - 1 : current - 1);
        break;
      case 'Home':
        e.preventDefault();
        focusAt(0);
        break;
      case 'End':
        e.preventDefault();
        focusAt(items.length - 1);
        break;
      case 'Escape':
        e.preventDefault();
        close(true);
        break;
      case 'Tab':
        // Let focus move on naturally; the menu simply goes away.
        setOpen(false);
        break;
    }
  };

  return (
    <div ref={wrapRef} className={className ? `atmo ${className}` : 'atmo'}>
      <button
        ref={buttonRef}
        type="button"
        className="btn btn-quiet atmo-btn"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        onClick={() => setOpen((o) => !o)}
        onKeyDown={onButtonKeyDown}
      >
        Atmosphere <span className="atmo-state">{stateWord(chosen, effective)}</span>
      </button>
      {open && (
        <div id={menuId} role="menu" aria-label="Atmosphere" className="atmo-menu" onKeyDown={onMenuKeyDown}>
          {OPTIONS.map((option, i) => (
            <button
              key={option.mode}
              ref={(el) => {
                itemRefs.current[i] = el;
              }}
              type="button"
              role="menuitemradio"
              aria-checked={chosen === option.mode}
              className="atmo-item"
              tabIndex={-1}
              onClick={() => choose(option.mode)}
            >
              <span>{option.label}</span>
              <span className="atmo-hint">{option.hint}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
