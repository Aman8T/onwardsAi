/**
 * Search and share metadata for the public site: the site-wide constants, the heads of the two app routes that get
 * their own tags (Home and Pricing), and the structured-data builders. Pure functions, no browser or Node APIs, so
 * scripts/build-pages.tsx and scripts/build-check.ts can both use them.
 */
import { DEFAULT_SITE_URL, SITE_PAGES } from './links';
import type { ContentPage } from './types';

export const SITE_NAME = 'Leafout';

/** The line that closes every page's footer and describes the site in structured data. */
export const SITE_LINE = 'Leafout reads your resume, GitHub and public links and shows your skills as an evidence-cited graph.';

/** Search results cut a description near this length; a longer one is rewritten by the engine. */
export const MAX_DESCRIPTION = 160;

export interface AppHead {
  /** The whole document title. */
  title: string;
  description: string;
}

export const HOME_HEAD: AppHead = {
  title: 'Leafout · Evidence-cited skill graph for your career',
  description:
    'Leafout reads your resume, GitHub and public links and shows your skills as a graph where every skill cites its source. Then match postings and practise.',
};

export const PRICING_HEAD: AppHead = {
  title: 'Pricing: pay per posting or by the month · Leafout',
  description:
    'Your skill graph, next roles and portfolio page are free, with one posting match a month. Pay per posting or by the month. No annual lock-in.',
};

/** The title of a content page's document. */
export function documentTitle(page: Pick<ContentPage, 'title'>): string {
  return `${page.title} · ${SITE_NAME}`;
}

/** The site's address without a trailing slash: `SITE_URL` from the environment when it is a http(s) address, else the default. */
export function resolveSiteUrl(fromEnv: string | undefined): string {
  const value = (fromEnv ?? '').trim();
  const base = /^https?:\/\/[^\s/]+/i.test(value) ? value : DEFAULT_SITE_URL;
  return base.replace(/\/+$/, '');
}

/** `/` is the site root with its slash; any other path is appended to the base as it is. */
export function absoluteUrl(base: string, path: string): string {
  return path === '/' ? `${base}/` : `${base}${path}`;
}

/** The label of a site page (navigation and breadcrumbs). */
export function labelOf(path: string): string {
  return SITE_PAGES.find((p) => p.path === path)?.label ?? path;
}

type Ld = Record<string, unknown>;

const orgId = (base: string) => `${base}/#organization`;

export function organizationLd(base: string): Ld {
  return { '@type': 'Organization', '@id': orgId(base), name: SITE_NAME, url: `${base}/`, description: SITE_LINE };
}

export function websiteLd(base: string): Ld {
  return {
    '@type': 'WebSite',
    '@id': `${base}/#website`,
    name: SITE_NAME,
    url: `${base}/`,
    description: SITE_LINE,
    inLanguage: 'en',
    publisher: { '@id': orgId(base) },
  };
}

/** Home, then the page. */
export function breadcrumbLd(base: string, path: string): Ld {
  return {
    '@type': 'BreadcrumbList',
    itemListElement: [
      { '@type': 'ListItem', position: 1, name: labelOf('/'), item: absoluteUrl(base, '/') },
      { '@type': 'ListItem', position: 2, name: labelOf(path), item: absoluteUrl(base, path) },
    ],
  };
}

export function faqLd(faq: ContentPage['faq']): Ld {
  return {
    '@type': 'FAQPage',
    mainEntity: faq.map((f) => ({ '@type': 'Question', name: f.q, acceptedAnswer: { '@type': 'Answer', text: f.a } })),
  };
}

/** No `offers`: a price is not stated in structured data until there is one to state. */
export function softwareLd(base: string): Ld {
  return {
    '@type': 'SoftwareApplication',
    '@id': `${base}/#app`,
    name: SITE_NAME,
    url: `${base}/`,
    description: SITE_LINE,
    applicationCategory: 'BusinessApplication',
    operatingSystem: 'Any (web browser)',
    publisher: { '@id': orgId(base) },
  };
}

/** The structured data of a content page: the site's Organization and WebSite, its breadcrumb, its FAQ, and the product on the page that explains it. */
export function pageLd(base: string, page: ContentPage): Ld[] {
  const graph = [organizationLd(base), websiteLd(base), breadcrumbLd(base, page.path), faqLd(page.faq)];
  if (page.software) graph.push(softwareLd(base));
  return graph;
}
