/**
 * Where the person looks and when it last ran: the city chip with its picker, the "Include remote in India" checkbox,
 * the status of the last search, and the button that starts the next one. The Start state and the results share the
 * city chip and the checkbox; the bar is the results' panel.
 *
 * The city comes from the resume and says so; Change opens a list of the cities Discover reads (from
 * `GET /api/discover/cities`) and the choice is kept in this browser and in the address. We never ask for GPS or look at
 * an IP address, and the server never stores the choice. Both the city and the remote choice feed the next search and the
 * lookup of the last results, which is why changing either starts the page over for that place.
 */
import { useId, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import type { DiscoverCity } from '../../../shared/discover';
import { IconSearch } from './icons';

export interface CityControlProps {
  /** The city as it is shown ("Bengaluru"), or undefined when none is known. */
  name?: string;
  /** Where the name comes from: the resume's own words, the person's pick, or nowhere. */
  source: 'resume' | 'you' | 'none';
  cities: readonly DiscoverCity[];
  /** The city the person picked (undefined = the resume's). */
  picked?: string;
  /** The city the resume names, when it names one the list knows. */
  resumeCity?: string;
  onPick: (city: string | undefined) => void;
  /** The framed form of the Start state. */
  boxed?: boolean;
}

export function CityControl({ name, source, cities, picked, resumeCity, onPick, boxed = false }: CityControlProps) {
  const [open, setOpen] = useState(false);
  const panel = useId();
  const change = useRef<HTMLButtonElement>(null);
  const chip = source === 'resume' ? 'from your resume' : source === 'you' ? 'you picked this' : '';

  const pick = (city: string | undefined) => {
    setOpen(false);
    onPick(city);
    requestAnimationFrame(() => change.current?.focus({ preventScroll: true }));
  };

  return (
    <div className="dc-city-wrap">
      <div className={boxed ? 'dc-city is-boxed' : 'dc-city'}>
        <span className="dc-city-name">{name ?? 'No city yet'}</span>
        {chip && <span className="chip">{chip}</span>}
        <button ref={change} type="button" className="dc-link dc-link-pad" aria-expanded={open} aria-controls={open ? panel : undefined} onClick={() => setOpen((o) => !o)}>
          {open ? 'Close' : name ? 'Change' : 'Pick a city'}
        </button>
      </div>
      {open && (
        <div id={panel} className="dc-picker" role="group" aria-label="Cities">
          <div className="dc-picks">
            {resumeCity && (
              <button type="button" className="dc-pick" aria-pressed={picked === undefined} onClick={() => pick(undefined)}>
                From my resume ({resumeCity})
              </button>
            )}
            {cities.map((c) => (
              <button key={c.name} type="button" className="dc-pick" aria-pressed={picked === c.name} onClick={() => pick(c.name)}>
                {c.name}
              </button>
            ))}
          </div>
          {cities.length === 0 && <p className="dc-muted dc-small">The list of cities is not available right now.</p>}
          <p className="dc-muted dc-small">We use the city you pick, never your phone's location or your IP address. Kept in this browser.</p>
        </div>
      )}
    </div>
  );
}

export function RemoteToggle({ checked, onChange, large = false }: { checked: boolean; onChange: (on: boolean) => void; large?: boolean }) {
  return (
    <label className={large ? 'dc-check is-large' : 'dc-check'}>
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      Include remote in India
    </label>
  );
}

export interface SearchBarProps extends CityControlProps {
  remote: boolean;
  onRemote: (on: boolean) => void;
  /** The status of the last search: "Searched today at 7:31 pm · 8 roles", and a quieter line under it. */
  status: ReactNode;
  sub?: ReactNode;
  /** "Search with AI" before there are results, "Search again" after. */
  label: string;
  /** The service says no for now (paused, profile missing, limit reached): the button waits. */
  blocked: boolean;
  blockedWhy?: string;
  onSearch: () => void;
  /** The line "About 20 seconds". */
  hint: string;
}

export function SearchBar({ status, sub, label, blocked, blockedWhy, onSearch, hint, remote, onRemote, ...city }: SearchBarProps) {
  return (
    <section className="panel dc-bar" aria-label="Search">
      <div className="dc-bar-where">
        <CityControl {...city} />
        <RemoteToggle checked={remote} onChange={onRemote} />
      </div>
      <div className="dc-bar-text" role="status">
        <span>{status}</span>
        <span className="dc-muted dc-small">{[sub, hint].filter(Boolean).join(' · ')}</span>
      </div>
      <button type="button" className="btn btn-primary dc-btn dc-bar-go" aria-disabled={blocked || undefined} title={blocked ? blockedWhy : undefined} onClick={() => !blocked && onSearch()}>
        <IconSearch />
        {label}
      </button>
    </section>
  );
}
