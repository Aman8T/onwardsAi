/**
 * Search and share metadata for the public site: the site-wide constants, the heads of the two app routes that get
 * their own tags (Home and Pricing), and the structured-data builders. Pure functions, no browser or Node APIs, so
 * scripts/build-pages.tsx and scripts/build-check.ts can both use them.
 */
import { COMPANY_URL, DEFAULT_SITE_URL, SITE_PAGES, SUPPORT_EMAIL } from './links';
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
    'Your skill graph and next roles are free, with one posting match a month. Pay per posting or for 30 days, once. Nothing renews, and there is no annual plan.',
};

/** The share picture: drawn once, served from the app's own files (`public/og-default.png`), the same for every page. */
export const OG_IMAGE = {
  path: '/og-default.png',
  width: 1200,
  height: 630,
  alt: 'Leafout: Know where you stand. See where you can go.',
} as const;

/** The logo of the Organization (a file of the company site, which owns the brand). */
export const LOGO_URL = `${COMPANY_URL}/icon-512.png`;

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

/** One Organization for both properties: the company site owns it, and every page here points at the same id. */
export const ORG_ID = `${COMPANY_URL}/#organization`;

export function organizationLd(): Ld {
  return { '@type': 'Organization', '@id': ORG_ID, name: SITE_NAME, url: COMPANY_URL, logo: LOGO_URL, email: SUPPORT_EMAIL };
}

export function websiteLd(base: string): Ld {
  return {
    '@type': 'WebSite',
    '@id': `${base}/#website`,
    name: SITE_NAME,
    url: `${base}/`,
    description: SITE_LINE,
    inLanguage: 'en',
    publisher: { '@id': ORG_ID },
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

/** No `offers`: the prices are settings of the server, so the static data does not state one. */
export function softwareLd(base: string): Ld {
  return {
    '@type': 'SoftwareApplication',
    '@id': `${base}/#app`,
    name: SITE_NAME,
    url: `${base}/`,
    description: SITE_LINE,
    applicationCategory: 'BusinessApplication',
    operatingSystem: 'Any (web browser)',
    publisher: { '@id': ORG_ID },
  };
}

/** The page itself, with the day its text last changed when that is known. */
export function webPageLd(base: string, path: string, name: string, modified: string | undefined): Ld {
  return {
    '@type': 'WebPage',
    '@id': `${absoluteUrl(base, path)}#webpage`,
    url: absoluteUrl(base, path),
    name,
    inLanguage: 'en-IN',
    isPartOf: { '@id': `${base}/#website` },
    ...(modified ? { dateModified: modified } : {}),
  };
}

/** The structured data of a content page: the company's Organization and this host's WebSite, the page, its breadcrumb, its FAQ, and the product on the page that explains it. */
export function pageLd(base: string, page: ContentPage, modified?: string): Ld[] {
  const graph = [
    organizationLd(),
    websiteLd(base),
    webPageLd(base, page.path, documentTitle(page), modified),
    breadcrumbLd(base, page.path),
    faqLd(page.faq),
  ];
  if (page.software) graph.push(softwareLd(base));
  return graph;
}

const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];

/** `2026-11-03` as `3 November 2026`; the text unchanged when it is not a date. */
export function longDate(iso: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
  const month = m ? MONTHS[Number(m[2]) - 1] : undefined;
  return m && month ? `${Number(m[3])} ${month} ${m[1]}` : iso;
}
