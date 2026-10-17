/**
 * Tayyari design-system entry for Claude Design (/design-sync).
 *
 * Re-exports the components the app already ships, nothing reimplemented. Lives outside src/ so the app
 * bundle never sees it; esbuild bundles it (with the stylesheets below) into ds-bundle/_ds_bundle.{js,css}.
 * The `*Props` aliases exist only so the converter can emit a typed contract for components whose props
 * are not named `<Name>Props` in source (icons, portfolio blocks, PortfolioPage, Atmosphere).
 */
import type { ReactNode, SVGProps } from 'react';
import type { PortfolioViewProps } from '../src/portfolio/types';
import type { BlockProps, PfCtx } from '../src/portfolio/blocks/SectionShell';

import './ds-fonts.css';
import '../src/theme/tokens.css';
import '../src/styles/global.css';

/* ── Charts and the shared kit (src/components) ─────────────────────────── */
export { Radar, RadarGlyph } from '../src/components/Radar';
export type { RadarAxis, RadarHandle, RadarProps, VertexGeom } from '../src/components/Radar';
export { Ring } from '../src/components/Ring';
export type { RingProps } from '../src/components/Ring';
export { ValueLine } from '../src/components/ValueLine';
export type { ValueLineProps } from '../src/components/ValueLine';
export { Popover } from '../src/components/Popover';
export type { PopoverBullet, PopoverModel, PopoverProps, PopoverSkill } from '../src/components/Popover';
export { Brand } from '../src/components/Brand';
export { Atmosphere } from '../src/components/Atmosphere';
export {
  IconArrowRight,
  IconCheck,
  IconDoc,
  IconMinus,
  IconPlus,
  IconRefresh,
  IconStop,
  IconUpload,
} from '../src/components/Icons';

export interface RadarGlyphProps {
  /** 0–100 per axis, in the axis order of the full chart. */
  values: number[];
  /** Square size in px, default 20. */
  size?: number;
  className?: string;
}
export interface BrandProps {
  /** When given the brand renders as a button (home link); otherwise as plain markup. */
  onClick?: () => void;
  /** Adds the "Career intelligence" kicker beside the wordmark. */
  tagline?: boolean;
  /** Scale reference: the default 28 gives a 22px wordmark and a 20px mark. */
  size?: number;
}
export interface AtmosphereProps {
  className?: string;
}
/** Every stroke icon: a 24-box SVG drawn with currentColor; `size` sets width and height (default 18). */
export type IconProps = SVGProps<SVGSVGElement> & { size?: number };
export type IconArrowRightProps = IconProps;
export type IconCheckProps = IconProps;
export type IconDocProps = IconProps;
export type IconMinusProps = IconProps;
export type IconPlusProps = IconProps;
export type IconRefreshProps = IconProps;
export type IconStopProps = IconProps;
export type IconUploadProps = IconProps;

/* ── Resume → Portfolio (src/portfolio) ──────────────────────────────────── */
export { PortfolioPage } from '../src/portfolio/PublicView';
export { About } from '../src/portfolio/blocks/About';
export { Achievements } from '../src/portfolio/blocks/Achievements';
export { Clips } from '../src/portfolio/blocks/Clips';
export { Contact } from '../src/portfolio/blocks/Contact';
export { Education } from '../src/portfolio/blocks/Education';
export { Experience } from '../src/portfolio/blocks/Experience';
export { Footer } from '../src/portfolio/blocks/Footer';
export { Hero } from '../src/portfolio/blocks/Hero';
export { OpenTo } from '../src/portfolio/blocks/OpenTo';
export { Projects } from '../src/portfolio/blocks/Projects';
export { Proof } from '../src/portfolio/blocks/Proof';
export { PfProvider, usePf } from '../src/portfolio/blocks/SectionShell';
export type { BlockProps, PfCtx } from '../src/portfolio/blocks/SectionShell';
export { graphVariant, shownBlocks, shownDomains, styleVars } from '../src/portfolio/model';
export type { ViewMode } from '../src/portfolio/model';
export { scopeClass } from '../src/portfolio/kit/fields';
export { typeVars } from '../src/portfolio/kit/type';
export { RIYA as sampleRiya } from '../src/portfolio/dev/samples';

/** PortfolioPage: the whole portfolio from its spec, content and graph snapshot, in either skin. */
export type PortfolioPageProps = Omit<PortfolioViewProps, 'renderGraph' | 'renderPress'>;
export type PfProviderProps = { value: PfCtx; children?: ReactNode };
export type AboutProps = BlockProps;
export type AchievementsProps = BlockProps;
export type ClipsProps = BlockProps;
export type ContactProps = BlockProps;
export type EducationProps = BlockProps;
export type ExperienceProps = BlockProps;
/** Footer takes no props: it reads the graph snapshot from the portfolio context. */
export type FooterProps = Record<never, never>;
export type HeroProps = BlockProps;
export type OpenToProps = BlockProps;
export type ProjectsProps = BlockProps;
export type ProofProps = BlockProps;

/* ── Shared helpers the components are built with ────────────────────────── */
export { EASE, NARROW_QUERY, clamp, cssVars, useMediaQuery } from '../src/lib/ui';
