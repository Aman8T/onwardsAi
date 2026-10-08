/**
 * The Sections list: every block in page order with Up / Down buttons, a variant select, a "shown on the
 * page" toggle and a "public" toggle. The Skill Graph row also carries the area checkboxes (at least
 * min(3, n) stay) and the numbers-strip toggle. Constraints are enforced here before anything is sent:
 * the hero stays first and the graph stays within the first three.
 */
import { useId, useLayoutEffect, useRef } from 'react';
import type { BlockSpec, GraphSnapshot, PortfolioContent, PortfolioSpec } from '../../../shared/portfolio';
import { IconChevron, IconEye, IconVisibility } from './icons';
import { BLOCK_NAMES, emptyHint, minDomains, tryMove, variantName, variantsFor } from './labels';
import type { SpecPatch } from './useSpecDraft';

export interface SectionListProps {
  spec: PortfolioSpec;
  content: PortfolioContent;
  graph: GraphSnapshot;
  selectedId: string;
  onSelect(id: string): void;
  update(fn: (latest: PortfolioSpec) => SpecPatch): void;
  /** Spoken to screen readers after a move ("Moved About up, position 3 of 10"). */
  announce(message: string): void;
}

export function SectionList({ spec, content, graph, selectedId, onSelect, update, announce }: SectionListProps) {
  const list = useRef<HTMLUListElement>(null);
  const keepFocus = useRef<string | null>(null);

  // A moved row is re-inserted by the browser and loses focus; put it back on the button that was used.
  useLayoutEffect(() => {
    const want = keepFocus.current;
    if (!want || !list.current) return;
    keepFocus.current = null;
    const buttons = Array.from(list.current.querySelectorAll<HTMLElement>('[data-move]'));
    buttons.find((el) => el.dataset.move === want)?.focus();
  }, [spec.blocks]);

  const patchBlock = (id: string, change: (b: BlockSpec) => BlockSpec) =>
    update((s) => ({ blocks: s.blocks.map((b) => (b.id === id ? change(b) : b)) }));

  const move = (id: string, dir: -1 | 1) => {
    update((s) => {
      const at = s.blocks.findIndex((b) => b.id === id);
      const r = tryMove(s.blocks, at, dir);
      if (!r.ok) return {};
      const name = BLOCK_NAMES[s.blocks[at].kind];
      announce(`Moved ${name} ${dir < 0 ? 'up' : 'down'}, position ${at + dir + 1} of ${s.blocks.length}`);
      keepFocus.current = `${id}:${dir < 0 ? 'up' : 'down'}`;
      return { blocks: r.blocks };
    });
  };

  return (
    <ul ref={list} className="pe-rows">
      {spec.blocks.map((b, i) => {
        const name = BLOCK_NAMES[b.kind];
        const empty = emptyHint(b.kind, content);
        const up = tryMove(spec.blocks, i, -1);
        const down = tryMove(spec.blocks, i, 1);
        const variants = variantsFor(b.kind, b.variant);
        const cannotShow = !b.visible && empty !== undefined;
        const tag = !b.visible ? (b.kind === 'openTo' ? '· off' : '· hidden') : !b.public ? '· private' : '';
        return (
          <li key={b.id} className="pe-row" data-selected={b.id === selectedId || undefined} data-off={!b.visible || undefined}>
            <div className="pe-row-main">
              <div className="pe-move" role="group" aria-label={`Move ${name}`}>
                <MoveButton dir="up" name={name} id={b.id} result={up} onMove={move} />
                <MoveButton dir="down" name={name} id={b.id} result={down} onMove={move} />
              </div>
              <button
                type="button"
                className="pe-name"
                aria-current={b.id === selectedId ? 'true' : undefined}
                title={`Edit ${name} in the preview`}
                onClick={() => onSelect(b.id)}
              >
                <span>{name}</span>
                {tag && <span className="pe-tag">{tag}</span>}
              </button>
              {variants.length > 1 && (
                <select
                  className="pe-sel pe-sel-variant"
                  aria-label={`${name} variant`}
                  value={b.variant}
                  onChange={(e) => patchBlock(b.id, (x) => ({ ...x, variant: e.target.value }))}
                >
                  {variants.map((v) => (
                    <option key={v} value={v}>
                      {variantName(v)}
                    </option>
                  ))}
                </select>
              )}
              <button
                type="button"
                className="pe-ib"
                aria-label={`Show ${name} on the page`}
                aria-pressed={b.visible}
                aria-disabled={cannotShow || undefined}
                title={cannotShow ? empty : undefined}
                onClick={() => {
                  if (cannotShow) return;
                  // Turning "Open to" on is the opt-in itself, so it goes public with it.
                  patchBlock(b.id, (x) => ({ ...x, visible: !x.visible, public: x.kind === 'openTo' ? !x.visible : x.public }));
                }}
              >
                <IconEye off={!b.visible} />
              </button>
              <button
                type="button"
                className="pe-ib"
                aria-label={`Make ${name} public`}
                aria-pressed={b.public}
                onClick={() => patchBlock(b.id, (x) => ({ ...x, public: !x.public }))}
              >
                <IconVisibility isPublic={b.public} />
              </button>
            </div>
            {cannotShow && <p className="pe-row-note">{empty}</p>}
            {b.kind === 'graph' && <GraphOptions spec={spec} content={content} graph={graph} update={update} />}
          </li>
        );
      })}
    </ul>
  );
}

