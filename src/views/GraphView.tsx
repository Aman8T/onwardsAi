/**
 * The graph screen: header + two stacked faces that crossfade.
 * Now = the current-role skill radar, Next = the next-roles role radar. Both faces stay
 * mounted in one grid cell; the header toggle or the "f" key swaps which one is in front
 * (opacity and a hair of scale, 500ms). The face that is away goes `.is-off` once the
 * fade is done so it never sizes the stack, and is `inert` the whole time.
 */
import { useReducedMotion } from 'motion/react';
import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import type { Profile } from '../../shared/types';
import { actions, getState, useApp } from '../store';
import { GraphHeader } from './graph/GraphHeader';
import { NextGraph } from './graph/NextGraph';
import { NowGraph } from './graph/NowGraph';
import '../styles/shell.css';

type Face = 'now' | 'next';

const faceOf = (flipped: boolean): Face => (flipped ? 'next' : 'now');
const otherFace = (face: Face): Face => (face === 'now' ? 'next' : 'now');

/** The whole-screen crossfade length, read from --d-screen so CSS stays the single source (500ms). */
function screenDuration(): number {
  try {
    const raw = getComputedStyle(document.documentElement).getPropertyValue('--d-screen').trim();
    const value = parseFloat(raw);
    if (Number.isFinite(value)) return raw.endsWith('ms') ? value : raw.endsWith('s') ? value * 1000 : 500;
  } catch {
    /* no DOM style access — fall through to the token's own value */
  }
  return 500;
}

function isTypingTarget(t: EventTarget | null): boolean {
  if (!(t instanceof HTMLElement)) return false;
  return t.isContentEditable || !!t.closest('input, textarea, select, [contenteditable=""], [contenteditable="true"]');
}

export function GraphView({ profile }: { profile: Profile }) {
  const flipped = useApp((s) => s.flipped);
  const reduced = useReducedMotion() ?? false;

  const stack = useRef<HTMLElement>(null);
  const faces = useRef<Record<Face, HTMLElement | null>>({ now: null, next: null });
  // Set by the "f" key when focus was inside the face that is about to go inert.
  const focusIncoming = useRef(false);
  const timer = useRef<number | undefined>(undefined);
  const lastFace = useRef<Face>(faceOf(flipped));
  // Which face is visually in front; trails the store by the one beat the incoming face needs to take its start pose.
  const [shown, setShown] = useState<Face>(faceOf(flipped));
  // The incoming face while it holds its start pose (data-enter), before the fade is released.
  const [entering, setEntering] = useState<Face | null>(null);
  // The face that is away and fully faded: out of flow and hidden (.is-off).
  const [off, setOff] = useState<Face | null>(otherFace(faceOf(flipped)));

  // The store flipped: bring the incoming face back into flow, hold its start pose, then release the fade.
  useLayoutEffect(() => {
    const target = faceOf(flipped);
    if (lastFace.current === target) return;
    lastFace.current = target;
    window.clearTimeout(timer.current);
    // A face that is still fading out reverses smoothly; only a fully hidden one needs its start pose.
    const fromHidden = off === target;
    setOff(null);
    if (fromHidden && !reduced) setEntering(target);
    else {
      setEntering(null);
      setShown(target);
    }
    timer.current = window.setTimeout(() => setOff(otherFace(target)), reduced ? 0 : screenDuration());
  }, [flipped, reduced]);

  // The start pose is committed (style flushed with transitions off), so the release below runs a real transition.
  useLayoutEffect(() => {
    if (!entering) return;
    stack.current?.getBoundingClientRect();
    setEntering(null);
    setShown(entering);
  }, [entering]);

  // The face in front changed: when the key flip left focus in a face that is now inert, land on
  // the incoming face (a labelled region; the next Tab goes to its first control).
  useLayoutEffect(() => {
    if (!focusIncoming.current) return;
    focusIncoming.current = false;
    faces.current[shown]?.focus({ preventScroll: true });
  }, [shown]);

  // No timer outlives the view.
  useEffect(() => () => window.clearTimeout(timer.current), []);

  // "f" flips the graph (unless the user is typing somewhere, or a menu or dialog has the keyboard).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'f' && e.key !== 'F') return;
      if (e.metaKey || e.ctrlKey || e.altKey || e.repeat || e.defaultPrevented) return;
      if (isTypingTarget(e.target)) return;
      if (e.target instanceof Element && e.target.closest('[role="dialog"], [role="menu"]')) return;
      e.preventDefault();
      focusIncoming.current = !!stack.current?.contains(document.activeElement);
      actions.setFlipped(!getState().flipped);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  return (
    <div className="gv-view">
      <GraphHeader profile={profile} />

      {/* Says which face is in front for screen readers; the header toggle only announces when it has focus. */}
      <p className="sr-only" aria-live="polite">
        {flipped ? 'Showing next roles' : 'Showing your current role'}
      </p>

      <main ref={stack} className="gv-stack">
        <section
          ref={(el) => {
            faces.current.now = el;
          }}
          className={`gv-face${off === 'now' ? ' is-off' : ''}`}
          data-show={shown === 'now'}
          data-enter={entering === 'now' ? '' : undefined}
          inert={flipped}
          tabIndex={-1}
          aria-label="Current role: skill radar"
        >
          <NowGraph profile={profile} />
        </section>
        <section
          ref={(el) => {
            faces.current.next = el;
          }}
          className={`gv-face${off === 'next' ? ' is-off' : ''}`}
          data-show={shown === 'next'}
          data-enter={entering === 'next' ? '' : undefined}
          inert={!flipped}
          tabIndex={-1}
          aria-label="Next roles: role radar"
        >
          <NextGraph profile={profile} />
        </section>
      </main>
    </div>
  );
}
