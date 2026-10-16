/**
 * Where the person is looking. The city comes from their resume and says so; they can change it, pick a locality
 * (so distances mean something) and add a second city they would move to. The pickers are plain selects fed by
 * `GET /api/discover/cities`. The choice is kept in this browser and in the address; the server never stores it, and
 * we never ask for GPS or look at an IP address.
 */
import { useEffect, useId, useRef, useState } from 'react';
import type { FormEvent, ReactNode } from 'react';
import type { DiscoverArea, DiscoverCity } from '../../shared/discover';
import { areaText } from './model';

export interface AreaChoice {
  city?: string;
  locality?: string;
  otherCity?: string;
}

const localitiesOf = (cities: readonly DiscoverCity[], city?: string) => cities.find((c) => c.name === city)?.localities ?? [];

function areaSubline(area: DiscoverArea): string {
  const quoted = area.resumeText ? `: "${area.resumeText}"` : '';
  if (area.cityFrom === 'resume') return `City from your resume${quoted}. ${area.locality ? 'Area set by you.' : 'Pick an area for distances.'}`;
  if (area.cityFrom === 'you') return `City set by you. ${area.locality ? 'Area set by you.' : 'Pick an area for distances.'}`;
  return `We couldn't read a city from your resume${quoted}.`;
}

export interface ContextBarProps {
  area: DiscoverArea;
  otherCity?: string;
  cities: readonly DiscoverCity[];
  editing: boolean;
  /** Which field takes focus when the editor opens. */
  focusField?: 'otherCity';
  onEditing: (open: boolean, focus?: 'otherCity') => void;
  onSave: (choice: AreaChoice) => void;
  /** The lenses and "Why this order", laid out beside the area. */
  children?: ReactNode;
}

export function ContextBar({ area, otherCity, cities, editing, focusField, onEditing, onSave, children }: ContextBarProps) {
  const changeRef = useRef<HTMLButtonElement>(null);
  const name = areaText(area);
  const chip = area.locality ? 'you set this' : area.cityFrom === 'resume' ? 'from your resume' : area.cityFrom === 'you' ? 'you set this' : '';

  return (
    <section className="panel dc-ctx" aria-label="Where and how wide">
      <div className="dc-ctx-row">
        <div className="dc-area">
          <div className="dc-area-line">
            <span className="dc-area-name">{name || 'No city yet'}</span>
            {chip && <span className="chip">{chip}</span>}
            <button ref={changeRef} type="button" className="dc-link dc-link-pad" aria-expanded={editing} onClick={() => onEditing(!editing)}>
              {editing ? 'Close' : 'Change'}
            </button>
          </div>
          <span className="dc-faint dc-small">{areaSubline(area)}</span>
        </div>
        {children}
      </div>
      {editing && (
        <AreaEditor
          area={area}
          otherCity={otherCity}
          cities={cities}
          focusField={focusField}
          onCancel={() => {
            onEditing(false);
            changeRef.current?.focus();
          }}
          onSave={(choice) => {
            onSave(choice);
            onEditing(false);
            changeRef.current?.focus();
          }}
        />
      )}
    </section>
  );
}

