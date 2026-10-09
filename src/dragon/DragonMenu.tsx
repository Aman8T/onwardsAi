/**
 * The dragon's chip and menu, bottom-right on Home: "Dragon · fledgling · 2:14" opening Awake / Asleep,
 * the species, Start over, and the credits. Same shape and keyboard pattern as the Atmosphere control
 * (arrows move, Escape closes and refocuses, a pointer outside closes). No three.js is loaded by this file.
 */
import { useEffect, useId, useRef, useState } from 'react';
import type { KeyboardEvent } from 'react';
import { useMediaQuery } from '../lib/ui';
import { ageLabel } from './growth';
import { SPECIES } from './species';
import { dragonState, sendDragonCommand, useDragon } from './state';
import type { DragonMode } from './state';

export function DragonMenu() {
  const wide = useMediaQuery('(min-width: 760px)');
  const reduced = useMediaQuery('(prefers-reduced-motion: reduce)');
  const snap = useDragon();
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const itemRefs = useRef<(HTMLButtonElement | null)[]>([]);
  const menuId = useId();

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (e: PointerEvent) => {
      if (!wrapRef.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('pointerdown', onPointerDown);
    return () => document.removeEventListener('pointerdown', onPointerDown);
  }, [open]);

  useEffect(() => {
    if (open) itemRefs.current[0]?.focus();
  }, [open]);

  if (!wide || reduced) return null;

  const close = (refocus: boolean) => {
    setOpen(false);
    if (refocus) buttonRef.current?.focus();
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
      case 'Escape':
        e.preventDefault();
        close(true);
        break;
      case 'Tab':
        setOpen(false);
        break;
    }
  };

  const setMode = (mode: DragonMode) => {
    dragonState.setMode(mode);
    close(true);
  };

  const stageWord = snap.mode === 'asleep' ? 'asleep' : snap.stage;
  let i = 0;
  const bind = (el: HTMLButtonElement | null, index: number) => {
    itemRefs.current[index] = el;
  };

  return (
    <div ref={wrapRef} className="dragon-ctl">
      <button
        ref={buttonRef}
        type="button"
        className="btn dragon-chip"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        onClick={() => setOpen((o) => !o)}
        onKeyDown={(e) => {
          if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
            e.preventDefault();
            setOpen(true);
          }
        }}
      >
        Dragon <span className="dragon-state">· {stageWord}{snap.mode === 'awake' && snap.stage !== 'egg' ? ` · ${ageLabel(snap.age)}` : ''}</span>
      </button>
      {open && (
        <div id={menuId} role="menu" aria-label="Dragon" className="atmo-menu" onKeyDown={onMenuKeyDown}>
          {(['awake', 'asleep'] as const).map((mode) => {
            const index = i++;
            return (
              <button
                key={mode}
                ref={(el) => bind(el, index)}
                type="button"
                role="menuitemradio"
                aria-checked={snap.mode === mode}
                className="atmo-item"
                tabIndex={-1}
                onClick={() => setMode(mode)}
              >
                <span>{mode === 'awake' ? 'Awake' : 'Asleep'}</span>
                <span className="atmo-hint">{mode === 'awake' ? 'flies, hunts, grows' : 'gone from the page; it remembers its age'}</span>
              </button>
            );
          })}
          {Object.values(SPECIES).map((sp) => {
            const index = i++;
            return (
              <button
                key={sp.id}
                ref={(el) => bind(el, index)}
                type="button"
                role="menuitemradio"
                aria-checked={snap.species === sp.id}
                className="atmo-item"
                tabIndex={-1}
                onClick={() => {
                  sendDragonCommand({ kind: 'species', id: sp.id });
                  close(true);
                }}
              >
                <span>Species · {sp.name}</span>
                <span className="atmo-hint">{sp.credit}</span>
              </button>
            );
          })}
          <button
            ref={(el) => bind(el, i++)}
            type="button"
            role="menuitem"
            className="atmo-item"
            tabIndex={-1}
            onClick={() => {
              sendDragonCommand({ kind: 'reset' });
              close(true);
            }}
          >
            <span>Start over</span>
            <span className="atmo-hint">lays a new egg</span>
          </button>
          <p className="dragon-credit">Fire sprites: Kenney Particle Pack, CC0. Models under CC BY: see public/dragons/CREDITS.md.</p>
        </div>
      )}
    </div>
  );
}
