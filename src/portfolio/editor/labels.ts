/**
 * Editor labels and rules (pure, no React, no CSS): human names for the kit's blocks, variants and style
 * choices, the "nothing to show yet" test per block, and the move rules for the Sections list. The
 * rules mirror `spec_problems` in the contract: a hero first, the Skill Graph within the first three.
 */
import { BLOCK_VARIANTS } from '../../../shared/portfolio';
import type { BlockKind, BlockSpec, Corners, Density, PortfolioContent, TypePairing } from '../../../shared/portfolio';
import { factsOf, slotText } from '../model';

/** The graph block must sit at index 2 or earlier (contract: GRAPH_MAX_INDEX). */
export const GRAPH_MAX_INDEX = 2;

/** At least this many domains always stay on the graph (or all of them when there are fewer). */
export const minDomains = (n: number): number => Math.min(3, n);

export const capitalise = (s: string): string => (s ? s[0].toUpperCase() + s.slice(1) : s);

export const BLOCK_NAMES: Record<BlockKind, string> = {
  hero: 'Hero',
  graph: 'Skill Graph',
  proof: 'Proof',
  about: 'About',
  experience: 'Experience',
  projects: 'Selected work',
  clips: 'Writing and press',
  achievements: 'Recognition',
  education: 'Education',
  openTo: 'Open to',
  contact: 'Contact',
};

const VARIANT_NAMES: Record<string, string> = {
  featureList: 'Feature + list',
  thenNow: 'Then and now',
  signOff: 'Sign-off',
};

/** "featureList" -> "Feature + list", "peaks" -> "Peaks". */
export const variantName = (variant: string): string => VARIANT_NAMES[variant] ?? capitalise(variant);

/** The variants a block kind offers, always including the current one. */
export function variantsFor(kind: BlockKind, current: string): readonly string[] {
  const all: readonly string[] = BLOCK_VARIANTS[kind];
  return all.includes(current) ? all : [...all, current];
}

export const TYPE_OPTIONS: readonly TypePairing[] = ['studio', 'editorial', 'technical', 'warm'];
export const CORNER_OPTIONS: readonly Corners[] = ['square', 'soft', 'round'];
export const DENSITY_OPTIONS: readonly Density[] = ['airy', 'compact'];

/** Why a block has nothing to show yet (the editor prompts instead of offering the eye), or undefined. */
export function emptyHint(kind: BlockKind, content: PortfolioContent): string | undefined {
  const none = (n: number) => n === 0;
  switch (kind) {
    case 'proof':
      return content.stats.length < 2 ? 'Needs 2 or more numbers' : undefined;
    case 'about':
      return none(slotText(content, 'aboutLede').trim().length + slotText(content, 'aboutBody').trim().length) ? 'No text yet' : undefined;
    case 'experience':
      return none(factsOf(content, 'experience').length) ? 'No roles found' : undefined;
    case 'projects':
      return none(factsOf(content, 'project').length) ? 'No projects found' : undefined;
    case 'clips':
      return none(factsOf(content, 'clip').length) ? 'No clips found' : undefined;
    case 'achievements':
      return none(factsOf(content, 'achievement').length) ? 'No recognition found' : undefined;
    case 'education':
      return none(factsOf(content, 'education').length) ? 'No education found' : undefined;
    case 'openTo':
      return none(content.openTo.length) ? 'No roles to list' : undefined;
    default:
      return undefined;
  }
}

export type MoveResult = { ok: true; blocks: BlockSpec[] } | { ok: false; why: string };

/** Swap the block at `index` with its neighbour, unless that breaks the page rules. */
export function tryMove(blocks: BlockSpec[], index: number, dir: -1 | 1): MoveResult {
  const j = index + dir;
  if (index < 0 || index >= blocks.length) return { ok: false, why: '' };
  if (j < 0) return { ok: false, why: 'Already first' };
  if (j >= blocks.length) return { ok: false, why: 'Already last' };
  const next = blocks.slice();
  [next[index], next[j]] = [next[j], next[index]];
  if (next[0]?.kind !== 'hero') return { ok: false, why: 'The hero always comes first' };
  const graphAt = next.findIndex((b) => b.kind === 'graph');
  if (graphAt > GRAPH_MAX_INDEX) return { ok: false, why: 'The Skill Graph stays within the first three sections' };
  return { ok: true, blocks: next };
}

/** True when the text looks like markup; owner text is plain text only. */
export const looksLikeMarkup = (s: string): boolean => /<\/?[a-z!][^>]*>/i.test(s);
