/**
 * Prop contracts between the portfolio's parts (frozen at W0): `PortfolioView` (standard skin + blocks),
 * `SkillGraph` (graph variants), `PressView` (Old Newspaper skin) and `EditorView`. Data only flows in
 * through props; nothing here talks to the store or the network.
 */
import type { ReactNode } from 'react';
import type {
  GraphDomain,
  GraphSnapshot,
  GraphVariant,
  PortfolioContent,
  PortfolioSkin,
  PortfolioSpec,
} from '../../shared/portfolio';
import type { ViewMode } from './model';

/** Editing affordances the editor passes down; absent on the public page. */
export interface EditHooks {
  selectedBlockId?: string;
  onSelectBlock?(id: string): void;
  /** When given, the selected block renders this in place of a slot's text (a textarea with a counter). */
  renderSlotEditor?(slotId: string, text: string): ReactNode;
}

export interface GraphRenderArgs {
  spec: PortfolioSpec;
  graph: GraphSnapshot;
  /** The shown domains, in the snapshot's fixed order (hidden ones removed). */
  domains: GraphDomain[];
  variant: GraphVariant;
  content: PortfolioContent;
  /** `hero`: inside the split hero band; `section`: its own section after the hero. */
  placement: 'hero' | 'section';
}

export interface PressIndexArgs {
  domains: GraphDomain[];
  variant: GraphVariant;
  graph: GraphSnapshot;
}

export interface PressRenderArgs {
  spec: PortfolioSpec;
  content: PortfolioContent;
  graph: GraphSnapshot;
  mode: ViewMode;
  onToggleSkin?: () => void;
}

export interface PortfolioViewProps {
  spec: PortfolioSpec;
  content: PortfolioContent;
  graph: GraphSnapshot;
  mode: ViewMode;
  skin: PortfolioSkin;
  onToggleSkin?: () => void;
  editing?: EditHooks;
  renderGraph: (args: GraphRenderArgs) => ReactNode;
  renderPress: (args: PressRenderArgs) => ReactNode;
}
