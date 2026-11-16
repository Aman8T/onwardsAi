/**
 * The page's own style control: one button per skin, the current one pressed. Every skin renders it in its own
 * chrome (`.pf-skins` is styled by portfolio.css, press.css and coast.css), so a viewer can move between the
 * three looks from any of them. `SKINS` is the one list of skins and their product names; the editor, the public
 * page (`?view=`) and the dev gallery read it too.
 */
import type { PortfolioSkin } from '../../shared/portfolio';
import type { PickSkin } from './types';

export const SKINS: readonly { skin: PortfolioSkin; label: string }[] = [
  { skin: 'standard', label: 'Modern' },
  { skin: 'press', label: 'Old Newspaper' },
  { skin: 'coast', label: 'Neon Coast' },
];

/** The skin a string names (`?view=coast`, a stored default), or `null` when it names none. */
export function skinOf(value: string | null | undefined): PortfolioSkin | null {
  return SKINS.find((s) => s.skin === value)?.skin ?? null;
}

export function skinLabel(skin: PortfolioSkin): string {
  return SKINS.find((s) => s.skin === skin)?.label ?? skin;
}

export function SkinPicker({ skin, onPick }: { skin: PortfolioSkin; onPick: PickSkin }) {
  return (
    <div className="pf-skins" role="group" aria-label="Page style">
      {SKINS.map((s) => (
        <button key={s.skin} type="button" className="pf-skin" aria-pressed={s.skin === skin} onClick={() => onPick(s.skin)}>
          {s.label}
        </button>
      ))}
    </div>
  );
}
