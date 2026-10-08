/**
 * The owner's portfolio editor (the approved Editor board). A top bar (back, title, draft status, the
 * Edit | Preview public view switch, the Old Newspaper switch, an inert Share), a left panel (looks, family,
 * type, corners, density, the Sections list, personal details) and the live preview on the right, where the
 * selected block's copy slots turn into in-place editors.
 *
 * Data only comes in through props and leaves through `onPatch` / `onRewrite` / `onBack` / `onRegenerate`:
 * no store, no fetch. The preview is whatever `preview(...)` returns (the app passes `PortfolioView`), so this
 * file never imports it. The chrome uses the app's ambient tokens; only the swatches, look thumbnails and the
 * preview sit inside a `.pf-scope` and show the portfolio's own colours.
 */
import { useCallback, useId, useMemo, useState } from 'react';
import type { KeyboardEvent, ReactNode } from 'react';
import { FAMILY_HUES } from '../../../shared/portfolio';
import type {
  Family,
  GraphSnapshot,
  Portfolio,
  PortfolioContent,
  PortfolioPatch,
  PortfolioPrivacy,
  PortfolioSkin,
  PortfolioSpec,
  PortfolioStyle,
} from '../../../shared/portfolio';
import { IconCheck, IconRefresh } from '../../components/Icons';
import { cssVars, useElementSize, useMediaQuery } from '../../lib/ui';
import { asOfLabel, slot, styleVars } from '../model';
import type { ViewMode } from '../model';
import type { EditHooks } from '../types';
import { IconBack } from './icons';
import { capitalise, CORNER_OPTIONS, DENSITY_OPTIONS, TYPE_OPTIONS } from './labels';
import { LookThumb } from './LookThumb';
import { SectionList } from './SectionList';
import { SlotEditor } from './SlotEditor';
import { useSpecDraft } from './useSpecDraft';
import '../../styles/portfolio-editor.css';

/** What the editor asks its `preview` render prop to draw (the props of `PortfolioView`, minus the renderers). */
export interface EditorPreviewArgs {
  spec: PortfolioSpec;
  content: PortfolioContent;
  graph: GraphSnapshot;
  mode: ViewMode;
  skin: PortfolioSkin;
  editing: EditHooks;
  onToggleSkin(): void;
}

export interface EditorViewProps {
  portfolio: Portfolio;
  /** A calm status line for work in progress ("Rewriting the About section…"); also pauses the long actions. */
  busy?: string;
  onPatch(patch: PortfolioPatch): void;
  onRewrite(slotId: string): void;
  onBack(): void;
  onRegenerate(): void;
  preview: (p: EditorPreviewArgs) => ReactNode;
}

/** The width the preview is laid out at; a narrower pane shows it scaled down to fit. */
const DESIGN_WIDTH = 1120;
const FAMILIES = Object.keys(FAMILY_HUES) as Family[];

type PaneMode = 'edit' | 'preview';

/** Arrow keys move through a radio group made of buttons, selecting as they go. */
function radioKeys(e: KeyboardEvent<HTMLElement>) {
  if (!['ArrowRight', 'ArrowDown', 'ArrowLeft', 'ArrowUp'].includes(e.key)) return;
  const items = Array.from(e.currentTarget.querySelectorAll<HTMLElement>('[role="radio"]'));
  const at = items.indexOf(document.activeElement as HTMLElement);
  if (at < 0) return;
  e.preventDefault();
  const step = e.key === 'ArrowRight' || e.key === 'ArrowDown' ? 1 : -1;
  const next = items[(at + step + items.length) % items.length];
  next.focus();
  next.click();
}

/** Tokens of a portfolio colour family, for a wrapper that makes its children paint in that family. */
const familyVars = (spec: PortfolioSpec, family: Family) => cssVars(styleVars({ ...spec, style: { ...spec.style, family } }));

const PRIVACY_ROWS: { key: keyof PortfolioPrivacy; label: string }[] = [
  { key: 'email', label: 'Email address' },
  { key: 'phone', label: 'Phone number' },
  { key: 'grades', label: 'Grades' },
];

