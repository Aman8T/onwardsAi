/**
 * The spotlight under the wake, for the quiet look in dark: a 320 px soft pool of `--spot` that follows the
 * pointer, beneath the dye (between the ambient field and the WebGL canvas). One fixed, pointer-transparent
 * div; a passive pointermove listener writes its transform straight to the element's style, so there is no
 * React state, no requestAnimationFrame and no work at all while the pointer is still. It exists only for
 * `html[data-look='quiet']` in the dark scheme, and under the same gating as the wake (fine pointer, no
 * reduced motion, forced colours, more contrast, Atmosphere not Off). Everything visual is in spot.css.
 */
import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { readDark, readLook, usePointerLayersAllowed } from './CursorLight';
import '../styles/spot.css';

const SIZE = 320;

/** Whether the root says quiet + dark now. Re-read on a change of the root's attributes or the device scheme. */
function useQuietDark(enabled: boolean): boolean {
  const [on, setOn] = useState(false);
  useEffect(() => {
    if (!enabled) {
      setOn(false);
      return;
    }
    const read = () => setOn(readLook() === 'quiet' && readDark());
    read();
    const mo = new MutationObserver(read);
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ['data-look', 'data-scheme', 'data-atmo'] });
    const mq = window.matchMedia('(prefers-color-scheme: dark)');
    mq.addEventListener('change', read);
    return () => {
      mo.disconnect();
      mq.removeEventListener('change', read);
    };
  }, [enabled]);
  return enabled && on;
}

export function Spot() {
  const allowed = usePointerLayersAllowed();
  const active = useQuietDark(allowed);
  if (!active) return null;
  return createPortal(<SpotPool />, document.body);
}

function SpotPool() {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const half = SIZE / 2;
    const onMove = (e: PointerEvent) => {
      el.style.transform = `translate3d(${e.clientX - half}px, ${e.clientY - half}px, 0)`;
      el.classList.add('is-on');
    };
    const onLeave = () => el.classList.remove('is-on');
    window.addEventListener('pointermove', onMove, { passive: true });
    document.documentElement.addEventListener('pointerleave', onLeave, { passive: true });
    return () => {
      window.removeEventListener('pointermove', onMove);
      document.documentElement.removeEventListener('pointerleave', onLeave);
    };
  }, []);

  return <div ref={ref} className="spot" aria-hidden="true" />;
}
