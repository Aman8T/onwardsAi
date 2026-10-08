/**
 * Old Newspaper faces: UnifrakturMaguntia (nameplate), Playfair Display (headlines) and Libre Caslon Text
 * (body). One Google Fonts <link>, appended once, only when the newspaper skin is shown.
 */
const HREF =
  'https://fonts.googleapis.com/css2?family=UnifrakturMaguntia&family=Playfair+Display:ital,wght@0,700;0,900;1,400' +
  '&family=Libre+Caslon+Text:ital,wght@0,400;0,700;1,400&display=swap';

export function ensurePressFonts(): void {
  if (typeof document === 'undefined') return;
  const sheets = Array.from(document.querySelectorAll<HTMLLinkElement>('link[rel="stylesheet"]'));
  if (sheets.some((l) => l.hasAttribute('data-press-fonts') || l.href.includes('UnifrakturMaguntia'))) return;
  const link = document.createElement('link');
  link.rel = 'stylesheet';
  link.href = HREF;
  link.setAttribute('data-press-fonts', '');
  document.head.appendChild(link);
}