export function EditorView({ portfolio, busy, onPatch, onRewrite, onBack, onRegenerate, preview }: EditorViewProps) {
  const uid = useId();
  const draft = useSpecDraft(portfolio, onPatch);
  const { spec } = draft;
  const { content, graph, looks } = portfolio;

  const [mode, setMode] = useState<PaneMode>('edit');
  const [skin, setSkin] = useState<PortfolioSkin>(portfolio.publish.defaultView);
  const [picked, setPicked] = useState('about');
  const [spoken, setSpoken] = useState('');
  const [confirming, setConfirming] = useState(false);

  const selectedId = spec.blocks.some((b) => b.id === picked) ? picked : (spec.blocks.find((b) => b.kind !== 'hero')?.id ?? '');
  const select = useCallback((id: string) => {
    setPicked(id);
    setMode('edit');
  }, []);
  const toggleSkin = useCallback(() => setSkin((s) => (s === 'press' ? 'standard' : 'press')), []);

  const setStyle = (change: Partial<PortfolioStyle>) => draft.update((s) => ({ style: { ...s.style, ...change } }));
  const setPrivacy = (key: keyof PortfolioPrivacy, on: boolean) => draft.update((s) => ({ privacy: { ...s.privacy, [key]: on } }));

  // The slot editor the blocks drop in where a slot's text would be. Its identity changes with the props it
  // reads, so a busy flag or new content reaches the preview; the editors themselves keep their state.
  const renderSlotEditor = useCallback(
    (slotId: string, text: string): ReactNode => (
      <SlotEditor
        key={slotId}
        slotId={slotId}
        text={text}
        slot={slot(content, slotId)}
        facts={content.facts}
        busy={!!busy}
        onPatch={onPatch}
        onRewrite={onRewrite}
      />
    ),
    [content, busy, onPatch, onRewrite],
  );

  const editing = useMemo<EditHooks>(
    () => (mode === 'edit' ? { selectedBlockId: selectedId, onSelectBlock: select, renderSlotEditor } : {}),
    [mode, selectedId, select, renderSlotEditor],
  );

  // The preview is laid out at a desktop width and scaled to fit a narrower pane (phones get the real narrow layout).
  const [paneRef, pane] = useElementSize<HTMLDivElement>();
  const wide = useMediaQuery('(min-width: 761px)');
  const scale = wide && pane.width > 0 && pane.width < DESIGN_WIDTH ? pane.width / DESIGN_WIDTH : 1;

  const lookName = capitalise((looks[draft.lookIndex] ?? { composition: spec.composition }).composition);
  const published = portfolio.publish.state === 'published';
  const editMode = mode === 'edit';

  return (
    <div className="pe-view">
      <header className="pe-header">
        <button type="button" className="pe-ghost" aria-label="Back to Skill Graph" onClick={onBack}>
          <IconBack />
          Skill Graph
        </button>
        <span className="pe-vsep" aria-hidden="true" />
        <h1 className="pe-title serif">Your portfolio</h1>
        <p className="pe-chip pe-chip-status">
          <span className="pe-dot" aria-hidden="true" />
          {published ? 'Published · anyone with the link can see it' : 'Draft · only you can see this'}
        </p>
        <div className="pe-grow" />
        <div className="pe-seg" role="group" aria-label="View">
          <button type="button" aria-pressed={editMode} onClick={() => setMode('edit')}>
            Edit
          </button>
          <button type="button" aria-pressed={!editMode} onClick={() => setMode('preview')}>
            Preview public view
          </button>
        </div>
        <button type="button" role="switch" className="pe-switch" aria-checked={skin === 'press'} onClick={toggleSkin}>
          <span>Try Old Newspaper Style</span>
          <span className="pe-sw-track" aria-hidden="true">
            <span className="pe-sw-knob" />
          </span>
        </button>
        <button type="button" className="pe-outline" disabled aria-disabled="true">
          Share · coming soon
        </button>
      </header>

      <div className="pe-body">
        <aside className="pe-aside scroll" aria-label="Portfolio controls">
          <section className="pe-col-a" aria-labelledby={`${uid}-look`}>
            <h2 id={`${uid}-look`} className="pe-lbl">
              Look
            </h2>
            <div role="radiogroup" aria-label="Candidate looks" className="pe-looks" onKeyDown={radioKeys}>
              {looks.map((look, i) => {
                const checked = i === draft.lookIndex;
                return (
                  <button
                    key={i}
                    type="button"
                    role="radio"
                    className="pe-look"
                    aria-checked={checked}
                    tabIndex={checked ? 0 : -1}
                    onClick={() => draft.selectLook(i)}
                  >
                    <span className="pf-scope pe-scope" style={familyVars(look, look.style.family)}>
                      <span className="pe-mini">
                        <LookThumb spec={look} />
                      </span>
                    </span>
                    <span className="pe-look-meta">
                      <span className="pe-look-text">
                        <span className="pe-look-name">{capitalise(look.composition)}</span>
                        <span className="pe-look-sub">{capitalise(look.style.family)}</span>
                      </span>
                      {checked && <IconCheck size={14} />}
                    </span>
                  </button>
                );
              })}
            </div>
            {looks.length > 1 && (
              <button
                type="button"
                className="pe-outline pe-wide pe-gap"
                onClick={() => draft.selectLook((draft.lookIndex + 1) % looks.length)}
              >
                <IconRefresh size={16} />
                Try another look
              </button>
            )}
          </section>

          <div className="pe-lockable" inert={draft.locked}>
            <section className="pe-style pe-col-a" aria-label="Style">
              <div className="pe-rowc pe-rowc-flush">
                <span id={`${uid}-fam`} className="pe-rl">
                  Gradient family
                </span>
                <span className="numeral pe-muted">{capitalise(spec.style.family)}</span>
              </div>
              <div role="radiogroup" aria-labelledby={`${uid}-fam`} className="pe-swatches" onKeyDown={radioKeys}>
                {FAMILIES.map((family) => {
                  const checked = spec.style.family === family;
                  return (
                    <button
                      key={family}
                      type="button"
                      role="radio"
                      className="pe-swc"
                      aria-checked={checked}
                      aria-label={capitalise(family)}
                      tabIndex={checked ? 0 : -1}
                      onClick={() => setStyle({ family })}
                    >
                      <span className="pf-scope pe-scope" style={familyVars(spec, family)}>
                        <span className="pe-swc-dot" />
                      </span>
                    </button>
                  );
                })}
              </div>

              <div className="pe-rowc">
                <label htmlFor={`${uid}-type`} className="pe-rl">
                  Type pairing
                </label>
                <select
                  id={`${uid}-type`}
                  className="pe-sel pe-sel-wide"
                  value={spec.style.type}
                  onChange={(e) => setStyle({ type: e.target.value as PortfolioStyle['type'] })}
                >
                  {TYPE_OPTIONS.map((t) => (
                    <option key={t} value={t}>
                      {capitalise(t)}
                    </option>
                  ))}
                </select>
              </div>
              <div className="pe-rowc">
                <span id={`${uid}-cor`} className="pe-rl">
                  Corners
                </span>
                <div className="pe-seg" role="group" aria-labelledby={`${uid}-cor`}>
                  {CORNER_OPTIONS.map((c) => (
                    <button key={c} type="button" aria-pressed={spec.style.corners === c} onClick={() => setStyle({ corners: c })}>
                      {capitalise(c)}
                    </button>
                  ))}
                </div>
              </div>
              <div className="pe-rowc">
                <span id={`${uid}-den`} className="pe-rl">
                  Density
                </span>
                <div className="pe-seg" role="group" aria-labelledby={`${uid}-den`}>
                  {DENSITY_OPTIONS.map((d) => (
                    <button key={d} type="button" aria-pressed={spec.style.density === d} onClick={() => setStyle({ density: d })}>
                      {capitalise(d)}
                    </button>
                  ))}
                </div>
              </div>
            </section>

            <section className="pe-sec pe-col-b" aria-labelledby={`${uid}-sec`}>
              <div className="pe-sec-head">
                <h2 id={`${uid}-sec`} className="pe-lbl">
                  Sections
                </h2>
                <span className="pe-muted pe-small">Reorder with the arrows</span>
              </div>
              <SectionList
                spec={spec}
                content={content}
                graph={graph}
                selectedId={selectedId}
                onSelect={select}
                update={draft.update}
                announce={setSpoken}
              />
            </section>

            <section className="pe-sec pe-col-a" aria-labelledby={`${uid}-priv`}>
              <h2 id={`${uid}-priv`} className="pe-lbl">
                Personal details
              </h2>
              <p className="pe-muted pe-small pe-hint">Private by default. Switch one on to show it on your page.</p>
              <ul className="pe-plain">
                {PRIVACY_ROWS.map(({ key, label }) => (
                  <li key={key}>
                    <label className="pe-check pe-check-row">
                      <input type="checkbox" checked={spec.privacy[key]} onChange={(e) => setPrivacy(key, e.target.checked)} />
                      <span>{label}</span>
                    </label>
                  </li>
                ))}
              </ul>
            </section>

            <section className="pe-sec pe-col-a" aria-labelledby={`${uid}-fresh`}>
              <h2 id={`${uid}-fresh`} className="pe-lbl">
                Start fresh
              </h2>
              {confirming ? (
                <div className="pe-confirm" role="group" aria-label="Confirm regenerate">
                  <p className="pe-small">This builds a new portfolio from your sources and replaces this one, edits included.</p>
                  <div className="pe-confirm-actions">
                    <button type="button" className="pe-outline" autoFocus onClick={() => setConfirming(false)}>
                      Keep my edits
                    </button>
                    <button
                      type="button"
                      className="pe-solid"
                      disabled={!!busy}
                      onClick={() => {
                        setConfirming(false);
                        onRegenerate();
                      }}
                    >
                      Regenerate
                    </button>
                  </div>
                </div>
              ) : (
                <button type="button" className="pe-outline pe-wide pe-gap" disabled={!!busy} onClick={() => setConfirming(true)}>
                  <IconRefresh size={16} />
                  Regenerate from my sources
                </button>
              )}
            </section>
          </div>
        </aside>

        <main className="pe-main">
          {portfolio.graphOutdated && (
            <div className="pe-banner" role="status">
              <p>
                Your profile was analysed again after this snapshot ({asOfLabel(graph)}). The page still shows the older graph.
              </p>
              <button type="button" className="pe-solid" disabled={!!busy} onClick={() => onPatch({ refreshGraph: true })}>
                Update graph
              </button>
            </div>
          )}

          <div className="pe-main-head">
            <div className="pe-main-title">
              <h2 className="pe-lbl">Live preview</h2>
              <span className="pe-muted pe-small">
                {editMode ? 'Select a section to edit its wording' : 'Public view · hidden and private sections are left out'}
              </span>
            </div>
            <div className="pe-main-side">
              {busy && (
                <p className="pe-busy" role="status" aria-live="polite">
                  <span className="pe-dot" aria-hidden="true" />
                  {busy}
                </p>
              )}
              <span className="pe-chip">{skin === 'press' ? 'Old Newspaper Style' : `Look: ${lookName} · ${capitalise(spec.style.family)}`}</span>
            </div>
          </div>

          <div className="pe-pane">
            <div ref={paneRef} className="pe-pane-scroll scroll" role="region" aria-label="Portfolio preview" tabIndex={0}>
              <div className="pe-pane-inner" style={scale < 1 ? { width: DESIGN_WIDTH, zoom: scale } : undefined}>
                {preview({
                  spec,
                  content,
                  graph,
                  mode: editMode ? 'owner' : 'public',
                  skin,
                  editing,
                  onToggleSkin: toggleSkin,
                })}
              </div>
            </div>
          </div>
        </main>
      </div>

      <p className="sr-only" role="status" aria-live="polite">
        {spoken}
      </p>
    </div>
  );
}
