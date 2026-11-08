/**
 * The site footer: every public page of the site as a plain link, so visitors and search engines can move between
 * them. Each page is its own HTML file, so these are ordinary `<a href>` links (full page loads). The list is
 * SITE_PAGES without Home (this footer is shown on Home).
 *
 * It says the same things as the static pages' footer (src/site/Layout.tsx) from the same sources: the company links
 * (website, blog, privacy, terms, refunds, contact) and the support address from links.ts, the site line from seo.ts.
 */
import { COMPANY_LINKS, OPERATOR_LINE, SITE_PAGES, SUPPORT_EMAIL } from '../site/links';
import { SITE_LINE } from '../site/seo';
import '../styles/site-footer.css';

export function SiteFooter() {
  const pages = SITE_PAGES.filter((page) => page.path !== '/');

  return (
    <footer className="site-footer">
      <nav className="site-footer-links" aria-label="Site">
        {pages.map((page) => (
          <a key={page.path} href={page.path}>
            {page.label}
          </a>
        ))}
      </nav>
      <nav className="site-footer-links" aria-label="Leafout, the company">
        {COMPANY_LINKS.map((link) => (
          <a key={link.href} href={link.href}>
            {link.label}
          </a>
        ))}
        <a href={`mailto:${SUPPORT_EMAIL}`}>{SUPPORT_EMAIL}</a>
      </nav>
      <p className="site-footer-line">{SITE_LINE}</p>
      <p className="site-footer-line">{OPERATOR_LINE}</p>
    </footer>
  );
}
