import type { ReactElement } from 'react';

/** One question of a page's FAQ. `a` is plain text: the same words are shown on the page and put in the FAQPage data. */
export interface Faq {
  q: string;
  a: string;
}

/** The one primary link from a content page into the app. */
export interface Cta {
  label: string;
  href: string;
}

/**
 * A content page of the public site, rendered to static HTML at build time (scripts/build-pages.tsx).
 * Its `path` must be a `page` entry of SITE_PAGES (links.ts), and the other way round.
 */
export interface ContentPage {
  path: string;
  /** The words before " · Tayyari" in the document title. */
  title: string;
  /** The meta description and the share description: plain text, at most 160 characters. */
  description: string;
  h1: string;
  /** The opening paragraph under the h1. */
  lede: string;
  /** One short sentence about the page, shown on the "Related" cards of other pages. */
  blurb: string;
  cta: Cta;
  /** Three to five questions. */
  faq: readonly Faq[];
  /** Paths of three other content pages. */
  related: readonly string[];
  /** The sections between the lede and the FAQ: `<section>` elements that each open with an h2. */
  Body: () => ReactElement;
  /** Adds SoftwareApplication structured data (only the page that explains the product as a whole). */
  software?: boolean;
}
