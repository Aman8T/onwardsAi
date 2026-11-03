/**
 * The site footer: every public page of the site as a plain link, so visitors and search engines can move between
 * them. Each page is its own HTML file, so these are ordinary `<a href>` links (full page loads). The list is
 * SITE_PAGES without Home.
 */
import { SITE_PAGES } from '../site/links';
import '../styles/site-footer.css';

/** The legal and contact pages live on the site (leafout.in), so these are absolute links. */
const LEGAL_LINKS: readonly { href: string; label: string }[] = [
  { href: 'https://leafout.in/terms', label: 'Terms' },
  { href: 'https://leafout.in/privacy', label: 'Privacy' },
  { href: 'https://leafout.in/refunds', label: 'Refunds' },
  { href: 'https://leafout.in/contact', label: 'Contact' },
];

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
      <nav className="site-footer-links" aria-label="Terms and policies">
        {LEGAL_LINKS.map((link) => (
          <a key={link.href} href={link.href}>
            {link.label}
          </a>
        ))}
      </nav>
      <p className="site-footer-line">Leafout reads your resume, GitHub and public links and shows your skills as an evidence-cited graph.</p>
    </footer>
  );
}
