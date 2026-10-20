import { useScheme } from '../theme/ambient';
import { IconMoon, IconSun } from './Icons';

/**
 * One click between the light and the dark field. It flips whatever is in force now (the device's scheme under
 * System), and the choice is kept like the Atmosphere menu's Light / Dark; System stays one step away in that menu.
 * The icon shows where the click goes: a moon in the light field, a sun in the dark.
 */
export function SchemeToggle({ className }: { className?: string }) {
  const [, effective, setScheme] = useScheme();
  const next = effective === 'dark' ? 'light' : 'dark';
  const label = next === 'dark' ? 'Switch to dark mode' : 'Switch to light mode';
  return (
    <button
      type="button"
      className={className ? `btn btn-quiet scheme-toggle ${className}` : 'btn btn-quiet scheme-toggle'}
      aria-label={label}
      title={label}
      onClick={() => setScheme(next)}
    >
      {next === 'dark' ? <IconMoon size={18} /> : <IconSun size={18} />}
    </button>
  );
}
