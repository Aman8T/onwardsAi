/**
 * The site footer: every public page of the site as a plain link, so visitors and search engines can move between
 * them. Each page is its own HTML file, so these are ordinary `<a href>` links (full page loads). The list is
 * SITE_PAGES without Home.
 */
import { SITE_PAGES } from '../site/links';
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
      <p className="site-footer-line">Leafout reads your resume, GitHub and public links and shows your skills as an evidence-cited graph.</p>
    </footer>
  );
}
