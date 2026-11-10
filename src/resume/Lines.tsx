/**
 * The resume in view, as lines. A fixed line (the name, a heading, a job's title, skills, a degree) is text; a bullet
 * is a real button, and pressing it selects it (`aria-pressed`). A mark from the server ("moved", "edited by you",
 * "added") shows at the right of its line and tints it.
 *
 * Keyboard: the sheet is one tab stop. Arrow keys, Home and End move between the bullets; Enter or Space presses one.
 */
import { Fragment, useId, useRef } from 'react';
import type { KeyboardEvent, ReactNode } from 'react';
import { markText } from './model';
import type { SheetLine } from './model';

interface LinesProps {
  lines: readonly SheetLine[];
  /** The pressed bullet's id. */
  selectedId: string | null;
  onSelect: (bulletId: string | null) => void;
  /** Shown directly under the pressed line (on a phone the block's panel sits there). */
  inline?: ReactNode;
}

const LINE = '[data-rs-line]';

export function Lines({ lines, selectedId, onSelect, inline }: LinesProps) {
  const sheet = useRef<HTMLDivElement>(null);
  const hintId = useId();
  const bullets = lines.filter((l) => l.bullet);
  const tabStop = bullets.some((l) => l.bullet?.id === selectedId) ? selectedId : (bullets[0]?.bullet?.id ?? null);

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    // only from a line itself: the panel under a pressed line has its own keys
    if (!(e.target instanceof HTMLElement) || !e.target.matches(LINE)) return;
    const all = Array.from(sheet.current?.querySelectorAll<HTMLElement>(LINE) ?? []);
    const at = all.indexOf(e.target);
    const to = e.key === 'ArrowDown' ? at + 1 : e.key === 'ArrowUp' ? at - 1 : e.key === 'Home' ? 0 : e.key === 'End' ? all.length - 1 : -1;
    if (to < 0 || to >= all.length || to === at) return;
    e.preventDefault();
    all[to].focus();
  };

  return (
    <div ref={sheet} className="rs-sheet" role="group" aria-label="The resume, line by line" aria-describedby={hintId} onKeyDown={onKeyDown}>
      <p id={hintId} className="sr-only">
        Arrow keys move between the blocks. Enter presses one and shows where it comes from and what you can do with it.
      </p>
      {lines.map((line) => {
        const no = (
          <span className="rs-line-no" aria-hidden="true">
            {line.no}
          </span>
        );
        if (!line.bullet) {
          const cls = `rs-line${line.kind === 'fixed' ? '' : ` is-${line.kind}`}`;
          return line.kind === 'head' ? (
            <h3 key={line.key} className={cls}>
              {no}
              <span className="rs-line-t">{line.text}</span>
            </h3>
          ) : (
            <p key={line.key} className={cls}>
              {no}
              <span className="rs-line-t">{line.text}</span>
            </p>
          );
        }
        const id = line.bullet.id;
        const on = id === selectedId;
        const marked = line.marks.length > 0;
        return (
          <Fragment key={line.key}>
            <button
              type="button"
              className={`rs-line is-bullet${marked ? ' is-marked' : ''}${on ? ' is-on' : ''}`}
              aria-pressed={on}
              tabIndex={id === tabStop ? 0 : -1}
              data-rs-line
              data-bullet={id}
              onClick={() => onSelect(on ? null : id)}
            >
              {no}
              <span className="rs-line-t">{line.text}</span>
              {marked && <span className="rs-line-mark">{markText(line.marks)}</span>}
            </button>
            {on && inline}
          </Fragment>
        );
      })}
    </div>
  );
}
