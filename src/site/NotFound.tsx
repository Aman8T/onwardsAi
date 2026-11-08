import { SITE_PAGES } from './links';

/** The body of dist/404.html: it is served for any address that is not a page, so every link in it is absolute from the root. */
export function NotFound() {
  return (
    <div className="column">
      <header className="doc-head">
        <h1 className="serif">That page does not exist</h1>
        <p className="lede">The address may have a typo, or the page may have moved. These are the pages of the site.</p>
      </header>
      <div className="prose">
        <p>
          <a href="/">Back to Home</a>
        </p>
        <ul>
          {SITE_PAGES.map((p) => (
            <li key={p.path}>
              <a href={p.path}>{p.label}</a>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
