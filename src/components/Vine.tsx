/**
 * The vine: a stem along the page's edges with leaves that unfurl as the page scrolls ("leaf out"). Mounted once in
 * App, fixed behind the view. One passive scroll listener writes --vine-grow at most once a frame; nothing runs while
 * the page is still. Decoration only: aria-hidden, no pointer events.
 */
import { useEffect, useRef } from 'react';
import type { CSSProperties } from 'react';
import '../styles/vine.css';

/** [x %, y %, rotation °, hue offset °], kept near the edges so they sit beside the content, not behind it. */
const LEAVES: [number, number, number, number][] = [
  [4, 9, -30, 0], [2.5, 22, 200, 40], [7, 31, -55, 80], [3, 47, 190, 120], [6, 63, -20, 160], [2.5, 79, 210, 200], [6, 92, -40, 240],
  [96, 14, 150, 30], [93, 27, -20, 70], [97, 41, 170, 110], [94, 56, 10, 150], [97, 70, 200, 190], [93, 85, -60, 230], [96, 97, 160, 270],
];
const ORDERED = [...LEAVES].sort((a, b) => a[1] - b[1]);

export function Vine() {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    let raf = 0;
    const update = () => {
      raf = 0;
      const max = document.documentElement.scrollHeight - window.innerHeight;
      const p = max > 40 ? window.scrollY / max : 0;
      el.style.setProperty('--vine-grow', (0.12 + 0.88 * Math.min(1, Math.max(0, p))).toFixed(3));
    };
    const onScroll = () => {
      if (!raf) raf = requestAnimationFrame(update);
    };
    update();
    window.addEventListener('scroll', onScroll, { passive: true });
    window.addEventListener('resize', onScroll);
    return () => {
      window.removeEventListener('scroll', onScroll);
      window.removeEventListener('resize', onScroll);
      if (raf) cancelAnimationFrame(raf);
    };
  }, []);
  return (
    <div ref={ref} className="vine" aria-hidden="true">
      <svg className="vine-stem" viewBox="0 0 100 100" preserveAspectRatio="none">
        <path pathLength={1} d="M4 0 C 1 8, 8 14, 5 22 S 1 38, 6 46 S 2 62, 5 70 S 2 86, 5 100" />
        <path pathLength={1} d="M96 0 C 99 9, 92 16, 95 25 S 99 40, 94 50 S 98 66, 95 74 S 98 90, 95 100" />
      </svg>
      {ORDERED.map(([x, y, r, o], k) => (
        <svg
          key={k}
          className="vine-leaf"
          viewBox="0 0 64 40"
          style={{ left: `${x}%`, top: `${y}%`, '--leaf-t': (k / ORDERED.length) * 0.9 + 0.08, '--leaf-r': `${r}deg`, '--leaf-o': o } as CSSProperties}
        >
          <path className="blade" d="M4 20 C 16 2, 44 2, 62 20 C 44 38, 16 38, 4 20 Z" />
          <path className="rib" d="M4 20 C 24 18, 44 18, 60 20" />
        </svg>
      ))}
    </div>
  );
}
