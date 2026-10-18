/**
 * The phone's jump bar: Stand · Move · Plan · Next, real anchors to the four blocks, the one most on screen marked.
 * Sticky at the top of the thread's scroller; a block that has not arrived yet is shown but cannot be reached.
 */
import { useEffect, useState } from 'react';
import type { MouseEvent } from 'react';
import { useReducedMotion } from 'motion/react';

export interface JumpItem {
  id: string;
  label: string;
  present: boolean;
}

export function JumpBar({ items }: { items: JumpItem[] }) {
  const reduced = useReducedMotion();
  const [current, setCurrent] = useState<string | null>(null);
  const presentKey = items
    .filter((i) => i.present)
    .map((i) => i.id)
    .join(',');

  // The block with the most of itself on screen is the current one.
  useEffect(() => {
    if (typeof IntersectionObserver === 'undefined') return;
    const els = presentKey
      .split(',')
      .filter(Boolean)
      .map((id) => document.getElementById(id))
      .filter((el): el is HTMLElement => el !== null);
    if (!els.length) return;
    const visible = new Map<string, number>();
    const io = new IntersectionObserver(
      (entries) => {
        for (const en of entries) visible.set(en.target.id, en.isIntersecting ? en.intersectionRect.height : 0);
        let best: string | null = null;
        let most = 0;
        for (const [id, h] of visible) {
          if (h > most) {
            best = id;
            most = h;
          }
        }
        setCurrent((c) => (best !== null && best !== c ? best : c));
      },
      { threshold: [0, 0.1, 0.25, 0.5, 0.75, 1] },
    );
    els.forEach((el) => io.observe(el));
    return () => io.disconnect();
  }, [presentKey]);

  const go = (e: MouseEvent<HTMLAnchorElement>, id: string, present: boolean) => {
    e.preventDefault();
    if (!present) return;
    document.getElementById(id)?.scrollIntoView({
      behavior: reduced ? 'auto' : 'smooth',
      block: 'start',
    });
  };

  return (
    <nav className="pp-jump" aria-label="Blocks of this answer">
      {items.map((it) => (
        <a
          key={it.id}
          href={`#${it.id}`}
          className={`pp-jump-a${current === it.id ? ' is-on' : ''}`}
          aria-current={current === it.id ? 'true' : undefined}
          aria-disabled={it.present ? undefined : 'true'}
          onClick={(e) => go(e, it.id, it.present)}
        >
          {it.label}
        </a>
      ))}
    </nav>
  );
}
