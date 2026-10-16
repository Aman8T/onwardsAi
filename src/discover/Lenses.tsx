/**
 * The two lenses and the lanes. Each stop of a lens shows the count you would see there, so the person never has to
 * try a setting to learn what it does. "How far" is a straight line from the area they set and needs a locality;
 * without one every stop but "Whole city" is disabled and says why.
 */
import type { DiscoverBreadth, DiscoverLane, DiscoverReach, LensStop } from '../../shared/discover';

interface StopsProps {
  label: string;
  /** A short note beside the label ("straight line"). */
  note?: string;
  stops: readonly LensStop[];
  value: string;
  /** Stops that cannot be picked, by value. */
  disabled?: (stop: LensStop) => boolean;
  onPick: (value: string) => void;
  hint?: string;
}

function Stops({ label, note, stops, value, disabled, onPick, hint }: StopsProps) {
  return (
    <div role="group" aria-label={label} className="dc-lens">
      <div className="dc-lens-head">
        <span className="kicker">{label}</span>
        {note && <span className="dc-faint dc-small">{note}</span>}
      </div>
      <div className="dc-stops">
        {stops.map((s) => {
          const off = disabled?.(s) ?? false;
          return (
            <button key={s.value} type="button" className="dc-stop" aria-pressed={s.value === value} disabled={off} onClick={() => onPick(s.value)}>
              <span>{s.label}</span>
              <span className="numeral dc-stop-n">{off ? '–' : s.count}</span>
            </button>
          );
        })}
      </div>
      {hint && <p className="dc-faint dc-small dc-lens-hint">{hint}</p>}
    </div>
  );
}

export function ReachLens({ stops, value, hasLocality, onPick }: { stops: readonly LensStop[]; value: DiscoverReach; hasLocality: boolean; onPick: (reach: DiscoverReach) => void }) {
  return (
    <Stops
      label="How far"
      note="straight line"
      stops={stops}
      value={value}
      disabled={(s) => !hasLocality && s.value !== 'city'}
      hint={hasLocality ? undefined : 'Pick your area for distances'}
      onPick={(v) => onPick(v as DiscoverReach)}
    />
  );
}

export function BreadthLens({ stops, value, onPick }: { stops: readonly LensStop[]; value: DiscoverBreadth; onPick: (breadth: DiscoverBreadth) => void }) {
  return <Stops label="How wide" stops={stops} value={value} onPick={(v) => onPick(v as DiscoverBreadth)} />;
}

export interface LaneTabsProps {
  lane: DiscoverLane;
  counts: { near: number; remote: number; city: number };
  /** The city they would move to, if they chose one. */
  otherCity?: string;
  onPick: (lane: DiscoverLane) => void;
  /** "Worth moving for" while off: ask for a city. */
  onAddCity: () => void;
}

export function LaneTabs({ lane, counts, otherCity, onPick, onAddCity }: LaneTabsProps) {
  return (
    <div role="group" aria-label="Lanes" className="dc-lanes">
      <button type="button" className="btn dc-lane" aria-pressed={lane === 'near'} onClick={() => onPick('near')}>
        Near you · <span className="numeral">{counts.near}</span>
      </button>
      <button type="button" className="btn dc-lane" aria-pressed={lane === 'remote'} onClick={() => onPick('remote')}>
        Remote, India-eligible · <span className="numeral">{counts.remote}</span>
      </button>
      {otherCity ? (
        <button type="button" className="btn dc-lane" aria-pressed={lane === 'city'} onClick={() => onPick('city')}>
          Worth moving for · {otherCity} · <span className="numeral">{counts.city}</span>
        </button>
      ) : (
        <button type="button" className="btn dc-lane" aria-pressed={false} onClick={onAddCity}>
          Worth moving for · Off
        </button>
      )}
    </div>
  );
}
