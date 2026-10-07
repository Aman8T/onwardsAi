/**
 * Chat view — the calm theme (CalmChat board). A quiet rail (where the thread started, what
 * Tayyari can see, threads) next to a streaming conversation on the field: the person's words
 * as a block on the second surface tone, Tayyari's answers as plain text under a "Tayyari"
 * kicker, and a panel composer under the suggestion chips.
 */
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { AnimatePresence, motion } from 'motion/react';
import type { Profile } from '../../shared/types';
import { actions, useApp } from '../store';
import { freeAction } from '../lib/prompts';
import { EASE, initials, useNow } from '../lib/ui';
import { Brand } from '../components/Brand';
import { AssistantMessage, UserMessage } from './chat/Message';
import { Composer } from './chat/Composer';
import { Rail } from './chat/Rail';
import { ThreadHead } from './chat/ThreadHead';
import { collapseRetries, sourcesPhrase, statusLines, suggestionsFor } from './chat/helpers';
import '../styles/chat.css';

const FREE_ORIGIN = freeAction().origin;
const NEAR_BOTTOM = 64;

function Suggestions({ items, onPick, hero = false }: { items: string[]; onPick: (t: string) => void; hero?: boolean }) {
  return (
    <div className={`ch-suggs${hero ? ' ch-suggs--hero' : ''}`} role="group" aria-label="Suggested questions">
      {items.map((t) => (
        <button key={t} type="button" className="ch-sugg" onClick={() => onPick(t)}>
          {t}
        </button>
      ))}
    </div>
  );
}

