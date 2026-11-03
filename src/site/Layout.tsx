import type { ReactNode } from 'react';
import { COMPANY_LINKS, OPERATOR_LINE, SITE_PAGES, SUPPORT_EMAIL } from './links';
import { SITE_LINE } from './seo';

interface LayoutProps {
  /** The path of the page being rendered; its navigation link is marked as the current page. Empty on the 404 page. */
  currentPath: string;
  children: ReactNode;
}

/** The Leafout mark: a hairline ring with a centre dot (the app's Brand, as static markup). */
function Mark() {
  return (
    <svg viewBox="0 0 20 20" width="20" height="20" aria-hidden="true" focusable="false">
      <circle className="mark-ring" cx="10" cy="10" r="7.5" />
      <circle className="mark-dot" cx="10" cy="10" r="1.25" />
    </svg>
  );
}

/**
 * The frame of every static page: the ambient field, a skip link, the header (the mark linking to Home and the
 * navigation), the page, and a footer that lists every page of the site. Static markup only: no store, no browser
 * APIs, no script.
 */
export function Layout({ currentPath, children }: LayoutProps) {
  const nav = SITE_PAGES.filter((p) => p.inNav);
  return (
    <>
      <div className="ambient" aria-hidden="true" />
      <div className="site">
        <a className="skip" href="#main">
          Skip to content
        </a>
        <header className="site-header">
          <a className="site-brand" href="/">
            <Mark />
            <span className="serif site-brand-word">Leafout</span>
          </a>
          <nav className="site-nav" aria-label="Main">
            <ul>
              {nav.map((p) => (
                <li key={p.path}>
                  <a href={p.path} aria-current={p.path === currentPath ? 'page' : undefined}>
                    {p.label}
                  </a>
                </li>
              ))}
            </ul>
          </nav>
        </header>
        <main id="main" className="site-main" tabIndex={-1}>
          {children}
        </main>
        <footer className="site-footer">
          <div className="site-footer-inner">
            <p>{SITE_LINE}</p>
            <nav aria-label="All pages">
              <ul>
                {SITE_PAGES.map((p) => (
                  <li key={p.path}>
                    <a href={p.path} aria-current={p.path === currentPath ? 'page' : undefined}>
                      {p.label}
                    </a>
                  </li>
                ))}
              </ul>
            </nav>
            <nav aria-label="Leafout, the company">
              <ul>
                {COMPANY_LINKS.map((l) => (
                  <li key={l.href}>
                    <a href={l.href}>{l.label}</a>
                  </li>
                ))}
                <li>
                  <a href={`mailto:${SUPPORT_EMAIL}`}>{SUPPORT_EMAIL}</a>
                </li>
              </ul>
            </nav>
            <p>{OPERATOR_LINE}</p>
          </div>
        </footer>
      </div>
    </>
  );
}
