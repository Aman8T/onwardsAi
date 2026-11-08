import { CONTENT_PAGES } from './pages';
import { labelOf, longDate } from './seo';
import type { ContentPage } from './types';

/** The content page at `path`, or undefined. */
export function pageAt(path: string): ContentPage | undefined {
  return CONTENT_PAGES.find((p) => p.path === path);
}

/**
 * A content page: breadcrumb, h1 and lede with the one call to action, the page's sections, its questions, and three
 * related pages. Rendered inside `Layout`.
 */
export function Article({ page, updated }: { page: ContentPage; /** The day the page's text last changed (ISO), when the build could find it. */ updated?: string }) {
  const related = page.related.map(pageAt).filter((p): p is ContentPage => p !== undefined);
  const Body = page.Body;
  return (
    <div className="column">
      <nav className="crumbs" aria-label="Breadcrumb">
        <ol>
          <li>
            <a href="/">{labelOf('/')}</a>
          </li>
          <li>
            <span aria-current="page">{labelOf(page.path)}</span>
          </li>
        </ol>
      </nav>
      <article>
        <header className="doc-head">
          <h1 className="serif">{page.h1}</h1>
          <p className="lede">{page.lede}</p>
          <p className="doc-cta">
            <a className="btn btn-primary" href={page.cta.href}>
              {page.cta.label}
            </a>
          </p>
          {updated && (
            <p className="doc-updated">
              Updated <time dateTime={updated}>{longDate(updated)}</time>
            </p>
          )}
        </header>
        <div className="prose">
          <Body />
          <section aria-labelledby="faq-title">
            <h2 id="faq-title">Questions</h2>
            {page.faq.map((f, i) => (
              <div className="faq-item" role="group" aria-labelledby={`faq-q-${i + 1}`} key={f.q}>
                <h3 id={`faq-q-${i + 1}`}>{f.q}</h3>
                <p>{f.a}</p>
              </div>
            ))}
          </section>
        </div>
      </article>
      <section className="related" aria-labelledby="related-title">
        <h2 id="related-title">Related</h2>
        <ul className="related-grid">
          {related.map((r) => (
            <li key={r.path}>
              <a className="panel related-card" href={r.path}>
                <strong>{labelOf(r.path)}</strong>
                <span>{r.blurb}</span>
              </a>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