export function ChatView({ profile }: { profile: Profile }) {
  const threads = useApp((s) => s.threads);
  const activeId = useApp((s) => s.activeThreadId);
  const now = useNow(30_000);

  // activeThreadId can be null/stale (fresh load, deleted thread): fall back to the most recent.
  const thread = useMemo(() => threads.find((t) => t.id === activeId) ?? threads[0] ?? null, [threads, activeId]);
  const origin = thread?.origin ?? FREE_ORIGIN;

  const okSources = useMemo(() => profile.sources.filter((s) => s.ok), [profile.sources]);
  const lines = useMemo(() => statusLines(profile), [profile]);
  const footnote = useMemo(() => sourcesPhrase(profile), [profile]);

  const rawMessages = thread?.messages;
  const messages = useMemo(() => collapseRetries(rawMessages ?? []), [rawMessages]);
  const streaming = !!rawMessages?.some((m) => m.status === 'streaming');
  const lastUserText = useMemo(() => [...(rawMessages ?? [])].reverse().find((m) => m.role === 'user')?.text, [rawMessages]);
  const firstUserId = messages.find((m) => m.role === 'user')?.id;
  const threadId = thread?.id;
  const empty = messages.length === 0;

  /* ───────────── draft (kept per thread) ───────────── */

  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const draftKey = threadId ?? '_new';
  const draft = drafts[draftKey] ?? '';
  const setDraft = useCallback((v: string) => setDrafts((d) => ({ ...d, [draftKey]: v })), [draftKey]);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);

  useEffect(() => {
    // Autofocus only where a keyboard is likely; on touch it would pop the keyboard open.
    if (window.matchMedia?.('(pointer: fine)').matches) inputRef.current?.focus();
  }, []);

  /* ───────────── scrolling ───────────── */

  const scrollRef = useRef<HTMLDivElement | null>(null);
  const flowRef = useRef<HTMLDivElement | null>(null);
  /** True while the view follows the newest text. Only a user gesture turns it off. */
  const stick = useRef(true);
  const [away, setAway] = useState(false);
  /** An empty thread shows the hero: anchor it to the top (on a short phone its heading would scroll out of view). */
  const emptyRef = useRef(empty);
  useLayoutEffect(() => {
    emptyRef.current = empty;
  }, [empty]);

  useEffect(() => {
    const el = scrollRef.current;
    const flow = flowRef.current;
    if (!el || !flow) return;
    const gap = () => el.scrollHeight - el.scrollTop - el.clientHeight;

    // A big block (table, code) can land between two frames and widen the gap without the
    // user scrolling, so a scroll event alone must not detach us. Only gestures do.
    let lastWheel = 0;
    let touching = false;
    let dragging = false;
    const gesture = () => touching || dragging || performance.now() - lastWheel < 220;

    const onWheel = (e: WheelEvent) => {
      lastWheel = performance.now();
      if (e.deltaY < 0) stick.current = false;
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'ArrowUp' || e.key === 'PageUp' || e.key === 'Home') stick.current = false;
    };
    const onTouchStart = () => {
      touching = true;
    };
    const onTouchEnd = () => {
      touching = false;
    };
    const onPointerDown = (e: PointerEvent) => {
      // pressing the scrollbar itself (not content) starts a drag-scroll
      if (e.pointerType === 'mouse' && e.target === el && e.offsetX >= el.clientWidth) dragging = true;
    };
    const onPointerUp = () => {
      dragging = false;
    };
    const onScroll = () => {
      const near = gap() < NEAR_BOTTOM;
      if (gesture()) stick.current = near; // the user scrolled: follow only if they are back at the bottom
      else if (near) stick.current = true; // e.g. End key, or our own scroll-to-bottom
      setAway(!stick.current && !near);
    };
    const onResize = () => {
      if (stick.current) el.scrollTop = emptyRef.current ? 0 : el.scrollHeight;
      else setAway(gap() >= NEAR_BOTTOM);
    };

    const ro = new ResizeObserver(onResize);
    ro.observe(flow);
    ro.observe(el);
    el.addEventListener('scroll', onScroll, { passive: true });
    el.addEventListener('wheel', onWheel, { passive: true });
    el.addEventListener('keydown', onKey);
    el.addEventListener('touchstart', onTouchStart, { passive: true });
    el.addEventListener('touchend', onTouchEnd, { passive: true });
    el.addEventListener('touchcancel', onTouchEnd, { passive: true });
    el.addEventListener('pointerdown', onPointerDown);
    window.addEventListener('pointerup', onPointerUp);
    return () => {
      ro.disconnect();
      el.removeEventListener('scroll', onScroll);
      el.removeEventListener('wheel', onWheel);
      el.removeEventListener('keydown', onKey);
      el.removeEventListener('touchstart', onTouchStart);
      el.removeEventListener('touchend', onTouchEnd);
      el.removeEventListener('touchcancel', onTouchEnd);
      el.removeEventListener('pointerdown', onPointerDown);
      window.removeEventListener('pointerup', onPointerUp);
    };
  }, []);

  // New thread selected: start at the latest message.
  useLayoutEffect(() => {
    stick.current = true;
    setAway(false);
    const el = scrollRef.current;
    if (el) el.scrollTop = emptyRef.current ? 0 : el.scrollHeight;
  }, [threadId]);

  const jumpToLatest = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    stick.current = true;
    setAway(false);
    const calm = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
    el.scrollTo({ top: el.scrollHeight, behavior: calm ? 'auto' : 'smooth' });
  }, []);

  /* ───────────── actions ───────────── */

  const submit = useCallback(
    (text: string) => {
      const t = text.trim();
      if (!t || streaming) return;
      stick.current = true;
      setDrafts((d) => ({ ...d, [draftKey]: '' }));
      if (threadId) void actions.send(threadId, t);
      else actions.openChat(FREE_ORIGIN, t); // no thread yet: start a free-form one
    },
    [streaming, threadId, draftKey],
  );

  const onStop = useCallback(() => {
    if (threadId) actions.stop(threadId);
  }, [threadId]);

  const onRetry = useCallback(() => {
    if (threadId && lastUserText) {
      stick.current = true;
      void actions.send(threadId, lastUserText);
    }
  }, [threadId, lastUserText]);

  const [railOpen, setRailOpen] = useState(false);

  const goGraph = useCallback(() => {
    // Land on the graph the thread came from: role/requirement nodes live on the career map.
    const k = thread?.origin.kind;
    if (k === 'role' || k === 'requirement') actions.setFlipped(true);
    else if (k === 'skill' || k === 'domain' || k === 'source' || k === 'you') actions.setFlipped(false);
    actions.showGraph();
  }, [thread]);

  const onNew = useCallback(() => {
    const blank = thread && thread.origin.kind === 'free' && thread.messages.length === 0;
    if (!blank) actions.openChat(FREE_ORIGIN, '');
    setRailOpen(false);
    inputRef.current?.focus();
  }, [thread]);

  const onSelect = useCallback((id: string) => {
    actions.selectThread(id);
    setRailOpen(false);
  }, []);

  /* ───────────── suggestions ───────────── */

  const suggestions = useMemo(() => {
    const used = new Set(messages.filter((m) => m.role === 'user').map((m) => m.text.trim().toLowerCase()));
    return suggestionsFor(origin, profile).filter((s) => !used.has(s.toLowerCase()));
  }, [origin, profile, messages]);

  const showDockSuggestions = !empty && !streaming && suggestions.length > 0;
  const lastId = messages[messages.length - 1]?.id;

  return (
    <div className="ch-root">
      <div className="ch-wrap">
        <header className="ch-head">
          <h1 className="sr-only">Tayyari career chat</h1>
          <div className="ch-head-l">
            <Brand />
            <button type="button" className="btn btn-quiet ch-back" onClick={goGraph} aria-label="Back to graph">
              <svg
                width="16"
                height="16"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.5"
                strokeLinecap="round"
                strokeLinejoin="round"
                aria-hidden="true"
                focusable="false"
              >
                <path d="M19 12H5M11 6l-6 6 6 6" />
              </svg>
              <span className="ch-back-text">Back to graph</span>
            </button>
          </div>
          <div className="ch-head-r">
            <span className="kicker ch-model">claude-sonnet-5-5 · context loaded</span>
            <span className="numeral ch-avatar" role="img" aria-label={profile.person.name || 'You'}>
              <span>{initials(profile.person.name, 'ME')}</span>
            </span>
          </div>
        </header>

        <main className="ch-main scroll">
          <Rail
            profile={profile}
            origin={origin}
            threads={threads}
            activeId={threadId ?? null}
            now={now}
            open={railOpen}
            onToggle={() => setRailOpen((o) => !o)}
            onOrigin={goGraph}
            onSelect={onSelect}
            onNew={onNew}
          />

          <section className="ch-convo" aria-label="Conversation">
            <div className="ch-scroll-wrap">
              <div className="ch-scroll scroll" ref={scrollRef}>
                {!empty && thread && <ThreadHead thread={thread} now={now} />}
                <div
                  ref={flowRef}
                  className={`ch-flow${empty ? ' is-empty' : ''}`}
                  role="log"
                  aria-label="Conversation"
                  aria-busy={streaming}
                >
                  {empty ? (
                    <div className="ch-hero ch-in">
                      <h2 className="serif">
                        {origin.kind === 'free' ? (
                          <>
                            Ask anything about <span className="ch-em">your path</span>
                          </>
                        ) : (
                          <>
                            Ask about <span className="ch-em">{origin.title}</span>
                          </>
                        )}
                      </h2>
                      <p>Tayyari reads {footnote} before it answers, so every reply is about you, not a generic career.</p>
                      <Suggestions hero items={suggestionsFor(origin, profile)} onPick={submit} />
                    </div>
                  ) : (
                    messages.map((m) =>
                      m.role === 'user' ? (
                        <UserMessage
                          key={m.id}
                          text={m.text}
                          caption={m.id === firstUserId && origin.kind !== 'free' ? `from the graph · ${origin.title}` : undefined}
                        />
                      ) : (
                        <AssistantMessage
                          key={m.id}
                          message={m}
                          sources={okSources}
                          lines={lines}
                          onRetry={m.id === lastId && m.status === 'error' ? onRetry : undefined}
                        />
                      ),
                    )
                  )}
                </div>
              </div>

              <AnimatePresence>
                {away && (
                  <motion.div
                    className="ch-jump-wrap"
                    initial={{ opacity: 0, y: 12 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0, y: 12 }}
                    transition={{ duration: 0.25, ease: EASE }}
                  >
                    <button type="button" className="btn btn-sm ch-jump" onClick={jumpToLatest}>
                      <svg
                        width="14"
                        height="14"
                        viewBox="0 0 24 24"
                        fill="none"
                        stroke="currentColor"
                        strokeWidth="1.5"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                        aria-hidden="true"
                        focusable="false"
                      >
                        <path d="M12 5v14M6 13l6 6 6-6" />
                      </svg>
                      <span>Jump to latest</span>
                      {streaming && <span className="ch-live" aria-hidden="true" />}
                    </button>
                  </motion.div>
                )}
              </AnimatePresence>
            </div>

            <div className="ch-dock">
              <AnimatePresence initial={false}>
                {showDockSuggestions && (
                  <motion.div
                    key="suggs"
                    className="ch-suggs-wrap"
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    exit={{ opacity: 0 }}
                    transition={{ duration: 0.25, ease: EASE }}
                  >
                    <Suggestions items={suggestions} onPick={submit} />
                  </motion.div>
                )}
              </AnimatePresence>
              <Composer value={draft} onChange={setDraft} onSubmit={() => submit(draft)} onStop={onStop} streaming={streaming} textareaRef={inputRef} />
              <p className="kicker ch-foot">Answers draw on {footnote}</p>
            </div>
          </section>
        </main>
      </div>
    </div>
  );
}
