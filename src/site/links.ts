/**
 * The public pages of the site: one list, shared by the generated pages (scripts/build-pages.tsx), the app's own footer
 * and the sitemap. Each `path` is a real URL served as its own HTML file (search engines read it without running the
 * app); the app's own screens (/match, /discover, /practice, the graph) stay single-page routes.
 *
 * `kind`:
 *   - `page`: a content page generated at build time into dist/<slug>/index.html
 *   - `app`:  a route of the app whose HTML shell gets its own title, description and share tags at build time
 */

export interface SitePage {
  path: string;
  /** Short label for navigation and footers. */
  label: string;
  kind: 'page' | 'app';
  /** Shown in the header navigation (the footer lists every page). */
  inNav: boolean;
}

export const SITE_PAGES: readonly SitePage[] = [
  { path: '/', label: 'Home', kind: 'app', inNav: false },
  { path: '/how-it-works', label: 'How it works', kind: 'page', inNav: true },
  { path: '/skill-graph', label: 'Skill graph', kind: 'page', inNav: true },
  { path: '/job-match', label: 'Job match', kind: 'page', inNav: true },
  { path: '/mock-interview', label: 'Mock interview', kind: 'page', inNav: true },
  { path: '/job-search', label: 'Job search', kind: 'page', inNav: true },
  { path: '/career-plan', label: 'Career plan', kind: 'page', inNav: false },
  { path: '/resume-to-portfolio', label: 'Resume to portfolio', kind: 'page', inNav: false },
  { path: '/pricing', label: 'Pricing', kind: 'app', inNav: true },
  { path: '/about', label: 'About', kind: 'page', inNav: false },
];

/** The address the canonical links, the sitemap and share tags use (overridable at build time with SITE_URL). */
export const DEFAULT_SITE_URL = 'https://tayyari.fastapicloud.dev';
