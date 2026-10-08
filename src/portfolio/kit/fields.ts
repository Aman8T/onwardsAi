/**
 * Field shapes (the page's gradient background) and the other style tokens that are plain classes.
 * The three lobe layouts (Dawn, Horizon, Corner) are CSS in src/styles/portfolio.css, built from
 * `var(--bg)` and `var(--lobe-1..3)` only, so they follow the person's gradient family.
 */
import type { FieldShape, PortfolioStyle } from '../../../shared/portfolio';

/** `pf-field-dawn` | `pf-field-horizon` | `pf-field-corner`. */
export function fieldClass(field: FieldShape): string {
  return `pf-field-${field}`;
}

/** Every style-driven class on the `.pf-scope` wrapper (field shape, type pairing, corners, density). */
export function scopeClass(style: PortfolioStyle): string {
  return [
    'pf-scope',
    fieldClass(style.field),
    `pf-type-${style.type}`,
    `pf-corners-${style.corners}`,
    `pf-density-${style.density}`,
  ].join(' ');
}