function MoveButton({
  dir,
  name,
  id,
  result,
  onMove,
}: {
  dir: 'up' | 'down';
  name: string;
  id: string;
  result: ReturnType<typeof tryMove>;
  onMove(id: string, dir: -1 | 1): void;
}) {
  const blocked = !result.ok;
  return (
    <button
      type="button"
      className="pe-mv"
      data-move={`${id}:${dir}`}
      aria-label={`Move ${name} ${dir}`}
      aria-disabled={blocked || undefined}
      title={result.ok ? undefined : result.why || undefined}
      onClick={() => {
        if (!blocked) onMove(id, dir === 'up' ? -1 : 1);
      }}
    >
      <IconChevron dir={dir} />
    </button>
  );
}

/** Under the graph row: the "required" note, the areas to show, and the numbers strip. */
function GraphOptions({
  spec,
  content,
  graph,
  update,
}: {
  spec: PortfolioSpec;
  content: PortfolioContent;
  graph: GraphSnapshot;
  update: SectionListProps['update'];
}) {
  const uid = useId();
  const ids = new Set(graph.domains.map((d) => d.id));
  const hidden = new Set(spec.graph.hiddenDomainIds.filter((id) => ids.has(id)));
  const total = graph.domains.length;
  const shown = total - hidden.size;
  const floor = minDomains(total);
  const setHidden = (id: string, hide: boolean) =>
    update((s) => {
      const next = new Set(s.graph.hiddenDomainIds.filter((x) => ids.has(x)));
      if (hide) next.add(id);
      else next.delete(id);
      if (total - next.size < floor) return {};
      return { graph: { ...s.graph, hiddenDomainIds: graph.domains.filter((d) => next.has(d.id)).map((d) => d.id) } };
    });

  return (
    <div className="pe-graph-opts">
      <p className="pe-row-note pe-row-note-flush">Required on the page · hiding it hides it from viewers</p>
      <fieldset className="pe-areas" aria-describedby={`${uid}-floor`}>
        <legend className="pe-legend">
          Areas shown <span className="numeral">{shown} of {total}</span>
        </legend>
        {graph.domains.map((d) => {
          const isShown = !hidden.has(d.id);
          const locked = isShown && shown <= floor;
          return (
            <label key={d.id} className="pe-check" data-locked={locked || undefined}>
              <input
                type="checkbox"
                checked={isShown}
                disabled={locked}
                onChange={(e) => setHidden(d.id, !e.target.checked)}
              />
              <span>{d.name}</span>
            </label>
          );
        })}
        <p id={`${uid}-floor`} className="pe-row-note pe-row-note-flush">
          At least {floor} {floor === 1 ? 'area stays' : 'areas stay'} on the page.
        </p>
      </fieldset>
      <label className="pe-check">
        <input
          type="checkbox"
          checked={spec.graph.stats && content.stats.length > 0}
          disabled={content.stats.length === 0}
          onChange={(e) => update((s) => ({ graph: { ...s.graph, stats: e.target.checked } }))}
        />
        <span>Show the numbers strip beside the graph</span>
      </label>
    </div>
  );
}
