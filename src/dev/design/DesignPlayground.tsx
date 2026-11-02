/**
 * Dev page (`/dev/design`, development builds only): the real screens on fixtures under either look, scheme and hue,
 * one at a time or side by side. State lives in the query string (see ./state.ts).
 */
import { useEffect, useState } from 'react';
import type { AtmoMode, SchemeChoice } from '../../theme/ambient';
import { useAtmoMode, useScheme } from '../../theme/ambient';
import { Brand } from '../../components/Brand';
import { CursorLight } from '../../light/CursorLight';
import { Spot } from '../../light/Spot';
import { Screen } from './Screens';
import { SCREENS, applyHuePin, applyLook, applyScheme, cursorIsOff, readState, reloadWithCursor, writeUrl } from './state';
import type { Look, PlaygroundState } from './state';
import '../../styles/design-playground.css';

function Seg<T extends string>({ label, value, options, onPick }: { label: string; value: T; options: readonly (readonly [T, string])[]; onPick: (v: T) => void }) {
  return (
    <div className="dp-group" role="group" aria-label={label}>
      <span className="kicker">{label}</span>
      <div className="dp-seg">
        {options.map(([id, text]) => (
          <button key={id} type="button" className="btn btn-sm dp-btn" aria-pressed={value === id} onClick={() => onPick(id)}>
            {text}
          </button>
        ))}
      </div>
    </div>
  );
}

const LOOKS: readonly (readonly [Look, string])[] = [
  ['today', 'Today'],
  ['quiet', 'Quiet'],
];
const SCHEMES: readonly (readonly [SchemeChoice, string])[] = [
  ['system', 'System'],
  ['light', 'Light'],
  ['dark', 'Dark'],
];
const ATMOS: readonly (readonly [AtmoMode, string])[] = [
  ['drift', 'Drift'],
  ['still', 'Still'],
  ['off', 'Off'],
];

export function DesignPlayground() {
  const [chosenScheme, effective] = useScheme();
  const [atmo, , setAtmo] = useAtmoMode();
  const [s, setS] = useState<PlaygroundState>(() => readState(chosenScheme));
  const cursorOff = cursorIsOff();

  // the first state may come from the address: apply it once
  useEffect(() => {
    applyLook(s.look);
    return () => applyLook('today');
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const update = (patch: Partial<PlaygroundState>) => {
    const next = { ...s, ...patch };
    setS(next);
    if (patch.look !== undefined) applyLook(next.look);
    writeUrl(next);
    if (patch.scheme !== undefined) applyScheme(next.scheme);
    if ('hue' in patch) applyHuePin(next.hue);
  };

  const other: Look = s.look === 'quiet' ? 'today' : 'quiet';
  const wrap = (look: Look) => `dp-pane look-${look} scheme-${effective}`;

  return (
    <div className="dp">
      {/* the cursor layers App mounts, so the playground previews them too (quiet dark adds the spotlight) */}
      <CursorLight />
      <Spot />
      <header className="dp-bar">
        <div className="dp-bar-top">
          <Brand />
          <span className="kicker">Dev only · the real screens on fixtures, no server</span>
        </div>
        <div className="dp-controls">
          <Seg label="Look" value={s.look} options={LOOKS} onPick={(look) => update({ look })} />
          <Seg label="Scheme" value={s.scheme} options={SCHEMES} onPick={(scheme) => update({ scheme })} />
          <Seg label="Atmosphere" value={atmo} options={ATMOS} onPick={setAtmo} />
          <div className="dp-group" role="group" aria-label="Hue">
            <span className="kicker">Hue</span>
            <div className="dp-hue">
              <input id="dp-pin" type="checkbox" checked={s.hue !== null} onChange={(e) => update({ hue: e.target.checked ? 250 : null })} />
              <label htmlFor="dp-pin" className="dp-small">
                Pin
              </label>
              <input
                type="range"
                min={0}
                max={359}
                step={1}
                className="dp-range"
                aria-label="Hue in degrees"
                value={s.hue ?? 250}
                disabled={s.hue === null}
                onChange={(e) => update({ hue: Number(e.target.value) })}
              />
              <output className="dp-small">{s.hue ?? 'clock'}</output>
            </div>
          </div>
          <div className="dp-group" role="group" aria-label="Cursor light">
            <span className="kicker">Cursor</span>
            <div className="dp-seg">
              <button type="button" className="btn btn-sm dp-btn" aria-pressed={!cursorOff} onClick={() => cursorOff && reloadWithCursor(true)}>
                On
              </button>
              <button type="button" className="btn btn-sm dp-btn" aria-pressed={cursorOff} onClick={() => !cursorOff && reloadWithCursor(false)}>
                Off
              </button>
            </div>
            <span className="dp-small">Reloads the page (?light=off)</span>
          </div>
          <div className="dp-group">
            <span className="kicker">Compare</span>
            <button type="button" className="btn btn-sm dp-btn" aria-pressed={s.compare} onClick={() => update({ compare: !s.compare })}>
              {s.compare ? 'On' : 'Off'}
            </button>
          </div>
        </div>
        <nav className="dp-tabs" aria-label="Screens">
          {SCREENS.map(([id, label]) => (
            <button key={id} type="button" className={`dp-tab${s.screen === id ? ' is-current' : ''}`} aria-pressed={s.screen === id} onClick={() => update({ screen: id })}>
              {label}
            </button>
          ))}
        </nav>
        {s.compare && <p className="kicker dp-note">The ambient layer and the cursor light are global: both panes share one hue, one atmosphere and one cursor. Only the role tokens differ per pane.</p>}
        {s.screen === 'home' && <p className="kicker dp-note">Home is the real Onboarding. With no server its health and sample calls fail softly, so the sample preview and the Weekly tile may be empty.</p>}
      </header>
      <div className={`dp-stage${s.compare ? ' is-compare' : ''}`}>
        <section className={wrap(s.look)} aria-label={`${s.look} look`}>
          {s.compare && <p className="kicker dp-label">{s.look === 'quiet' ? 'Quiet (current)' : 'Today (current)'}</p>}
          <Screen id={s.screen} />
        </section>
        {s.compare && (
          <section className={wrap(other)} aria-label={`${other} look`}>
            <p className="kicker dp-label">{other === 'quiet' ? 'Quiet' : 'Today'}</p>
            <Screen id={s.screen} />
          </section>
        )}
      </div>
    </div>
  );
}
