import { useEffect, useId, useRef, useState } from 'react';
import type { KeyboardEvent } from 'react';
import { CYCLE_MINUTES, useAtmoMode, useScheme } from '../theme/ambient';
import { SchemeToggle } from './SchemeToggle';
import type { AtmoMode, SchemeChoice } from '../theme/ambient';

interface Option<T extends string> {
  value: T;
  label: string;
  hint: string;
}

const MODES: readonly Option<AtmoMode>[] = [
  { value: 'drift', label: 'Drift', hint: `colour travels the spectrum, one circle every ${CYCLE_MINUTES} minutes` },
  { value: 'still', label: 'Still', hint: 'a single calm colour' },
  { value: 'off', label: 'Off', hint: 'neutral greys' },
];

const SCHEMES: readonly Option<SchemeChoice>[] = [
  { value: 'system', label: 'System', hint: 'light or dark, as your device is' },
  { value: 'light', label: 'Light', hint: 'always the light field' },
  { value: 'dark', label: 'Dark', hint: 'always the dark field' },
];

/** The word after "Atmosphere": what the field is doing right now. */
function stateWord(chosen: AtmoMode, effective: AtmoMode): string {
  if (chosen === 'drift' && effective === 'still') return 'Still · system';
  if (effective === 'drift') return 'Drifting';
  return effective === 'off' ? 'Off' : 'Still';
}

/**
 * The Atmosphere control: Drift / Still / Off for the living gradient and System / Light / Dark
 * for the scheme, in one menu as two groups. A menu button with radio items: arrows move across
 * both groups, Escape closes and refocuses the button, a pointer outside closes, choosing applies
 * the value and closes.
 */
export function Atmosphere({ className }: { className?: string }) {
  const [chosen, effective, setMode] = useAtmoMode();
  const [scheme, , setScheme] = useScheme();
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

  // Opening moves focus onto the checked mode.
  useEffect(() => {
    if (!open) return;
    const index = Math.max(0, MODES.findIndex((o) => o.value === chosen));
    itemRefs.current[index]?.focus();
  }, [open, chosen]);

  const close = (refocus: boolean) => {
    setOpen(false);
    if (refocus) buttonRef.current?.focus();
  };

  const chooseMode = (mode: AtmoMode) => {
    setMode(mode);
    close(true);
  };

  const chooseScheme = (choice: SchemeChoice) => {
    setScheme(choice);
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

  // Every item of both groups in one roving list: the modes first, then the schemes.
  const item = <T extends string>(option: Option<T>, index: number, checked: boolean, choose: (value: T) => void) => (
    <button
      key={option.value}
      ref={(el) => {
        itemRefs.current[index] = el;
      }}
      type="button"
      role="menuitemradio"
      aria-checked={checked}
      className="atmo-item"
      tabIndex={-1}
      onClick={() => choose(option.value)}
    >
      <span>{option.label}</span>
      <span className="atmo-hint">{option.hint}</span>
    </button>
  );

  return (
    <div ref={wrapRef} className={className ? `atmo ${className}` : 'atmo'}>
      <SchemeToggle />
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
        <span className="atmo-word">Atmosphere</span> <span className="atmo-state">{stateWord(chosen, effective)}</span>
      </button>
      {open && (
        <div id={menuId} role="menu" aria-label="Atmosphere" className="atmo-menu" onKeyDown={onMenuKeyDown}>
          <div role="group" aria-label="Field" className="atmo-group">
            {MODES.map((option, i) => item(option, i, chosen === option.value, chooseMode))}
          </div>
          <div role="separator" className="atmo-sep" />
          <div role="group" aria-label="Appearance" className="atmo-group">
            <span className="kicker atmo-group-label" aria-hidden="true">
              Appearance
            </span>
            {SCHEMES.map((option, i) => item(option, MODES.length + i, scheme === option.value, chooseScheme))}
          </div>
        </div>
      )}
    </div>
  );
}
