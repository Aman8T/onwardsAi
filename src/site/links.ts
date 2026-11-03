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
export const DEFAULT_SITE_URL = 'https://app.leafout.in';

/** The company site, the one home of the legal pages and the blog. Absolute: these are another host. */
export const COMPANY_URL = 'https://leafout.in';

/** Support address and the operator line, shown in every footer (the site's /contact page carries the same). */
export const SUPPORT_EMAIL = 'aman@leafout.in';
export const OPERATOR_LINE = 'Leafout is run by Aman Sharma, India.';

export interface CompanyLink {
  label: string;
  href: string;
}

/** Links from every page to the company site: the legal pages, the blog and the contact page live there, not in the app. */
export const COMPANY_LINKS: readonly CompanyLink[] = [
  { label: 'Leafout website', href: `${COMPANY_URL}/` },
  { label: 'Blog', href: `${COMPANY_URL}/blog` },
  { label: 'Privacy', href: `${COMPANY_URL}/privacy` },
  { label: 'Terms', href: `${COMPANY_URL}/terms` },
  { label: 'Refunds', href: `${COMPANY_URL}/refunds` },
  { label: 'Contact', href: `${COMPANY_URL}/contact` },
];
