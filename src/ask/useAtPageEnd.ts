/**
 * Is the person at the end of the page? Watches a small sentinel that the caller renders last in the page's flow, with an
 * `IntersectionObserver` (no scroll listener): the sentinel is in view when the page has been scrolled to its end, and
 * also when the page is too short to scroll, so a short page counts as "at the end".
 *
 * `settled` turns true a frame after the first reading. The dock enables its size transition only then, so the bar does
 * not animate once on load from its first guess to where the page really is.
 */
import { useEffect, useRef, useState } from 'react';
import type { RefObject } from 'react';

export function useAtPageEnd(): { sentinel: RefObject<HTMLDivElement | null>; atEnd: boolean; settled: boolean } {
  const sentinel = useRef<HTMLDivElement>(null);
  const [atEnd, setAtEnd] = useState(false);
  const [settled, setSettled] = useState(false);

  useEffect(() => {
    const el = sentinel.current;
    if (!el) return;
    // no observer (an old engine): the wide bar, which is the full one
    if (typeof IntersectionObserver === 'undefined') {
      setAtEnd(true);
      setSettled(true);
      return;
    }
    let frame = 0;
    const observer = new IntersectionObserver((entries) => {
      const latest = entries[entries.length - 1];
      if (!latest) return;
      setAtEnd(latest.isIntersecting);
      if (!frame) frame = requestAnimationFrame(() => setSettled(true));
    });
    observer.observe(el);
    return () => {
      observer.disconnect();
      if (frame) cancelAnimationFrame(frame);
    };
  }, []);

  return { sentinel, atEnd, settled };
}