function AreaEditor({ area, otherCity, cities, focusField, onCancel, onSave }: { area: DiscoverArea; otherCity?: string; cities: readonly DiscoverCity[]; focusField?: 'otherCity'; onCancel: () => void; onSave: (c: AreaChoice) => void }) {
  const ids = [useId(), useId(), useId()];
  // '' = the city the resume names (the server reads it)
  const [city, setCity] = useState(area.cityFrom === 'you' ? (area.city ?? '') : '');
  const [locality, setLocality] = useState(area.locality ?? '');
  const [other, setOther] = useState(otherCity ?? '');
  const otherRef = useRef<HTMLSelectElement>(null);
  const firstRef = useRef<HTMLSelectElement>(null);

  useEffect(() => {
    (focusField === 'otherCity' ? otherRef : firstRef).current?.focus({ preventScroll: true });
  }, [focusField]);

  const effectiveCity = city || (area.cityFrom === 'resume' ? area.city : undefined);
  const localities = localitiesOf(cities, effectiveCity);
  const loaded = cities.length > 0;

  const submit = (e: FormEvent) => {
    e.preventDefault();
    onSave({
      ...(city ? { city } : {}),
      ...(locality && localities.includes(locality) ? { locality, city: city || effectiveCity } : {}),
      ...(other && other !== effectiveCity ? { otherCity: other } : {}),
    });
  };

  return (
    <form className="dc-editor" onSubmit={submit}>
      <div className="dc-editor-grid">
        <div className="dc-field">
          <label htmlFor={ids[0]} className="kicker">
            City
          </label>
          <select
            id={ids[0]}
            ref={firstRef}
            className="dc-select"
            value={city}
            disabled={!loaded}
            onChange={(e) => {
              setCity(e.target.value);
              setLocality('');
            }}
          >
            <option value="">{area.resumeText ? `From my resume (${area.resumeText})` : 'From my resume'}</option>
            {cities.map((c) => (
              <option key={c.name} value={c.name}>
                {c.name}
              </option>
            ))}
          </select>
        </div>
        <div className="dc-field">
          <label htmlFor={ids[1]} className="kicker">
            Your area{effectiveCity ? ` in ${effectiveCity}` : ''}
          </label>
          <select id={ids[1]} className="dc-select" value={locality} disabled={!loaded || localities.length === 0} onChange={(e) => setLocality(e.target.value)}>
            <option value="">Not set (the whole city)</option>
            {localities.map((l) => (
              <option key={l} value={l}>
                {l}
              </option>
            ))}
          </select>
        </div>
        <div className="dc-field">
          <label htmlFor={ids[2]} className="kicker">
            A city you would move to
          </label>
          <select id={ids[2]} ref={otherRef} className="dc-select" value={other} disabled={!loaded} onChange={(e) => setOther(e.target.value)}>
            <option value="">None</option>
            {cities
              .filter((c) => c.name !== effectiveCity)
              .map((c) => (
                <option key={c.name} value={c.name}>
                  {c.name}
                </option>
              ))}
          </select>
        </div>
      </div>
      {!loaded && <p className="dc-faint dc-small">The list of places is not available right now.</p>}
      <p className="dc-faint dc-small">We use the area you pick, never your phone's location or your IP address. Kept in this browser.</p>
      <div className="dc-actions">
        <button type="submit" className="btn btn-primary btn-sm dc-btn-sm">
          Save
        </button>
        <button type="button" className="btn btn-quiet btn-sm dc-btn-sm" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </form>
  );
}

/* ───────────────────────── asking for a locality ───────────────────────── */

/** The city is known and the area is not: offer the localities as one-tap choices, and a way to skip. */
export function LocalityPrompt({ city, cityFrom, localities, onPick, onMore, onSkip }: { city: string; cityFrom: DiscoverArea['cityFrom']; localities: readonly string[]; onPick: (locality: string) => void; onMore: () => void; onSkip: () => void }) {
  return (
    <section className="panel dc-prompt" aria-label="Pick your area">
      <p className="dc-prompt-text">{cityFrom === 'resume' ? `We read ${city} from your resume.` : `You picked ${city}.`} Pick your area so distances mean something.</p>
      {localities.length > 0 && (
        <div className="dc-picks" role="group" aria-label={`Areas in ${city}`}>
          {localities.slice(0, 8).map((l) => (
            <button key={l} type="button" className="dc-pick" onClick={() => onPick(l)}>
              {l}
            </button>
          ))}
          <button type="button" className="dc-pick" onClick={onMore}>
            Another area
          </button>
        </div>
      )}
      <div className="dc-prompt-foot">
        <button type="button" className="dc-link dc-link-pad" onClick={onSkip}>
          Show roles across {city} for now
        </button>
        <span className="dc-faint dc-small">Distances use the area you pick. We never use GPS or your IP address.</span>
      </div>
    </section>
  );
}

/* ───────────────────────── no city yet ───────────────────────── */

/** Nothing in the resume names a city we know: ask for one, and say what was read. */
export function NoCity({ resumeText, cities, onPick }: { resumeText?: string; cities: readonly DiscoverCity[]; onPick: (city: string) => void }) {
  return (
    <section className="panel dc-state" aria-labelledby="dc-nocity-t">
      <h2 id="dc-nocity-t" className="dc-state-title">
        Pick a city and we'll show what fits.
      </h2>
      <p className="dc-muted">
        {resumeText ? `Your resume says "${resumeText}", and we don't read job boards for that place yet. ` : "We couldn't read a city from your resume. "}
        Choose the nearest one below. You can change it any time.
      </p>
      {cities.length > 0 && (
        <div className="dc-picks" role="group" aria-label="Cities">
          {cities.map((c) => (
            <button key={c.name} type="button" className="dc-pick" onClick={() => onPick(c.name)}>
              {c.name}
            </button>
          ))}
        </div>
      )}
      <p className="dc-faint dc-small">We use the city you pick, never your phone's location or your IP address. Kept in this browser.</p>
    </section>
  );
}
